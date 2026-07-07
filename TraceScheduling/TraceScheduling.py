import ast
from collections import defaultdict, deque


# implements list scheduling for trace optimization.

# AST visitor to analyze variable reads/writes in an instruction
# Used to detect data dependencies between operations
class VariableAccessCollector(ast.NodeVisitor):
    def __init__(self):
        self.reads = set()      # Variables read by this instruction
        self.writes = set()     # Variables written by this instruction
        self.has_call = False   # Contains function call (side effects)
        self.is_control = False # Is a control flow instruction
        self.op_type = "other"  # Operation type for latency estimation

    # Variable name encountered - check if read or write
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.reads.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self.writes.add(node.id)

    # Assignment statement: x = value
    def visit_Assign(self, node):
        self.op_type = "assign"
        for target in node.targets:
            self.visit(target)
        self.visit(node.value)

    # Augmented assignment: x += value
    def visit_AugAssign(self, node):
        self.op_type = "assign"
        self.visit(node.target)
        self.visit(node.value)

    # Annotated assignment: x: int = value
    def visit_AnnAssign(self, node):
        self.op_type = "assign"
        self.visit(node.target)
        if node.value:
            self.visit(node.value)

    # Return statement
    def visit_Return(self, node):
        self.op_type = "return"
        if node.value:
            self.visit(node.value)

    # Function call - may have side effects, higher latency
    def visit_Call(self, node):
        self.has_call = True
        self.op_type = "call"
        self.generic_visit(node)

    # Comparison operation
    def visit_Compare(self, node):
        if self.op_type == "other":
            self.op_type = "compare"
        self.generic_visit(node)

    # Binary operation - mul/div have higher latency
    def visit_BinOp(self, node):
        if isinstance(node.op, (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)):
            if self.op_type == "other":
                self.op_type = "muldiv"
        elif self.op_type == "other":
            self.op_type = "arith"
        self.generic_visit(node)


