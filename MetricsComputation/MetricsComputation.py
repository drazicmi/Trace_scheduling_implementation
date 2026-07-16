
class MetricsComputer:
    # Computes quality and cost metrics for trace scheduling optimization
    # These metrics help evaluate the trade-off between optimization benefit and bookkeeping cost.

    # Initialize the metrics computer with the original (pre-bookkeeping) CFG data
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    # Get all blocks belonging to a specific trace, in trace order.
    def _trace_blocks(self, trace_id):
        return [b for b in self.blocks if getattr(b, "trace_id", None) == trace_id]

    # Get all edges whose src is inside the given set of block ids.
    def _outgoing(self, edges, block_id):
        return [e for e in edges if e.src == block_id]

    # Count "real" instructions in a single block (skip pure structural markers, which do not represent actual scheduled work).
    STRUCTURAL_MARKERS = ("ENTRY", "EXIT", "JOIN", "AFTER_LOOP", "AFTER_FOR")

    def _real_instruction_count(self, block):
        statements = getattr(block, "statements", None) or getattr(block, "instructions", [])
        return sum(1 for s in statements if s not in self.STRUCTURAL_MARKERS)

    # Cycle counts are read directly from the schedule results: "makespan" already accounts for instruction latency (baseline: 1 unit/cycle, sequential; optimized: list-scheduled across num_functional_units).
    def compute_cycle_counts(self, baseline_result, schedule_result):
        return {
            "baseline_cycles": baseline_result.get("makespan", 0),
            "optimized_cycles": schedule_result.get("makespan", 0),
        }

    # Critical path reduced if optimized makespan is strictly less than the baseline makespan.
    def critical_path_reduced(self, baseline_result, schedule_result):
        return schedule_result.get("makespan", 0) < baseline_result.get("makespan", 0)

    # Compute the probability of staying on the trace at each in-trace branch point, and return the product
    # (the probability the whole trace executes end-to-end as scheduled).
    def compute_trace_path_weight(self, trace_id=0):
        trace_blocks = self._trace_blocks(trace_id)
        trace_block_ids = {b.id for b in trace_blocks}
        trace_edges = [e for e in self.edges if getattr(e, "is_trace_edge", False)]
        trace_edge_by_src = {e.src: e for e in trace_edges}

        weight = 1.0
        for block in trace_blocks:
            edge = trace_edge_by_src.get(block.id)
            if edge is None:
                continue
            outgoing = self._outgoing(self.edges, block.id)
            if len(outgoing) <= 1:
                continue  # not a real branch point, weight factor is 1
            prob = edge.probability if edge.probability is not None else 1.0
            weight *= prob

        return weight

    # Build the list of (weight, scheduled_length) pairs for every relevant path: the main trace path plus one path per side exit.
    # `schedule_result` supplies per-instruction schedule_cycle data; `bookkeeping_result` supplies split-compensation instruction counts per exit
    # so side-exit paths can include their bookkeeping cost. If bookkeeping_result is None, side paths are approximated using only the portion of the trace executed before the exit (no compensation added)
    # this happens for the baseline wsl, since bookkeeping/compensation only exists for the optimized schedule.
    def _relevant_paths(self, schedule_result, trace_id=0, bookkeeping_result=None):
        trace_blocks = self._trace_blocks(trace_id)
        trace_block_ids = [b.id for b in trace_blocks]
        if not trace_block_ids:
            return []

        scheduled = schedule_result.get("scheduled_instructions", [])

        # Last scheduled cycle at which each block's own instructions run, used to know "how far" execution got before leaving at a given side exit.
        block_last_cycle = {}
        for item in scheduled:
            bid = item.get("block_id")
            cycle = item.get("schedule_cycle", 0)
            latency = item.get("latency", 1)
            finish = cycle + latency
            block_last_cycle[bid] = max(block_last_cycle.get(bid, 0), finish)

        makespan = schedule_result.get("makespan", 0)

        paths = []

        # 1) Main trace path: weight = probability of staying on-trace the whole way through, length = full schedule makespan.
        trace_weight = self.compute_trace_path_weight(trace_id)
        paths.append({
            "kind": "trace",
            "label": "main trace",
            "weight": trace_weight,
            "length": makespan,
        })

        # 2) One path per side exit: weight = probability of taking that specific off-trace branch, length = cycles executed up to that exit point,
        # plus any split-compensation instructions that would replay on that path (each counted as 1 cycle of simple straight-line code).
        side_exits = [e for e in self.edges if getattr(e, "is_side_exit", False)]

        comp_count_by_exit = {}
        if bookkeeping_result is not None:
            # Match compensation blocks back to their originating exit via the compensation edge label / src, since compensation edges are created 1:1 per side exit that needed one.
            comp_edges = bookkeeping_result.get("added_compensation_edges", [])
            comp_blocks = {b.id: b for b in bookkeeping_result.get("added_compensation_blocks", [])}
            for edge in comp_edges:
                label = getattr(edge, "label", "")
                if label.startswith("split_comp") and edge.dst in comp_blocks:
                    comp_count_by_exit[edge.src] = len(comp_blocks[edge.dst].statements)

        for exit_edge in side_exits:
            if exit_edge.src not in trace_block_ids:
                continue
            weight = exit_edge.probability if exit_edge.probability is not None else 0.0
            if weight <= 0:
                continue  # never taken in profiling; not a relevant path

            length = block_last_cycle.get(exit_edge.src, 0)
            length += comp_count_by_exit.get(exit_edge.src, 0)

            paths.append({
                "kind": "side_exit",
                "label": f"side exit B{exit_edge.src}->B{exit_edge.dst}",
                "weight": weight,
                "length": length,
            })

        return paths

    # Weighted schedule length: W(S) = sum_j( Wj * |Sj| )
    def compute_wsl(self, schedule_result, trace_id=0, bookkeeping_result=None):
        paths = self._relevant_paths(schedule_result, trace_id, bookkeeping_result)
        wsl = sum(p["weight"] * p["length"] for p in paths)
        return wsl, paths

    # Total instruction count across all blocks in a CFG (real instructions only, structural markers excluded)
    # Used for both the pre-optimization and post-optimization block lists to compute code size increase.
    def compute_total_instruction_count(self, blocks):
        return sum(self._real_instruction_count(b) for b in blocks)

    # Code size increase = optimized total instructions - original total instructions. Since scheduling only reorders/hoists existing instructions (never deletes or duplicates them on the fast path)
    # any increase comes entirely from added bookkeeping/compensation blocks.
    def compute_code_size_increase(self, optimized_blocks):
        original_total = self.compute_total_instruction_count(self.blocks)
        optimized_total = self.compute_total_instruction_count(optimized_blocks)
        return {
            "original_instruction_count": original_total,
            "optimized_instruction_count": optimized_total,
            "code_size_increase": optimized_total - original_total,
        }

    # Bookkeeping cost: how many compensation blocks/instructions were added.
    def compute_bookkeeping_cost(self, bookkeeping_result):
        added_blocks = bookkeeping_result.get("added_compensation_blocks", [])
        return {
            "added_bookkeeping_blocks": len(added_blocks),
            "added_bookkeeping_instructions": bookkeeping_result.get("total_compensation_instructions", 0),
            "split_compensation_count": bookkeeping_result.get("split_compensation_count", 0),
            "join_compensation_count": bookkeeping_result.get("join_compensation_count", 0),
        }

    # Compute the full formal metrics report used by main.py's output phase. Returns a plain dict of primitive values (no CFG objects), ready to be formatted for console output.
    def compute_formal_report(self, baseline_result, schedule_result, bookkeeping_result, trace_id=0):
        cycles = self.compute_cycle_counts(baseline_result, schedule_result)
        critical_path_reduced = self.critical_path_reduced(baseline_result, schedule_result)

        baseline_wsl, baseline_paths = self.compute_wsl(baseline_result, trace_id, bookkeeping_result=None)
        optimized_wsl, optimized_paths = self.compute_wsl(schedule_result, trace_id, bookkeeping_result=bookkeeping_result)

        size = self.compute_code_size_increase(bookkeeping_result.get("blocks", []))
        cost = self.compute_bookkeeping_cost(bookkeeping_result)

        return {
            "trace_id": trace_id,
            "quality": {
                "baseline_cycles": cycles["baseline_cycles"],
                "optimized_cycles": cycles["optimized_cycles"],
                "cycles_reduced": cycles["baseline_cycles"] - cycles["optimized_cycles"],
                "critical_path_reduced": critical_path_reduced,
                "baseline_wsl": baseline_wsl,
                "optimized_wsl": optimized_wsl,
                "wsl_reduced": optimized_wsl < baseline_wsl,
                "baseline_paths": baseline_paths,
                "optimized_paths": optimized_paths,
            },
            "cost": {
                "original_instruction_count": size["original_instruction_count"],
                "optimized_instruction_count": size["optimized_instruction_count"],
                "code_size_increase": size["code_size_increase"],
                "added_bookkeeping_blocks": cost["added_bookkeeping_blocks"],
                "added_bookkeeping_instructions": cost["added_bookkeeping_instructions"],
                "split_compensation_count": cost["split_compensation_count"],
                "join_compensation_count": cost["join_compensation_count"],
            },
        }