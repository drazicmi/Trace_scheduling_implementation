import copy


# This class implements bookkeeping for trace scheduling.
class Bookkeeper:
    # Initialize bookkeeper with CFG data and optional factories for creating blocks/edges
    def __init__(self, blocks, edges, block_factory=None, edge_factory=None):
        self.blocks = blocks
        self.edges = edges
        self.block_factory = block_factory  # Optional factory to create Block objects
        self.edge_factory = edge_factory    # Optional factory to create Edge objects

    # Get set of block IDs belonging to a trace
    def get_trace_block_ids(self, trace_id=0):
        return {block.id for block in self.blocks if block.trace_id == trace_id}
    
    # Get list of trace blocks in order
    def get_trace_blocks(self, trace_id=0):
        return [block for block in self.blocks if block.trace_id == trace_id]

    # Collect all side entry and side exit edges from the CFG
    # Side entries: edges entering the trace from outside
    # Side exits: edges leaving the trace before completion
    def collect_side_edges(self):
        return self.collect_side_edges_from(self.edges)

    # Same as collect_side_edges but operates on an arbitrary edge list. Used so
    # build_optimized_cfg can look up side entries/exits on the CLONED edge list
    # (new_edges) instead of the original self.edges, letting later steps remove or
    # mutate the exact edge objects that will end up in the optimized CFG.
    def collect_side_edges_from(self, edges):
        side_entries = []
        side_exits = []

        for edge in edges:
            if getattr(edge, "is_side_entry", False):
                side_entries.append(edge)
            if getattr(edge, "is_side_exit", False):
                side_exits.append(edge)

        return side_entries, side_exits

    # Detect operations that were moved earlier in the schedule than their original position.
    #
    # This now delegates to TraceScheduler.detect_instruction_movements' exact result
    # (schedule_result["instruction_movements"]) instead of re-deriving movement with a
    # cheap "schedule_cycle < original_index" comparison, which compares two numbers on
    # unrelated scales (a cycle count vs. a flattened instruction index) and produces
    # both false positives and false negatives. If the exact movements are not present
    # (e.g. an older schedule_result), fall back to the previous heuristic so this stays
    # backward compatible with other callers.
    def collect_moved_operations(self, schedule_result):
        movements = schedule_result.get("instruction_movements")
        scheduled = schedule_result.get("scheduled_instructions", [])

        if movements:
            moved_op_ids = {m["op_id"] for m in movements if "op_id" in m}
            if moved_op_ids:
                return [item for item in scheduled if item.get("op_id") in moved_op_ids]

        # Backward-compatible fallback (only used if instruction_movements is unavailable).
        moved_ops = []
        for item in scheduled:
            sched_pos = item.get("schedule_cycle", item.get("schedule_index", 0))
            orig_pos = item.get("original_index", 0)
            if sched_pos < orig_pos:
                moved_ops.append(item)
        return moved_ops
    
    # Analyze which instructions need split compensation for a SPECIFIC side exit.
    #
    # Split compensation is needed at a side-exit edge (exit_edge.src -> exit_edge.dst,
    # where exit_edge.dst is OUTSIDE the trace) when the scheduler has speculatively
    # hoisted instructions that originally lived in trace blocks AFTER exit_edge.src
    # to execute at or before the point where exit_edge.src finishes. Those
    # instructions' effects are baked into the fast (on-trace) path, but the sequence
    # of code the side-exit path jumps to was never rebuilt to expect them, so a
    # compensation block replays exactly those hoisted instructions on the side-exit
    # path before continuing to exit_edge.dst.
    #
    # This is now computed strictly per boundary: only operations whose ORIGINAL block
    # is strictly after exit_edge.src in trace order, AND that are confirmed as moved
    # earlier (rank-exact, from collect_moved_operations/instruction_movements), AND
    # whose scheduled position is at/py before the exit block's last scheduled
    # instruction, are included. There is no fallback to the global moved-ops list:
    # if no operations satisfy these exact conditions for this boundary, this returns
    # an empty list and the caller must NOT synthesize a compensation block.
    def get_split_compensation_ops(self, exit_edge, schedule_result, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        trace_block_ids = [b.id for b in trace_blocks]

        if exit_edge.src not in trace_block_ids:
            return []

        exit_block_index = trace_block_ids.index(exit_edge.src)
        scheduled = schedule_result.get("scheduled_instructions", [])

        # Exact set of operations the scheduler actually moved earlier (by rank).
        moved_ops = self.collect_moved_operations(schedule_result)
        moved_op_ids = {op.get("op_id") for op in moved_ops if op.get("op_id") is not None}
        if not moved_op_ids:
            return []

        # Last cycle at which exit_edge.src's own instructions execute — this is the
        # point at which the branch decision for this exact edge is effectively made.
        # Computed only from this block's UN-MOVED ("anchor") instructions, so a
        # candidate moved op is never compared against a cycle that it itself
        # contributed (which would make the boundary self-referential and always
        # fail for the earliest-moved op in a block).
        exit_block_last_cycle = -1
        for item in scheduled:
            if item.get("block_id") == exit_edge.src and item.get("op_id") not in moved_op_ids:
                cycle = item.get("schedule_cycle", 0)
                exit_block_last_cycle = max(exit_block_last_cycle, cycle)

        compensation_ops = []
        for item in scheduled:
            if item.get("op_id") not in moved_op_ids:
                continue

            orig_block = item.get("block_id")
            if orig_block not in trace_block_ids:
                continue

            orig_block_index = trace_block_ids.index(orig_block)
            sched_cycle = item.get("schedule_cycle", 0)

            # Only instructions originally strictly after THIS exit's block, and
            # scheduled at/before this exit block finishes, are relevant to THIS
            # specific side-exit boundary.
            if orig_block_index > exit_block_index and sched_cycle <= exit_block_last_cycle:
                compensation_ops.append(item)

        return compensation_ops

    # Analyze which instructions need join compensation for a SPECIFIC side entry.
    #
    # Join compensation is needed at a side-entry edge (entry_edge.src -> entry_edge.dst,
    # where entry_edge.src is OUTSIDE the trace and entry_edge.dst is IN the trace) when
    # the scheduler has hoisted instructions that originally belonged to entry_edge.dst
    # (or later trace blocks) to execute earlier than entry_edge.dst's normal starting
    # point. A path arriving via the side entry never executed that hoisted code, so a
    # compensation block must replay it before continuing into entry_edge.dst.
    #
    # Computed strictly per boundary, same exact-movement source as split compensation,
    # with no fallback to the global moved-ops list.
    def get_join_compensation_ops(self, entry_edge, schedule_result, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        trace_block_ids = [b.id for b in trace_blocks]

        if entry_edge.dst not in trace_block_ids:
            return []

        entry_block_index = trace_block_ids.index(entry_edge.dst)
        scheduled = schedule_result.get("scheduled_instructions", [])

        moved_ops = self.collect_moved_operations(schedule_result)
        moved_op_ids = {op.get("op_id") for op in moved_ops if op.get("op_id") is not None}
        if not moved_op_ids:
            return []

        # First cycle at which entry_edge.dst's own instructions execute in the
        # schedule — this is where a side-entry path would normally join in.
        # Computed only from this block's UN-MOVED ("anchor") instructions, so a
        # candidate moved op is never compared against a cycle that it itself
        # contributed (which would make the boundary self-referential and always
        # fail for the earliest-moved op in a block).
        entry_block_first_cycle = float('inf')
        for item in scheduled:
            if item.get("block_id") == entry_edge.dst and item.get("op_id") not in moved_op_ids:
                cycle = item.get("schedule_cycle", 0)
                entry_block_first_cycle = min(entry_block_first_cycle, cycle)

        if entry_block_first_cycle == float('inf'):
            return []

        compensation_ops = []
        for item in scheduled:
            if item.get("op_id") not in moved_op_ids:
                continue

            orig_block = item.get("block_id")
            if orig_block not in trace_block_ids:
                continue

            orig_block_index = trace_block_ids.index(orig_block)
            sched_cycle = item.get("schedule_cycle", 0)

            # Only instructions originally at/after THIS entry's block, scheduled
            # strictly before this entry block's normal start, are relevant to THIS
            # specific side-entry boundary.
            if orig_block_index >= entry_block_index and sched_cycle < entry_block_first_cycle:
                compensation_ops.append(item)

        return compensation_ops

    # Create a deep copy of the CFG for modification
    def clone_cfg(self):
        new_blocks = copy.deepcopy(self.blocks)
        new_edges = copy.deepcopy(self.edges)
        return new_blocks, new_edges

    # Get the next available block ID (for creating compensation blocks)
    def next_block_id(self, blocks):
        if not blocks:
            return 0
        return max(block.id for block in blocks) + 1

    # Create a new basic block (uses factory if provided, otherwise creates dynamic object)
    def make_block(self, block_id, statements, trace_id=None, is_bookkeeping=True):
        if self.block_factory is not None:
            block = self.block_factory(block_id, statements)
            block.is_bookkeeping = is_bookkeeping
            block.trace_id = trace_id
            return block

        # Create dynamic block object if no factory provided
        block = type("DynamicBlock", (), {})()
        block.id = block_id
        block.statements = list(statements)
        block.instructions = list(statements)
        block.is_bookkeeping = is_bookkeeping
        block.trace_id = trace_id
        return block

    # Create a new CFG edge (uses factory if provided, otherwise creates dynamic object)
    def make_edge(
        self,
        src,
        dst,
        label="",
        count=0,
        probability=None,
        is_trace_edge=False,
        is_side_entry=False,
        is_side_exit=False
    ):
        if self.edge_factory is not None:
            edge = self.edge_factory(src, dst, label)
            edge.count = count
            edge.probability = probability
            edge.is_trace_edge = is_trace_edge
            edge.is_side_entry = is_side_entry
            edge.is_side_exit = is_side_exit
            return edge

        # Create dynamic edge object if no factory provided
        edge = type("DynamicEdge", (), {})()
        edge.src = src
        edge.dst = dst
        edge.label = label
        edge.count = count
        edge.probability = probability
        edge.is_trace_edge = is_trace_edge
        edge.is_side_entry = is_side_entry
        edge.is_side_exit = is_side_exit
        return edge

    # Reorder each trace block's visible statements to reflect the ACTUAL scheduled
    # order, instead of leaving the original textual order in place.
    #
    # Two kinds of non-data statements exist in a block's statement list:
    #  - Pure structural markers (ENTRY/EXIT/JOIN/AFTER_LOOP/AFTER_FOR): these have no
    #    schedule_cycle at all (collect_trace_instructions filters them out before the
    #    scheduler ever sees them), so they are left anchored at the front of the
    #    block — there is no scheduled position to place them at.
    #  - Control headers (IF[..]/WHILE[..]/FOR[..]): these DO get scheduled (they carry
    #    real cycle placement and participate in the dependency graph as barriers), so
    #    they are treated as regular scheduled instructions and placed at their actual
    #    scheduled position alongside the data operations, instead of being forced to
    #    the front regardless of when they were actually scheduled.
    #
    # CROSS-BLOCK RELOCATION: with the relaxed control-dependence rule in
    # TraceScheduler.build_dependency_graph, an operation can now be
    # genuinely hoisted to run during an EARLIER block's cycle window than
    # the block it originally lived in. When that happens, the optimized CFG
    # should show the statement actually living in the block it now executes
    # alongside -- not just reordered within its original block -- otherwise
    # the DOT output would misrepresent the schedule. We detect this using
    # the exact instruction_movements the scheduler already computed
    # (schedule_result["instruction_movements"], produced by
    # TraceScheduler.detect_instruction_movements): for each moved op, its
    # DESTINATION block is whichever trace block's own (non-hoisted)
    # instructions bracket its schedule_cycle -- i.e. the block whose
    # instructions are executing during that cycle in the final schedule.
    #
    # Schedulable instructions are ordered by (schedule_cycle, op_id) for a
    # stable, deterministic order within whichever block they end up in.
    def reorder_blocks_by_schedule(self, blocks, schedule_result, trace_id=0):
        scheduled = schedule_result.get("scheduled_instructions", [])
        if not scheduled:
            return

        pure_structural_markers = ("ENTRY", "EXIT", "JOIN", "AFTER_LOOP", "AFTER_FOR")

        moved_ops = self.collect_moved_operations(schedule_result)
        moved_op_ids = {op.get("op_id") for op in moved_ops if op.get("op_id") is not None}

        trace_blocks = self.get_trace_blocks(trace_id)
        trace_block_ids = [b.id for b in trace_blocks]
        trace_index = {bid: i for i, bid in enumerate(trace_block_ids)}

        # Determine each block's own cycle window from the instructions that
        # were NOT hoisted out of it (its "anchor" instructions), so a moved
        # op's destination can be resolved against blocks' real execution
        # windows rather than being circularly affected by other moved ops.
        anchor_items = [item for item in scheduled if item.get("op_id") not in moved_op_ids]
        block_last_cycle = {}
        for item in anchor_items:
            bid = item.get("block_id")
            cycle = item.get("schedule_cycle", 0)
            block_last_cycle[bid] = max(block_last_cycle.get(bid, cycle), cycle)

        def destination_block_for(item):
            op_id = item.get("op_id")
            orig_block = item.get("block_id")
            if op_id not in moved_op_ids or orig_block not in trace_index:
                return orig_block

            sched_cycle = item.get("schedule_cycle", 0)
            orig_pos = trace_index[orig_block]

            # Walk earlier trace blocks (closest first) and relocate into the
            # first one whose own instructions are still executing at/after
            # this cycle -- i.e. the earliest block this op could have
            # actually been co-scheduled into given its new cycle.
            best_block = orig_block
            for bid in trace_block_ids[:orig_pos]:
                last_cycle = block_last_cycle.get(bid)
                if last_cycle is not None and sched_cycle <= last_cycle:
                    best_block = bid
                    break
            return best_block

        # Group scheduled instructions (including control headers) by the
        # block they ended up executing in (their destination, which may
        # differ from their original block_id for genuinely hoisted ops), in
        # schedule order. Within the same cycle, preserve the exact order
        # list_schedule emitted them in (its priority function already
        # resolved ties via critical-path/descendant/original-index
        # heuristics) rather than re-sorting by op_id, which would silently
        # invert the scheduler's real within-cycle ordering decisions.
        by_block = {}
        for item in sorted(
            enumerate(scheduled),
            key=lambda pair: (pair[1]["schedule_cycle"], pair[0])
        ):
            _, entry_item = item
            dest_block = destination_block_for(entry_item)
            by_block.setdefault(dest_block, []).append(entry_item["instruction"])

        for block in blocks:
            if getattr(block, "trace_id", None) != trace_id:
                continue
            if block.id not in by_block:
                original_statements = list(block.statements)
                leading_markers = [s for s in original_statements if s in pure_structural_markers]
                if leading_markers and leading_markers != original_statements:
                    block.statements = leading_markers
                    block.instructions = list(leading_markers)
                continue

            original_statements = list(block.statements)
            # Only pure structural markers (which never appear in the schedule) stay
            # anchored at the front; everything else follows the schedule's order.
            leading_markers = [s for s in original_statements if s in pure_structural_markers]
            scheduled_order = by_block[block.id]

            new_statements = leading_markers + scheduled_order
            block.statements = new_statements
            block.instructions = list(new_statements)

    # Build optimized CFG with compensation code
    def build_optimized_cfg(self, schedule_result, trace_id=0):
        # Clone CFG
        new_blocks, new_edges = self.clone_cfg()

        # Reflect the scheduler's actual instruction order inside each trace block so
        # the optimized CFG visibly shows the effect of scheduling, not just the
        # original program order relabeled as "optimized".
        self.reorder_blocks_by_schedule(new_blocks, schedule_result, trace_id)

        # Collect all side entry and exit edges (looked up on the CLONED edges so the
        # edges we later remove/replace are the same objects living in new_edges).
        side_entries, side_exits = self.collect_side_edges_from(new_edges)

        # Get all operations that were moved earlier in the schedule (exact, rank-based)
        moved_ops = self.collect_moved_operations(schedule_result)

        block_id_counter = self.next_block_id(new_blocks)
        compensation_blocks = []
        compensation_edges = []
        edges_to_remove = set()
        split_comp_count = 0
        join_comp_count = 0

        # For each side exit, create compensation block
        for exit_edge in side_exits:
            # Get instructions that need compensation for this specific exit
            comp_ops = self.get_split_compensation_ops(exit_edge, schedule_result, trace_id)

            # NOTE: no fallback to the global moved_ops list here. If nothing was
            # actually hoisted across THIS boundary, no compensation block is created
            # and the original edge is left untouched — this is what keeps the
            # optimized CFG from bloating with irrelevant compensation code.
            if comp_ops:
                # Create compensation block with all required instructions
                comp_statements = [
                    f"SPLIT_COMP: {op['instruction']}" 
                    for op in comp_ops
                ]
                
                compensation_block = self.make_block(
                    block_id_counter,
                    comp_statements,
                    trace_id=None,
                    is_bookkeeping=True
                )
                block_id_counter += 1
                compensation_blocks.append(compensation_block)
                split_comp_count += 1

                # Reroute: exit_edge.src -> compensation_block -> exit_edge.dst
                compensation_edges.append(
                    self.make_edge(
                        exit_edge.src,
                        compensation_block.id,
                        label=f"split_comp_{exit_edge.label}"
                    )
                )
                compensation_edges.append(
                    self.make_edge(
                        compensation_block.id,
                        exit_edge.dst,
                        label="resume"
                    )
                )

                # The direct exit_edge.src -> exit_edge.dst path is now superseded by
                # exit_edge.src -> compensation_block -> exit_edge.dst. Keeping the
                # original edge active would let the CFG silently skip the
                # compensation code on some paths, corrupting the moved instructions'
                # effects for anything reachable only via exit_edge.dst. Mark it for
                # removal so only the rerouted path remains.
                edges_to_remove.add(id(exit_edge))

        # For each side entry, create compensation block
        for entry_edge in side_entries:
            # Get instructions that need compensation for this specific entry
            comp_ops = self.get_join_compensation_ops(entry_edge, schedule_result, trace_id)

            # NOTE: no fallback to the global moved_ops list here either, for the same
            # reason as split compensation above.
            if comp_ops:
                # Create compensation block with all required instructions
                comp_statements = [
                    f"JOIN_COMP: {op['instruction']}" 
                    for op in comp_ops
                ]
                
                compensation_block = self.make_block(
                    block_id_counter,
                    comp_statements,
                    trace_id=None,
                    is_bookkeeping=True
                )
                block_id_counter += 1
                compensation_blocks.append(compensation_block)
                join_comp_count += 1

                # Reroute: entry_edge.src -> compensation_block -> entry_edge.dst
                compensation_edges.append(
                    self.make_edge(
                        entry_edge.src,
                        compensation_block.id,
                        label=f"join_comp_{entry_edge.label}"
                    )
                )
                compensation_edges.append(
                    self.make_edge(
                        compensation_block.id,
                        entry_edge.dst,
                        label="resume"
                    )
                )

                # Same reasoning as the split-exit case: the direct entry_edge.src ->
                # entry_edge.dst path must not stay active alongside the rerouted
                # compensation path, or the side-entry path could bypass the join
                # compensation entirely.
                edges_to_remove.add(id(entry_edge))

        # Drop original edges that were superseded by a compensation reroute so the
        # optimized CFG never has two conflicting active paths for the same boundary.
        if edges_to_remove:
            new_edges = [e for e in new_edges if id(e) not in edges_to_remove]
            side_exits = [e for e in side_exits if id(e) not in edges_to_remove]
            side_entries = [e for e in side_entries if id(e) not in edges_to_remove]

        # Add compensation blocks and edges to the new CFG
        new_blocks.extend(compensation_blocks)
        new_edges.extend(compensation_edges)
        
        # Calculate total compensation instruction count
        total_comp_instructions = sum(
            len(b.statements) for b in compensation_blocks
        )

        return {
            "trace_id": trace_id,
            "blocks": new_blocks,
            "edges": new_edges,
            "side_entries": side_entries,
            "side_exits": side_exits,
            "moved_operations": moved_ops,
            "added_compensation_blocks": compensation_blocks,
            "added_compensation_edges": compensation_edges,
            "split_compensation_count": split_comp_count,
            "join_compensation_count": join_comp_count,
            "total_compensation_instructions": total_comp_instructions
        }
    
    # Simple bookkeeping summary without building full optimized CFG
    def collect_bookkeeping(self, trace_id=0):
        side_entries, side_exits = self.collect_side_edges()
        
        return {
            "trace_id": trace_id,
            "side_entries": [{"src": e.src, "dst": e.dst, "label": e.label} for e in side_entries],
            "side_exits": [{"src": e.src, "dst": e.dst, "label": e.label} for e in side_exits],
            "bookkeeping_block_count": len(side_entries) + len(side_exits)
        }

    # Generate .dot format visualization of the CFG with compensation blocks highlighted
    # Color scheme:
    #   - Yellow: trace blocks (optimized path)
    #   - Purple: bookkeeping/compensation blocks
    #   - Red edges: trace edges (the optimized path)
    #   - Blue dashed edges: side entries/exits
    #   - Purple edges: compensation routing
    def to_dot(self, blocks, edges):
        lines = [
            "digraph CFG {",
            "  rankdir=TB;",
            "  node [shape=box, fontname=Helvetica];"
        ]

        # Generate nodes (blocks)
        for b in blocks:
            label = "\\n".join(b.statements) if getattr(b, "statements", None) else f"B{b.id}"
            attrs = []

            # Highlight trace blocks in yellow
            if getattr(b, "trace_id", None) is not None:
                attrs.append('style="filled"')
                attrs.append('fillcolor="lightyellow"')

            # Highlight bookkeeping blocks in purple
            if getattr(b, "is_bookkeeping", False):
                attrs.append('color="purple"')
                attrs.append('style="filled"')
                attrs.append('fillcolor="purple"')

            attr_text = ", ".join(dict.fromkeys(attrs))
            if attr_text:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}", {attr_text}];')
            else:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}"];')

        # Generate edges
        for edge in edges:
            label_parts = []
            if getattr(edge, "label", ""):
                label_parts.append(edge.label)
            if getattr(edge, "probability", None) is not None:
                label_parts.append(f"{edge.probability:.2f}")

            attr_parts = []
            if label_parts:
                attr_parts.append(f'label="{" | ".join(label_parts)}"')

            # Trace edges in red (the optimized path)
            if getattr(edge, "is_trace_edge", False):
                attr_parts.append('color="red"')
                attr_parts.append('penwidth=2.0')
            # Side entries/exits in blue dashed
            elif getattr(edge, "is_side_entry", False) or getattr(edge, "is_side_exit", False):
                attr_parts.append('color="blue"')
                attr_parts.append('style="dashed"')

            # Compensation edges in purple
            edge_label = getattr(edge, "label", "")
            if edge_label.startswith("split_comp") or edge_label.startswith("join_comp"):
                attr_parts.append('color="purple"')
                attr_parts.append('penwidth=2.0')

            attr = f' [{", ".join(attr_parts)}]' if attr_parts else ""
            lines.append(f"  B{edge.src} -> B{edge.dst}{attr};")

        lines.append("}")
        return "\n".join(lines)