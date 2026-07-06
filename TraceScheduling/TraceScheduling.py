import ast
from collections import defaultdict, deque


class VariableAccessCollector(ast.NodeVisitor):
    def __init__(self):
        self.reads = set()
        self.writes = set()
        self.has_call = False
        self.is_control = False
        self.op_type = "other"

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.reads.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self.writes.add(node.id)

    def visit_Assign(self, node):
        self.op_type = "assign"
        for target in node.targets:
            self.visit(target)
        self.visit(node.value)

    def visit_AugAssign(self, node):
        self.op_type = "assign"
        self.visit(node.target)
        self.visit(node.value)

    def visit_AnnAssign(self, node):
        self.op_type = "assign"
        self.visit(node.target)
        if node.value:
            self.visit(node.value)

    def visit_Return(self, node):
        self.op_type = "return"
        if node.value:
            self.visit(node.value)

    def visit_Call(self, node):
        self.has_call = True
        self.op_type = "call"
        self.generic_visit(node)

    def visit_Compare(self, node):
        if self.op_type == "other":
            self.op_type = "compare"
        self.generic_visit(node)

    def visit_BinOp(self, node):
        if isinstance(node.op, (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)):
            if self.op_type == "other":
                self.op_type = "muldiv"
        elif self.op_type == "other":
            self.op_type = "arith"
        self.generic_visit(node)


class TraceScheduler:

    # Constructor
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    # Return lits of blocks for given trace_id
    def get_trace_blocks(self, trace_id=0):
        return [block for block in self.blocks if block.trace_id == trace_id]

    # Collect
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

    def list_schedule(self, operations, preds, succs):
        indegree = {op["id"]: len(preds[op["id"]]) for op in operations}
        critical_path = self.compute_critical_path_scores(operations, succs)
        descendants = self.compute_descendant_counts(operations, succs)

        op_map = {op["id"]: op for op in operations}
        ready = [op["id"] for op in operations if indegree[op["id"]] == 0]
        scheduled_ids = []

        while ready:
            best_id = max(
                ready,
                key=lambda op_id: (
                    critical_path[op_id],
                    descendants[op_id],
                    -op_map[op_id]["original_index"]
                )
            )

            ready.remove(best_id)
            scheduled_ids.append(best_id)

            for succ in succs[best_id]:
                indegree[succ] -= 1
                if indegree[succ] == 0:
                    ready.append(succ)

        return [
            {
                "schedule_index": index,
                "op_id": op_id,
                "block_id": op_map[op_id]["block_id"],
                "instruction": op_map[op_id]["instruction"],
                "latency": op_map[op_id]["latency"],
                "critical_path": critical_path[op_id],
                "descendants": descendants[op_id],
                "original_index": op_map[op_id]["original_index"]
            }
            for index, op_id in enumerate(scheduled_ids)
        ]

    def schedule_trace(self, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        operations = self.build_trace_operations(trace_id)
        preds, succs = self.build_dependency_graph(operations)
        scheduled_instructions = self.list_schedule(operations, preds, succs)

        return {
            "trace_id": trace_id,
            "block_ids": [block.id for block in trace_blocks],
            "original_instruction_count": len(operations),
            "scheduled_instructions": scheduled_instructions,
            "schedule_length": len(scheduled_instructions)
        }