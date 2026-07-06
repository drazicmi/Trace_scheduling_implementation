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
            return {
                "text": instruction_text,
                "reads": set(),
                "writes": set(),
                "latency": 1,
                "op_type": "other",
                "is_control": False,
                "has_call": False
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
    def build_dependency_graph(self, operations):
        preds = defaultdict(set)
        succs = defaultdict(set)

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
                elif op_i["is_control"] or op_j["is_control"]:
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

    # Detect which instructions moved across block boundaries
    def detect_instruction_movements(self, operations, scheduled_ops):
        movements = []
        op_map = {op["id"]: op for op in operations}
        
        # Build original order: (block_id, position within block)
        original_order = {}
        for op in operations:
            original_order[op["id"]] = (op["block_id"], op["original_index"])
        
        # Check each pair of scheduled operations
        for i, sched_i in enumerate(scheduled_ops):
            for j, sched_j in enumerate(scheduled_ops):
                if i >= j:
                    continue
                
                op_i_block, op_i_pos = original_order[sched_i["op_id"]]
                op_j_block, op_j_pos = original_order[sched_j["op_id"]]
                
                # Check if relative order changed
                originally_i_before_j = (op_i_block < op_j_block) or (op_i_block == op_j_block and op_i_pos < op_j_pos)
                
                # In schedule: i before j (since i < j in the loop)
                scheduled_i_before_j = sched_i["schedule_cycle"] < sched_j["schedule_cycle"] or \
                                       (sched_i["schedule_cycle"] == sched_j["schedule_cycle"])
                
                # If an instruction from a later block is now scheduled before one from an earlier block
                if op_j_block < op_i_block and sched_i["schedule_cycle"] <= sched_j["schedule_cycle"]:
                    movements.append({
                        "type": "moved_earlier",
                        "instruction": sched_i["instruction"],
                        "from_block": op_i_block,
                        "originally_after_block": op_j_block,
                        "scheduled_cycle": sched_i["schedule_cycle"]
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
        
        preds, succs = self.build_dependency_graph(operations)
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