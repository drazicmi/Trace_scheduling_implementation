import ast
import copy
from dataclasses import dataclass, field
from typing import List, Optional
from collections import defaultdict

# Dataclass annotation provides dunder method (like __init__, __repr__, etc.)
@dataclass
class Block:
    """
        BLock class representing a basic block
        Each block has a id and list of statements (lines of code basically)
    """
    id: int
    statements: List[str] = field(default_factory=list)
    instructions: List[str] = field(default_factory=list)
    is_bookkeeping: bool = False
    trace_id: Optional[int] = None


@dataclass
class Edge:
    src: int
    dst: int
    label: str = ""
    count: int = 0
    probability: Optional[float] = None
    is_trace_edge: bool = False
    is_side_entry: bool = False
    is_side_exit: bool = False


"""
    Service class that extends NodeVisitor class and handles ID assignment to control nodes
"""
class ControlIdAssigner(ast.NodeVisitor):
    def __init__(self):
        self.next_id = 0

    # Runs on ast.If nodes
    def visit_If(self, node):
        node.cfg_id = self.next_id
        self.next_id += 1
        self.generic_visit(node)

    # Runs on ast.While nodes
    def visit_While(self, node):
        node.cfg_id = self.next_id
        self.next_id += 1
        self.generic_visit(node)

    # Runs on ast.For nodes
    def visit_For(self, node):
        node.cfg_id = self.next_id
        self.next_id += 1
        self.generic_visit(node)


