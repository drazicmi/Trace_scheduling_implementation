import ast
from collections import defaultdict, deque


# implements list scheduling for trace optimization.

# AST visitor to analyze variable reads/writes in an instruction
# Used to detect data dependencies between operations
class VariableAccessCollector(ast.NodeVisitor):
    def __init__(self):
        self.reads = set()  # Variables read by this instruction (for hazard analysis)
        self.writes = set()  # Variables written by this instruction (for hazard analysis)
        self.has_call = False  # Contains function call (side effects) (barrier logic in building dependency graph)
        self.is_control = False  # Is a control flow instruction (barrier logic in building dependency graph)
        self.op_type = "other"  # Operation type for latency estimation

    # Variable name encountered - check if read or write and populate reads and writes sets
    # Everything else below exists to route subexpressions into a call to visit_Name (directly or via generic_visit)
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.reads.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self.writes.add(node.id)

    # Assignment statement: x = value
    def visit_Assign(self, node):
        self.op_type = "assign"
        # Since python allows x = y = ... = 2, node.targets is a list
        for target in node.targets:
            # Calling self.visit(...) on each recurses back into the visitor, so target
            # (a Name node with Store context) hits visit_Name and gets recorded as a write,
            # while walking value picks up whatever it reads.
            self.visit(target)
        # node.value is a right hand side
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
        # Forces operation to act as a universal barrier in dependency graph
        # Everything above it stays above it, everything under it stays under it
        self.has_call = True
        self.op_type = "call"
        # generic visit is used, because function calls have more complex shape
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
        # Check if it's a control instruction
        synthetic_prefixes = ("IF[", "WHILE[", "FOR[")
        if instruction_text.startswith(synthetic_prefixes):
            return {
                "text": instruction_text,
                "reads": set(),
                "writes": set(),
                "latency": 1,
                "op_type": "control",
                "is_control": True,
                # If structure could contain a call but that case is being ignored here
                "has_call": False
            }

        try:
            # Grab first (only) top statement from the instruction
            node = ast.parse(instruction_text).body[0]
            # Instantiate variable access collector object for each new instruction
            # The instance cannot be reused since it accumulates state, and .visit(node) kicks off dispatch walk.
            collector = VariableAccessCollector()
            collector.visit(node)

            latency = 1
            if collector.has_call:
                # if/elif are the most expensive operations
                latency = 3
            elif collector.op_type == "muldiv":
                # mul/div operations are a bit faster
                latency = 2
            elif collector.op_type in ("assign", "compare", "arith", "return"):
                # simple operations are the fastest
                latency = 1

            return {
                "text": instruction_text,
                "reads": collector.reads,
                "writes": collector.writes,
                "latency": latency,
                "op_type": collector.op_type,
                # It remains false, because its default value for "collector.is_control" and it won't be set anywhere
                "is_control": collector.is_control,
                "has_call": collector.has_call
            }
        except Exception:
            # An instruction that cannot be parsed must not be treated as dependency-free.
            # Assuming empty reads/writes lets the scheduler freely reorder around it, which is unsafe since we have no idea what it actually touches.
            # Instead, we mark it as a full barrier: it depends on everything before it and everything after it depends on it
            # (via is_control / has_call flags used by build_dependency_graph), and we deliberately keep latency conservative (call-level) too.
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
        # Grab list of schedule instructions
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
                # Value "original program order" used for tie-breaking and movement detection
                "original_index": index
            })

        return operations

    def schedule_baseline(self, trace_id=0):
        # Get all blocks on given trace
        trace_blocks = self.get_trace_blocks(trace_id)
        # Grab all schedulable instructions on a trace
        operations = self.build_trace_operations(trace_id)

        # Execute instructions sequentially and count the total execution time in cycles
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

        # Return metadata that was calculated during program "simulation"
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

    def compute_controlled_block_ids(self, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        """
            For every IF/WHILE/FOR header block in the trace, this figures out which downstream blocks are "guarded" by that header 
            (i.e., only execute because the branch went a certain way), stopping at the join point where control flow reconverges. 
            This mapping is what lets dependency-graph construction distinguish "this instruction is inside the branch" 
            from "this instruction just happens to come after the branch header in trace order" — 
            enabling real cross-block hoisting instead of treating every control instruction as a universal barrier.
            
        """
        # Ordered list of blocks ids in trace order
        trace_block_ids = [b.id for b in trace_blocks]
        # Lookup that converts block id into actual block
        block_by_id = {b.id: b for b in trace_blocks}
        # The inverse of the first - id -> position - needed because later code needs to slice the block list by position
        trace_index = {bid: i for i, bid in enumerate(trace_block_ids)}

        # Builds a reverse adjacency map (predecessor list) from the entire CFG's edges, not filtered to the trace.
        pred_edges = defaultdict(list)
        for edge in self.edges:
            pred_edges[edge.dst].append(edge.src)

        controlled = {}

        # Loop over all trace blocks
        for bid in trace_block_ids:
            block = block_by_id[bid]
            # Grab statement the safe way, one of the getattr will trigger
            statements = getattr(block, "instructions", None) or getattr(block, "statements", [])
            # Check if it's a control header block
            header_stmt = None
            for s in statements:
                if s.startswith(("IF[", "WHILE[", "FOR[")):
                    header_stmt = s
            if header_stmt is None:
                continue  # not a control-header block

            # Start searching for the join from headers position in trace order
            header_pos = trace_index[bid]

            # Find the join block: the first block (in trace order, after this header) whose predecessor set includes at least one block
            # that is NOT reachable purely by falling straight through from this header without ever leaving trace order.
            # If we don't find join block, assume that the header is running through the end of a trace
            join_pos = len(trace_block_ids)
            # Loop over every block after the header position
            for later_bid in trace_block_ids[header_pos + 1:]:
                # Check predecessors for each block on the trace
                preds_here = set(pred_edges.get(later_bid, []))
                # When we find the block with more than one predecessor we assume it's a merging point
                if len(preds_here) > 1:
                    join_pos = trace_index[later_bid]
                    break

            # Form a set of blocks that are guarded by a header
            guarded_ids = set(trace_block_ids[header_pos + 1: join_pos])
            # Save control dependency for the block
            controlled[bid] = guarded_ids

        return controlled

    def is_control_dependent(self, header_op, op, controlled_block_ids):
        """
            Function checks whether header_op guards op
        """
        guarded = controlled_block_ids.get(header_op["block_id"])
        if guarded is None:
            return True
        if op["block_id"] == header_op["block_id"]:
            return True
        return op["block_id"] in guarded

    # Build dependency graph between operations.
    # Operations that don't have control dependency to a control flow block, won't be barriered.
    # Side-effecting operations (has_call) remain full barriers
    def build_dependency_graph(self, operations, controlled_block_ids=None):
        # Used defaultdict to avoid manual key existence checks
        preds = defaultdict(set)
        succs = defaultdict(set)

        if controlled_block_ids is None:
            controlled_block_ids = {}

        # Loop over all possible pairs, Opi always comes before Opj
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

    # Compute number of dependent descendants for each operation (basically length of its entire downstream cone)
    def compute_descendant_counts(self, operations, succs):
        # Iterative DFS function
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
    # For each operation calculate the length of its critical path
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
        """
            Function performs list scheduling algorithm on a given trace of operations.
            Number of units that perform operations can be set as a parameter.
            perds and succs contain control dependencies.
        """

        # compute critical path and count all descendants for all operations
        critical_path = self.compute_critical_path_scores(operations, succs)
        descendants = self.compute_descendant_counts(operations, succs)

        # id -> operation map
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
                all_preds_done = all(pred_id in scheduled_set and finish_time[pred_id] <= current_cycle for pred_id in preds[op["id"]])
                if all_preds_done:
                    ready.append(op["id"])

            if ready:
                # Sort by priority: critical path (desc), descendants (desc), original order (asc)
                ready.sort(key=lambda op_id: (-critical_path[op_id], -descendants[op_id], op_map[op_id]["original_index"]))

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
    def detect_instruction_movements(self, operations, scheduled_ops):
        movements = []

        # Make mappers id -> original index, block_id
        original_rank = {op["id"]: op["original_index"] for op in operations}
        original_block = {op["id"]: op["block_id"] for op in operations}

        # Sort by (schedule_cycle, original_index)
        ordered_schedule = sorted(scheduled_ops, key=lambda item: (item["schedule_cycle"], item["original_index"]))
        # map id -> rank (id -> order number of an instruction)
        scheduled_rank = {item["op_id"]: rank for rank, item in enumerate(ordered_schedule)}

        # item represents an operation
        for item in scheduled_ops:
            op_id = item["op_id"]
            orig_rank = original_rank[op_id]
            sched_rank = scheduled_rank[op_id]

            # Only a genuine, exact reordering counts as moved: the operation's stposition relative to the rest of the trace shifted earlier.
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

    # Function schedules trace with given id, and defined number of processing units
    def schedule_trace(self, trace_id=0, num_units=2):
        # Get all blocks on a given trace
        trace_blocks = self.get_trace_blocks(trace_id)

        # If trace is empty, return empty results
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

        # Grab all schedulable instructions on a trace
        operations = self.build_trace_operations(trace_id)

        # If there are no operations in a trace, return empty results
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

        # For every IF/WHILE/FOR block, mark what instructions are guarded by that block
        # That tells list scheduler what instructions are inside a branch and which are just instructions located right after the branch
        controlled_block_ids = self.compute_controlled_block_ids(trace_id)

        # For every pair of operations, checks RAW/WAR/WAW hazards (shared variables), control-dependence, and call side effects.
        # Produces preds/succs adjacency sets keyed by operation id.
        preds, succs = self.build_dependency_graph(operations, controlled_block_ids)

        # Perform list scheduling on the selected trace
        scheduled_instructions, makespan = self.list_schedule(operations, preds, succs, num_units)

        # Detect instruction movements for bookkeeping
        instruction_movements = self.detect_instruction_movements(operations, scheduled_instructions)

        # Return metadata for scheduled trace
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