# Main trace scheduling class implementing list scheduling algorithm
class TraceScheduler:

    # Initialize with CFG blocks and edges
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    # Get all blocks belonging to a specific trace
    def get_trace_blocks(self, trace_id=0):
        return [block for block in self.blocks if block.trace_id == trace_id]

    # Compute, for each control-header block in the trace, the set of block
    # ids that are exclusively guarded by that header (i.e. blocks that only
    # execute because the branch/loop went a particular way, up to but not
    # including the join/merge point where control flow reconverges).
    #
    # This is what lets build_dependency_graph tell the difference between
    # "op is inside the branch this header controls" (must stay ordered
    # after the header) and "op lives before the header, or after the branch
    # has already joined back up" (free to be scheduled independently of the
    # header, and therefore eligible for genuine cross-block hoisting).
    #
    # Implementation: for each trace block whose last real instruction is a
    # control header (IF/WHILE/FOR), walk forward from each of the header's
    # CFG successors that is NOT the immediate next block in trace order
    # (i.e. the "taken"/side path in the untaken direction is out-of-trace;
    # the in-trace guarded region is the successor chain up until a block is
    # reached that is also reachable from before the header -- the natural
    # join point). We approximate the join point as the first block, walking
    # forward from the header along trace order, whose in-trace predecessors
    # include a block from BOTH sides of the branch (i.e. a JOIN block).
    def compute_controlled_block_ids(self, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        trace_block_ids = [b.id for b in trace_blocks]
        block_by_id = {b.id: b for b in trace_blocks}
        trace_index = {bid: i for i, bid in enumerate(trace_block_ids)}

        # Predecessor counts must come from the FULL CFG edge set, not just
        # edges whose both endpoints are in-trace: a real join point (e.g. the
        # merge after a nested if/else) has one predecessor on the taken path
        # (in-trace) and one on the not-taken path (typically off-trace, a
        # side entry). Restricting to in-trace-only edges would hide the
        # off-trace predecessor and make every join look like a simple
        # 1-predecessor block, silently extending a header's guarded region
        # past the real join and mis-marking downstream blocks as control-
        # dependent when they are not.
        pred_edges = defaultdict(list)
        for edge in self.edges:
            pred_edges[edge.dst].append(edge.src)

        controlled = {}

        for bid in trace_block_ids:
            block = block_by_id[bid]
            statements = getattr(block, "instructions", None) or getattr(block, "statements", [])
            header_stmt = None
            for s in statements:
                if s.startswith(("IF[", "WHILE[", "FOR[")):
                    header_stmt = s
            if header_stmt is None:
                continue  # not a control-header block

            header_pos = trace_index[bid]

            # Find the join block: the first block (in trace order, after
            # this header) whose predecessor set includes at least one block
            # that is NOT reachable purely by falling straight through from
            # this header without ever leaving trace order. In this project's
            # single-entry trace shape, that is simply the first later block
            # with more than one distinct predecessor recorded in pred_edges
            # (a real merge point), OR, if none exists before the trace ends,
            # there is no join and the guarded region runs to the end of the
            # trace.
            join_pos = len(trace_block_ids)
            for later_bid in trace_block_ids[header_pos + 1:]:
                preds_here = set(pred_edges.get(later_bid, []))
                if len(preds_here) > 1:
                    join_pos = trace_index[later_bid]
                    break

            guarded_ids = set(trace_block_ids[header_pos + 1: join_pos])
            controlled[bid] = guarded_ids

        return controlled

    # Collect all schedulable instructions from trace blocks (skip control markers)
    def collect_trace_instructions(self, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        scheduled = []

        for block in trace_blocks:
            for instr in block.instructions:
                if instr not in ("ENTRY", "EXIT", "JOIN", "AFTER_LOOP", "AFTER_FOR"):
                    scheduled.append({
                        "block_id": block.id,
                        "instruction": instr
                    })

        return scheduled

    # Parse instruction text using AST to extract reads, writes, and operation type
    def parse_instruction(self, instruction_text):
        synthetic_prefixes = ("IF[", "WHILE[", "FOR[")
        if instruction_text.startswith(synthetic_prefixes):
            return {
                "text": instruction_text,
                "reads": set(),
                "writes": set(),
                "latency": 1,
                "op_type": "control",
                "is_control": True,
                "has_call": False
            }

        try:
            node = ast.parse(instruction_text).body[0]
            collector = VariableAccessCollector()
            collector.visit(node)

            latency = 1
            if collector.has_call:
                latency = 3
            elif collector.op_type == "muldiv":
                latency = 2
            elif collector.op_type in ("assign", "compare", "arith", "return"):
                latency = 1

            return {
                "text": instruction_text,
                "reads": collector.reads,
                "writes": collector.writes,
                "latency": latency,
                "op_type": collector.op_type,
                "is_control": collector.is_control,
                "has_call": collector.has_call
            }
        except Exception:
            # CONSERVATIVE FALLBACK: an instruction that cannot be parsed must not be
            # treated as dependency-free. Assuming empty reads/writes lets the scheduler
            # freely reorder around it, which is unsafe since we have no idea what it
            # actually touches. Instead we mark it as a full barrier: it depends on
            # everything before it and everything after it depends on it (via
            # is_control / has_call flags used by build_dependency_graph), and we
            # deliberately keep latency conservative (call-level) too.
            return {
                "text": instruction_text,
                "reads": set(),
                "writes": set(),
                "latency": 3,
                "op_type": "unknown",
                "is_control": True,
                "has_call": True
            }

    # Build list of operations from trace blocks with full metadata
    def build_trace_operations(self, trace_id=0):
        raw_instructions = self.collect_trace_instructions(trace_id)
        operations = []

        for index, item in enumerate(raw_instructions):
            parsed = self.parse_instruction(item["instruction"])
            operations.append({
                "id": index,
                "block_id": item["block_id"],
                "instruction": item["instruction"],
                "reads": parsed["reads"],
                "writes": parsed["writes"],
                "latency": parsed["latency"],
                "op_type": parsed["op_type"],
                "is_control": parsed["is_control"],
                "has_call": parsed["has_call"],
                "original_index": index
            })

        return operations

    # Build dependency graph between operations
    #
    # RELAXED CONTROL-DEPENDENCE RULE (cross-block movement support):
    # Previously, ANY control instruction (IF/WHILE/FOR header) forced a
    # dependency edge to/from every other operation in the trace, regardless
    # of whether that operation was actually control-dependent on the branch.
    # That made it impossible for any operation positioned after a header in
    # the flattened trace order to ever be scheduled at or before that
    # header's own cycle -- which blocked all genuine cross-block hoisting.
    #
    # The relaxed rule only inserts a control edge between a header and an
    # operation that is control-dependent on it, i.e. an operation whose
    # block is nested INSIDE the region the header guards (a block that is
    # dominated by the header's block and does NOT also dominate/precede the
    # header -- concretely, an operation in the SAME block as the header, or
    # in a block strictly between the header's block and the header's own
    # immediate-post-dominator/join block, per the trace's block order).
    # Operations from blocks entirely before the header, or from the join
    # block onward that don't read/write anything the branch touches, are no
    # longer artificially barriered -- they may be spec-hoisted above the
    # branch, exactly like a real compiler would allow for side-effect-free
    # arithmetic. Side-effecting operations (has_call) remain full barriers,
    # since speculating those would be unsafe without much more machinery
    # than compensation blocks provide here.
    def build_dependency_graph(self, operations, controlled_block_ids=None):
        preds = defaultdict(set)
        succs = defaultdict(set)

        if controlled_block_ids is None:
            controlled_block_ids = {}

        for i in range(len(operations)):
            op_i = operations[i]
            for j in range(i + 1, len(operations)):
                op_j = operations[j]

                dep = False

                if op_i["writes"] & op_j["reads"]:
                    dep = True  # RAW
                elif op_i["reads"] & op_j["writes"]:
                    dep = True  # WAR
                elif op_i["writes"] & op_j["writes"]:
                    dep = True  # WAW
                elif op_i["is_control"] and self.is_control_dependent(op_i, op_j, controlled_block_ids):
                    dep = True
                elif op_j["is_control"] and self.is_control_dependent(op_j, op_i, controlled_block_ids):
                    dep = True
                elif op_i["has_call"] or op_j["has_call"]:
                    dep = True

                if dep:
                    succs[op_i["id"]].add(op_j["id"])
                    preds[op_j["id"]].add(op_i["id"])

        for op in operations:
            preds[op["id"]] = preds[op["id"]]
            succs[op["id"]] = succs[op["id"]]

        return preds, succs

    # True iff `op` is control-dependent on the branch header `header_op`,
    # i.e. `op` sits in a block that only executes because the branch went a
    # particular way. `controlled_block_ids` maps each header's block_id to
    # the set of block_ids that are exclusively reachable through that
    # header's branch (built by PythonToCFG/TraceSelection style dominance,
    # passed in by the caller). If no mapping is supplied for this header, we
    # fall back to the conservative old behavior (treat op as dependent) so
    # callers that don't supply dominance info keep the safe, original
    # semantics rather than silently becoming unsound.
    def is_control_dependent(self, header_op, op, controlled_block_ids):
        guarded = controlled_block_ids.get(header_op["block_id"])
        if guarded is None:
            return True
        if op["block_id"] == header_op["block_id"]:
            return True
        return op["block_id"] in guarded

    # Compute number of dependent descendants for each operation
    def compute_descendant_counts(self, operations, succs):
        memo = {}

        def dfs(op_id):
            if op_id in memo:
                return memo[op_id]
            visited = set()
            stack = list(succs[op_id])
            while stack:
                nxt = stack.pop()
                if nxt not in visited:
                    visited.add(nxt)
                    stack.extend(succs[nxt])
            memo[op_id] = len(visited)
            return memo[op_id]

        return {op["id"]: dfs(op["id"]) for op in operations}

    # Compute critical path score for each operation
    def compute_critical_path_scores(self, operations, succs):
        latency = {op["id"]: op["latency"] for op in operations}
        reverse_topological = list(reversed([op["id"] for op in operations]))
        score = {}

        for op_id in reverse_topological:
            if not succs[op_id]:
                score[op_id] = latency[op_id]
            else:
                score[op_id] = latency[op_id] + max(score[s] for s in succs[op_id])

        return score

    # List scheduling
    def list_schedule(self, operations, preds, succs, num_units=2):
        indegree = {op["id"]: len(preds[op["id"]]) for op in operations}
        critical_path = self.compute_critical_path_scores(operations, succs)
        descendants = self.compute_descendant_counts(operations, succs)

        op_map = {op["id"]: op for op in operations}
        
        # Track when each operation finishes
        finish_time = {}
        scheduled_ops = []
        scheduled_set = set()
        
        current_cycle = 0
        
        # Main scheduling loop - continue until all operations scheduled
        while len(scheduled_set) < len(operations):
            # Find ready operations (all predecessors completed)
            ready = []
            for op in operations:
                if op["id"] in scheduled_set:
                    continue
                
                # Check all predecessors have finished by current cycle
                all_preds_done = all(
                    pred_id in scheduled_set and finish_time[pred_id] <= current_cycle
                    for pred_id in preds[op["id"]]
                )
                if all_preds_done:
                    ready.append(op["id"])
            
            if ready:
                # Sort by priority: critical path (desc), descendants (desc), original order (asc)
                ready.sort(
                    key=lambda op_id: (
                        -critical_path[op_id],
                        -descendants[op_id],
                        op_map[op_id]["original_index"]
                    )
                )
                
                # Schedule up to num_units operations this cycle
                scheduled_this_cycle = 0
                for op_id in ready:
                    if scheduled_this_cycle >= num_units:
                        break
                    
                    op = op_map[op_id]
                    scheduled_ops.append({
                        "schedule_cycle": current_cycle,
                        "op_id": op_id,
                        "block_id": op["block_id"],
                        "instruction": op["instruction"],
                        "latency": op["latency"],
                        "critical_path": critical_path[op_id],
                        "descendants": descendants[op_id],
                        "original_index": op["original_index"]
                    })
                    scheduled_set.add(op_id)
                    finish_time[op_id] = current_cycle + op["latency"]
                    scheduled_this_cycle += 1
            
            current_cycle += 1
            
            # Safety limit to prevent infinite loops
            if current_cycle > len(operations) * 10:
                break
        
        # Calculate makespan
        makespan = max(finish_time.values()) if finish_time else 0
        
        return scheduled_ops, makespan

    # Detect which instructions moved across block boundaries.
    #
    # EXACT DEFINITION: build the original program order (a single total order,
    # since "original_index" is already assigned in original program/traversal
    # order across the whole trace) and the scheduled order (ordered by
    # schedule_cycle, breaking ties deterministically by original_index so
    # operations issued in the same cycle keep their relative original order).
    # An operation is "moved" iff its position (rank) in the scheduled order is
    # strictly earlier than its position (rank) in the original order, AND its
    # scheduled block is different from the block that block boundary logic
    # cares about (i.e. it crossed at least one block boundary earlier than it
    # originally would have executed). This replaces the previous O(n^2)
    # pairwise heuristic, which produced false positives (e.g. flagging
    # "return out", the very last instruction, as moved).
    def detect_instruction_movements(self, operations, scheduled_ops):
        movements = []

        # Original rank: operations are already produced in original program order,
        # so original_index (0-based) IS the original rank.
        original_rank = {op["id"]: op["original_index"] for op in operations}
        original_block = {op["id"]: op["block_id"] for op in operations}

        # Scheduled rank: sort by (schedule_cycle, original_index) for a stable,
        # deterministic total order of the actual schedule.
        ordered_schedule = sorted(
            scheduled_ops,
            key=lambda item: (item["schedule_cycle"], item["original_index"])
        )
        scheduled_rank = {item["op_id"]: rank for rank, item in enumerate(ordered_schedule)}

        for item in scheduled_ops:
            op_id = item["op_id"]
            orig_rank = original_rank[op_id]
            sched_rank = scheduled_rank[op_id]

            # Only a genuine, exact reordering counts as "moved": the operation's
            # position relative to the rest of the trace shifted earlier.
            if sched_rank < orig_rank:
                movements.append({
                    "type": "moved_earlier",
                    "instruction": item["instruction"],
                    "op_id": op_id,
                    "from_block": original_block[op_id],
                    "original_index": orig_rank,
                    "original_rank": orig_rank,
                    "scheduled_rank": sched_rank,
                    "schedule_cycle": item["schedule_cycle"]
                })

        return movements

    # Main entry point for trace scheduling
    def schedule_trace(self, trace_id=0, num_units=2):
        trace_blocks = self.get_trace_blocks(trace_id)
        
        if not trace_blocks:
            return {
                "trace_id": trace_id,
                "block_ids": [],
                "original_instruction_count": 0,
                "scheduled_instructions": [],
                "schedule_length": 0,
                "makespan": 0,
                "instruction_movements": [],
                "num_functional_units": num_units
            }
        
        operations = self.build_trace_operations(trace_id)
        
        if not operations:
            return {
                "trace_id": trace_id,
                "block_ids": [block.id for block in trace_blocks],
                "original_instruction_count": 0,
                "scheduled_instructions": [],
                "schedule_length": 0,
                "makespan": 0,
                "instruction_movements": [],
                "num_functional_units": num_units
            }
        
        controlled_block_ids = self.compute_controlled_block_ids(trace_id)
        preds, succs = self.build_dependency_graph(operations, controlled_block_ids)
        scheduled_instructions, makespan = self.list_schedule(operations, preds, succs, num_units)
        
        # Detect instruction movements for bookkeeping
        instruction_movements = self.detect_instruction_movements(operations, scheduled_instructions)

        return {
            "trace_id": trace_id,
            "block_ids": [block.id for block in trace_blocks],
            "original_instruction_count": len(operations),
            "scheduled_instructions": scheduled_instructions,
            "schedule_length": len(scheduled_instructions),
            "makespan": makespan,
            "instruction_movements": instruction_movements,
            "num_functional_units": num_units,
            "operations": operations
        }
    
    # Baseline schedule: sequential execution without optimization
    def schedule_baseline(self, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        operations = self.build_trace_operations(trace_id)
        
        # Execute one instruction per cycle, sequentially
        scheduled_instructions = []
        current_cycle = 0
        
        for op in operations:
            scheduled_instructions.append({
                "schedule_cycle": current_cycle,
                "op_id": op["id"],
                "block_id": op["block_id"],
                "instruction": op["instruction"],
                "latency": op["latency"],
                "original_index": op["original_index"]
            })
            current_cycle += op["latency"]
        
        return {
            "trace_id": trace_id,
            "block_ids": [block.id for block in trace_blocks],
            "original_instruction_count": len(operations),
            "scheduled_instructions": scheduled_instructions,
            "schedule_length": len(scheduled_instructions),
            "makespan": current_cycle,
            "instruction_movements": [],
            "num_functional_units": 1
        }