# CFG Builder class
class CFGBuilder:
    def __init__(self):
        # Stores every block object created in CFG
        self.blocks = []
        # Stores directed edges between blocks, currently as tuples
        self.edges = []
        # Auto ID for new blocks
        self.next_id = 0
        self.edge_lookup = {}
        self.control_edges = {}

    # Create a new block and add it to the global list of blocks
    def new_block(self, statements=None):
        block_statements = statements or []
        b = Block(self.next_id, list(block_statements), list(block_statements))
        self.next_id += 1
        self.blocks.append(b)
        return b

    # Add edge in a global list
    def add_edge(self, src, dst, label=""):
        edge = Edge(src.id, dst.id, label)
        self.edge_lookup[(src.id, dst.id, label)] = edge
        self.edges.append(edge)
        return edge

    # Grab a statement text as a string, if error is raised, it falls back to returning the AST class name
    def stmt_text(self, node):
        try:
            return ast.unparse(node)
        except Exception:
            return node.__class__.__name__

    # Append a statement to the current block
    def append_stmt(self, block, stmt):
        text = self.stmt_text(stmt)
        block.statements.append(text)
        block.instructions.append(text)

    # Check whether statement is a straight-line statement that can stay in the current block
    def is_simple_statement(self, stmt):
        return not isinstance(stmt, (ast.If, ast.While, ast.For, ast.Return))

    # Make sure the current block exists and can accept statements
    def ensure_block(self, block):
        if block is None:
            return self.new_block()
        return block

    """ 
        MAIN of this class
        Func is used for building the whole tree. It starts with Entry block, then it builds CFG from tree
        After finishing the tree it creates Exit block
        At the end, it adds all the edges from final blocks to the exit block
    """
    def build_function(self, func_def):
        entry = self.new_block(["ENTRY"])
        body_block = self.new_block()
        self.add_edge(entry, body_block)
        exits = self.build_statements(func_def.body, body_block)
        exit_block = self.new_block(["EXIT"])
        for b in exits:
            self.add_edge(b, exit_block)
        return entry, exit_block

    """
        Processes a sequence of statements one by one.
        Start from one or more blocks where execution may currently be.
        For each next statement build the CFG fragment for that statement from each current exit and collect the new exits
    """
    def build_statements(self, stmts, start_block):
        current_exits = [start_block]
        for stmt in stmts:
            if self.is_simple_statement(stmt):
                for i, b in enumerate(current_exits):
                    current_exits[i] = self.ensure_block(b)
                    self.append_stmt(current_exits[i], stmt)
            else:
                new_exits = []
                for b in current_exits:
                    b = self.ensure_block(b)
                    new_exits.extend(self.build_statement(stmt, b))
                # These are the blocks from which the next statement may start
                current_exits = new_exits
        return current_exits

    """
        Function handles logic behind processing a single statement and creates the nodes in the graph that describe that statement.
        There are few cases that are handled, including if, while, for, return and everything else that is not those four.
    """
    def build_statement(self, stmt, prev_block):
        if isinstance(stmt, ast.If):
            text = f"IF[{stmt.cfg_id}] {self.stmt_text(stmt.test)}"
            prev_block.statements.append(text)
            prev_block.instructions.append(text)

            then_entry = self.new_block()
            else_entry = self.new_block()
            true_edge = self.add_edge(prev_block, then_entry, "True")
            false_edge = self.add_edge(prev_block, else_entry, "False")
            self.control_edges[stmt.cfg_id] = {
                "type": "if",
                "true": true_edge,
                "false": false_edge
            }

            then_exits = self.build_statements(stmt.body, then_entry) if stmt.body else [then_entry]
            else_exits = self.build_statements(stmt.orelse, else_entry) if stmt.orelse else [else_entry]

            join = self.new_block(["JOIN"])
            for b in then_exits:
                self.add_edge(b, join)
            for b in else_exits:
                self.add_edge(b, join)
            return [join]

        elif isinstance(stmt, ast.While):
            text = f"WHILE[{stmt.cfg_id}] {self.stmt_text(stmt.test)}"
            prev_block.statements.append(text)
            prev_block.instructions.append(text)

            body_entry = self.new_block()
            after_loop = self.new_block(["AFTER_LOOP"])
            true_edge = self.add_edge(prev_block, body_entry, "True")
            false_edge = self.add_edge(prev_block, after_loop, "False")
            self.control_edges[stmt.cfg_id] = {
                "type": "while",
                "true": true_edge,
                "false": false_edge
            }

            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            for b in body_exits:
                self.add_edge(b, prev_block, "back")

            return [after_loop]

        elif isinstance(stmt, ast.For):
            text = f"FOR[{stmt.cfg_id}] {self.stmt_text(stmt.target)} in {self.stmt_text(stmt.iter)}"
            prev_block.statements.append(text)
            prev_block.instructions.append(text)

            body_entry = self.new_block()
            after_loop = self.new_block(["AFTER_FOR"])
            next_edge = self.add_edge(prev_block, body_entry, "next")
            done_edge = self.add_edge(prev_block, after_loop, "done")
            self.control_edges[stmt.cfg_id] = {
                "type": "for",
                "true": next_edge,
                "false": done_edge
            }

            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            for b in body_exits:
                self.add_edge(b, prev_block, "back")
            return [after_loop]

        elif isinstance(stmt, ast.Return):
            text = self.stmt_text(stmt)
            prev_block.statements.append(text)
            prev_block.instructions.append(text)
            return [prev_block]

        else:
            block = self.new_block([self.stmt_text(stmt)])
            self.add_edge(prev_block, block)
            return [block]

    def record_control_decision(self, cfg_id, taken_true):
        if cfg_id not in self.control_edges:
            return
        edge = self.control_edges[cfg_id]["true"] if taken_true else self.control_edges[cfg_id]["false"]
        edge.count += 1

    def finalize_probabilities(self):
        outgoing_totals = defaultdict(int)
        for edge in self.edges:
            outgoing_totals[edge.src] += edge.count

        for edge in self.edges:
            total = outgoing_totals[edge.src]
            if total > 0:
                edge.probability = edge.count / total

    """
        Creates dot file from CFG
    """
    def to_dot(self):
        lines = ["digraph CFG {", "  rankdir=TB;", "  node [shape=box, fontname=Helvetica];"]
        for b in self.blocks:
            label = "\\n".join(b.statements) if b.statements else f"B{b.id}"
            attrs = []
            if b.trace_id is not None:
                attrs.append('style="filled"')
                attrs.append('fillcolor="lightyellow"')
            attr_text = ", ".join(attrs)
            if attr_text:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}", {attr_text}];')
            else:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}"];')

        for edge in self.edges:
            label_parts = []
            if edge.label:
                label_parts.append(edge.label)
            if edge.probability is not None:
                label_parts.append(f"{edge.probability:.2f}")

            attr_parts = []
            if label_parts:
                attr_parts.append(f'label="{" | ".join(label_parts)}"')
            if edge.is_trace_edge:
                attr_parts.append('color="red"')
                attr_parts.append('penwidth=2.0')
            elif edge.is_side_entry or edge.is_side_exit:
                attr_parts.append('color="blue"')
                attr_parts.append('style="dashed"')

            attr = f' [{", ".join(attr_parts)}]' if attr_parts else ""
            lines.append(f"  B{edge.src} -> B{edge.dst}{attr};")
        lines.append("}")
        return "\n".join(lines)


class ProfilerTransformer(ast.NodeTransformer):
    def visit_If(self, node):
        self.generic_visit(node)

        log_true = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(True)],
                keywords=[]
            )
        )
        log_false = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(False)],
                keywords=[]
            )
        )

        node.body = [log_true] + node.body
        node.orelse = [log_false] + node.orelse
        return node

    def visit_While(self, node):
        self.generic_visit(node)

        original_test = node.test
        test_name = f"__cfg_while_test_{node.cfg_id}"

        node.test = ast.NamedExpr(
            target=ast.Name(id=test_name, ctx=ast.Store()),
            value=original_test
        )

        log_true = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(True)],
                keywords=[]
            )
        )

        node.body = [log_true] + node.body

        log_false_after = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(False)],
                keywords=[]
            )
        )

        return [node, log_false_after]

    def visit_For(self, node):
        self.generic_visit(node)

        entered_name = f"__cfg_for_entered_{node.cfg_id}"

        init_flag = ast.Assign(
            targets=[ast.Name(id=entered_name, ctx=ast.Store())],
            value=ast.Constant(False)
        )

        log_next = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(True)],
                keywords=[]
            )
        )

        set_flag = ast.Assign(
            targets=[ast.Name(id=entered_name, ctx=ast.Store())],
            value=ast.Constant(True)
        )

        node.body = [log_next, set_flag] + node.body

        log_done = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(False)],
                keywords=[]
            )
        )

        return [init_flag, node, log_done]


"""
    Function parses input.py in order to extract target function and TEST_INPUTS
"""
def extract_target_function_and_inputs(tree):
    # Check whether input.py contains only one top level function
    function_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(function_defs) != 1:
        raise ValueError("input.py must contain exactly one top-level function.")

    # Save target function and extract TEST_INPUTS
    target_function = function_defs[0]
    test_inputs = None

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TEST_INPUTS":
                    test_inputs = ast.literal_eval(node.value)

    # Raise an error if TEST_INPUTS importing fails
    if test_inputs is None:
        raise ValueError("input.py must define TEST_INPUTS.")
    if not isinstance(test_inputs, list):
        raise ValueError("TEST_INPUTS must be a list.")
    if len(test_inputs) != 100:
        raise ValueError("TEST_INPUTS must contain exactly 100 manual inputs.")

    return target_function, test_inputs


def compile_profiled_function(tree, function_name, builder):
    instrumented_tree = copy.deepcopy(tree)

    transformer = ProfilerTransformer()
    instrumented_tree = transformer.visit(instrumented_tree)
    ast.fix_missing_locations(instrumented_tree)

    namespace = {}

    def __cfg_record(cfg_id, taken_true):
        builder.record_control_decision(cfg_id, taken_true)

    namespace["__cfg_record"] = __cfg_record

    code = compile(instrumented_tree, filename="<instrumented>", mode="exec")
    exec(code, namespace)
    return namespace[function_name]


# Save CFG as a png
from graphviz import Source

def render_dot_to_png(dot_text, output_name="cfg"):
    src = Source(dot_text, format="png")
    src.render(output_name, cleanup=True)


def call_with_input(func, one_input):
    if isinstance(one_input, dict):
        return func(**one_input)
    if isinstance(one_input, tuple):
        return func(*one_input)
    if isinstance(one_input, list):
        return func(*one_input)
    return func(one_input)


def build_profiled_cfg(input_file='input.py', output_dot=None, output_png=None):
    # Open input file and read the script
    with open(input_file, 'r', encoding='utf-8') as f:
        source = f.read()

    # Tree building
    # Parse the python code and get AST (Abstract syntax tree) in return
    tree = ast.parse(source, filename=input_file)

    # Extract target funtion and TEST_INPUTS
    target_function, test_inputs = extract_target_function_and_inputs(tree)

    # Instantiates a visitor that walks the AST and assigns a unique cfg_id integer to each control-flow node (If, While, For).
    # This gives you a stable ID per control node that will be shared between the CFG and the instrumented version, so both sides can talk about “branch #3” or “loop #5” consistently.
    assigner = ControlIdAssigner()
    # Traverses the AST and attaches node.cfg_id = ... to every If, While, and For.
    # After this, the function body in tree has identifiers you can use in your CFG builder.
    assigner.visit(tree)

    # Instantiate CFG builder class
    builder = CFGBuilder()
    # AST -> CFG
    entry_block, exit_block = builder.build_function(target_function)

    profiled_function = compile_profiled_function(tree, target_function.name, builder)

    for one_input in test_inputs:
        call_with_input(profiled_function, one_input)

    builder.finalize_probabilities()

    dot = builder.to_dot()

    if output_png:
        render_dot_to_png(dot, output_png)

    if output_dot:
        with open(output_dot, 'w', encoding='utf-8') as f:
            f.write(dot)

    return builder, entry_block, exit_block, dot


def main():
    # input and output files
    # .dot format is used for representation of a CFG using Graphviz
    input_file = 'input.py'
    output_file = 'output.dot'
    output_png = 'cfg_output'

    builder, entry_block, exit_block, dot = build_profiled_cfg(
        input_file=input_file,
        output_dot=output_file,
        output_png=output_png
    )


if __name__ == '__main__':
    main()