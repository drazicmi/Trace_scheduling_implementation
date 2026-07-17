import ast    # Traverse and crete CFG
import copy    # Deepcopy tree
from dataclasses import dataclass, field    # Annotation that generates dunder methods (like __init__, __repr__, etc.)
from typing import List, Optional     # DataTypes used as fields in classes
from collections import defaultdict    # DataType that creates
import graphviz
from graphviz import Source     # CFG visualisation library

"""
        Python to CFG phase
        Script takes input.py file that contains following:
            1. global function with/without formal parameters (This code will be optimized)
            2. INPUT_TESTS: List that contains exactly 100 input parameters for a function 
        Firstly it simulates 100 executions of a program and calculates heuristic (probabilities of branching)
        Secondly it generate .dot file that describes the program and contains edges that are marked with calculated probabilities
        Lastly it generates .png that contains visual representation of .dot file using Graphviz library.
"""


# Dataclass annotation provides dunder method (like __init__, __repr__, etc.)
@dataclass
class Block:
    """
        BLock class representing a basic block
        Each block has an id and list of statements (lines of code basically)
    """

    id: int
    statements: List[str] = field(default_factory=list) # Used to store humanly readable statements
    # Field isn't used in this version, it is meant for machine-structured scheduling/tracing.
    # That type of implementation isn't provided here but the interface remains open to it.
    # This field would allow introduction of separate instruction format
    instructions: List[str] = field(default_factory=list)
    is_bookkeeping: bool = False    # Mark whether its bookkeeping block
    trace_id: Optional[int] = None    # Stores which trace the block belongs to later in the pipeline. None means it starts unset.


# Dataclass annotation provides dunder method (like __init__, __repr__, etc.)
@dataclass
class Edge:
    src: int    # SRC ID of a block
    dst: int    # DST ID of a block
    label: str = ""    # Store labels like True, False, back, next or done
    count: int = 0    # Store how many times edge was observed during profiling
    probability: Optional[float] = None    # Stores the branch probability derived from counts
    is_trace_edge: bool = False    # Flags for further phases use
    is_side_entry: bool = False    # Flags for further phases use
    is_side_exit: bool = False     # Flags for further phases use


class ControlIdAssigner(ast.NodeVisitor):
    """
        Service class that extends NodeVisitor class and handles ID assignment to control nodes
    """
    # Constructor
    def __init__(self):
        self.next_id = 0

    # Runs on ast.If nodes
    def visit_If(self, node):
        # It attaches a new custom attribute cfg_id to the AST node.
        # That ID links this AST control node to CFG edges and profiling data later.
        node.cfg_id = self.next_id
        self.next_id += 1
        # Continue default visit
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


class CFGBuilder:
    """
        CFG Builder class, it creates CFG from ast
    """
    def __init__(self):
        # Stores every block object created in CFG
        self.blocks = []
        # Stores directed edges between blocks, currently as tuples
        self.edges = []
        # Auto ID for new blocks
        self.next_id = 0
        # Dictionary for looking up edges by (src, dst, label).
        # It is useful for when direct access to a specific edge object later is required.
        self.edge_lookup = {}
        # Maps a control-node cfg_id to the corresponding outgoing CFG edges.
        # This is crucial for recording branch decisions during profiling.
        self.control_edges = {}

    # Create a new block and add it to the global list of blocks
    def new_block(self, statements=None):
        block_statements = statements or []
        # Both statements and instruction are initialized from same list
        b = Block(self.next_id, list(block_statements), list(block_statements))
        self.next_id += 1
        self.blocks.append(b)
        return b

    # Add edge in a global list
    def add_edge(self, src, dst, label=""):
        edge = Edge(src.id, dst.id, label)
        # Builds the edge object using block IDs and optional label
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

    def is_simple_statement(self, stmt):
        """
            Check whether statement is a straight-line statement that can stay in the current block.
            Returns False for control-flow statements that need special CFG handling.
        """
        return not isinstance(stmt, (ast.If, ast.While, ast.For, ast.Return))

    # Make sure the current block exists and can accept statements
    def ensure_block(self, block):
        if block is None:
            return self.new_block()
        return block

    def build_function(self, func_def):
        """
            MAIN of this class
            Func is used for building the whole tree. It starts with Entry block, then it builds CFG from tree
            After finishing the tree it creates Exit block
            At the end, it adds all the edges from final blocks to the exit block
        """
        # Create entry block and link it to body of function
        entry = self.new_block(["ENTRY"])
        body_block = self.new_block()
        self.add_edge(entry, body_block)

        # Build CFG fragments from body of a function
        exits = self.build_statements(func_def.body, body_block)

        # Link body with exit block
        exit_block = self.new_block(["EXIT"])
        for b in exits:
            self.add_edge(b, exit_block)
        return entry, exit_block

    def build_statements(self, stmts, start_block):
        """
            Processes a sequence of statements one by one.
            Start from one or more blocks where execution may currently be.
            For each next statement build the CFG fragment for that statement from each current exit and collect the new exits.
            It tracks all current possible exit blocks as control flow branches and rejoins.
        """
        # Starting block is current exit, we'll update the current_exits trough further iterations
        current_exits = [start_block]
        # Iterate through a list of statements
        for stmt in stmts:
            # Differentiate simple statement from control-flow statements.
            # If we recognize simple statement it stays in a block.
            if self.is_simple_statement(stmt):
                for i, b in enumerate(current_exits):
                    # For each current exit block, ensure it exists and append the statement into it
                    current_exits[i] = self.ensure_block(b)
                    self.append_stmt(current_exits[i], stmt)
            # Control-flow statements
            else:
                # If the statement is control-flow, the exits may change
                new_exits = []
                for b in current_exits:
                    b = self.ensure_block(b)
                    new_exits.extend(self.build_statement(stmt, b))
                # These are the blocks from which the next statement may start
                current_exits = new_exits

        # Return list of exit blocks at the end of statement list
        return current_exits

    def build_statement(self, stmt, prev_block):
        """
            Function handles logic behind processing a single non-simple statement and creates the nodes in the graph that describe that statement.
            There are few cases that are handled, including if, while, for, return and everything else that is not those four.
        """
        # Handle IF statement
        if isinstance(stmt, ast.If):
            # Build label IF[CFG_ID] CONDITION
            text = f"IF[{stmt.cfg_id}] {self.stmt_text(stmt.test)}"
            # Put statement to the current block
            prev_block.statements.append(text)
            prev_block.instructions.append(text)

            # Create separate entries for True and False branch
            then_entry = self.new_block()
            else_entry = self.new_block()
            # Add labeled edges
            true_edge = self.add_edge(prev_block, then_entry, "True")
            false_edge = self.add_edge(prev_block, else_entry, "False")

            # Save which edges correspond to this control decision for later profiling
            self.control_edges[stmt.cfg_id] = {
                "type": "if",
                "true": true_edge,
                "false": false_edge
            }

            # Build CFG for the then body, or keep the empty block if no body exists
            # Same for else body.
            then_exits = self.build_statements(stmt.body, then_entry) if stmt.body else [then_entry]
            else_exits = self.build_statements(stmt.orelse, else_entry) if stmt.orelse else [else_entry]

            # Create a join block where both branches merge.
            join = self.new_block(["JOIN"])
            for b in then_exits:
                self.add_edge(b, join)
            for b in else_exits:
                self.add_edge(b, join)
            return [join]

        # Handle WHILE statement
        elif isinstance(stmt, ast.While):
            # Build label While[CFG_ID] CONDITION
            text = f"WHILE[{stmt.cfg_id}] {self.stmt_text(stmt.test)}"
            # Put statement to the current block.
            prev_block.statements.append(text)
            prev_block.instructions.append(text)

            # Create one block for entering the loop body and one block for code after the loop
            body_entry = self.new_block()
            after_loop = self.new_block(["AFTER_LOOP"])
            # True means loop continues, false means exit loop
            true_edge = self.add_edge(prev_block, body_entry, "True")
            false_edge = self.add_edge(prev_block, after_loop, "False")
            # Save which edges correspond to this control decision for later profiling
            self.control_edges[stmt.cfg_id] = {
                "type": "while",
                "true": true_edge,
                "false": false_edge
            }

            # Build the loop body.
            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            # Every exit from the body returns to the loop header block as a back edge
            for b in body_exits:
                self.add_edge(b, prev_block, "back")

            # After the loop statement finishes, execution continues from the after-loop block
            return [after_loop]

        # Handle FOR statement
        elif isinstance(stmt, ast.For):
            # Build label FOR[CFG_ID] TARGET in ITERABLE
            text = f"FOR[{stmt.cfg_id}] {self.stmt_text(stmt.target)} in {self.stmt_text(stmt.iter)}"
            # Put that loop header into the current block
            prev_block.statements.append(text)
            prev_block.instructions.append(text)

            # Create body-entry and after-loop blocks
            body_entry = self.new_block()
            after_loop = self.new_block(["AFTER_FOR"])
            # next means another iteration exists, done means iteration is complete
            next_edge = self.add_edge(prev_block, body_entry, "next")
            done_edge = self.add_edge(prev_block, after_loop, "done")
            # Register those two edges for profiling
            self.control_edges[stmt.cfg_id] = {
                "type": "for",
                "true": next_edge,
                "false": done_edge
            }

            # Build the loop body
            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            # Connect body exits back to the loop header block
            for b in body_exits:
                self.add_edge(b, prev_block, "back")
            # Execution continues after the loop from after_loop
            return [after_loop]

        # Handle RETURN statement
        elif isinstance(stmt, ast.Return):
            # Convert return statement to text
            text = self.stmt_text(stmt)
            # Append statement to the current block
            prev_block.statements.append(text)
            prev_block.instructions.append(text)
            # Return the current block as the exit point.
            # This does not immediately isolate all later statements as unreachable,
            # it simply marks the block as an exit for the builder flow.
            return [prev_block]

        # handle OTHER non-simple statement types.
        # Creates a new block containing that statement, connects it from the previous block, and returns it as the new exit.
        else:
            block = self.new_block([self.stmt_text(stmt)])
            self.add_edge(prev_block, block)
            return [block]

    # Called at runtime when an instrumented branch or loop decision happens
    def record_control_decision(self, cfg_id, taken_true):
        # If the control ID is unknown, do nothing
        if cfg_id not in self.control_edges:
            return
        # Pick the edge corresponding to the observed outcome
        # Increment how many times that edge was taken
        edge = self.control_edges[cfg_id]["true"] if taken_true else self.control_edges[cfg_id]["false"]
        edge.count += 1

    # Converts raw edge counts into probabilities
    def finalize_probabilities(self):
        # Create a map from source block ID to total outgoing branch count
        outgoing_totals = defaultdict(int)
        # Sum the counts of all outgoing edges per source block
        for edge in self.edges:
            outgoing_totals[edge.src] += edge.count
        # For each edge, divide its count by the total outgoing count from the same source
        # That gives the branch probability
        for edge in self.edges:
            total = outgoing_totals[edge.src]
            if total > 0:
                edge.probability = edge.count / total

    def to_dot(self):
        """
            Creates dot file from CFG
        """
        # Start a directed graph named CFG.
        # rankdir=TB means top-to-bottom layout.
        # Nodes are drawn as boxes with Helvetica font.
        lines = ["digraph CFG {", "  rankdir=TB;", "  node [shape=box, fontname=Helvetica];"]
        # Process each block.
        for b in self.blocks:
            # Use all block statements as the visible node label.
            # If there are no statements, fall back to the block ID.
            label = "\\n".join(b.statements) if b.statements else f"B{b.id}"
            # If the block belongs to a selected trace, color it
            attrs = []
            if b.trace_id is not None:
                attrs.append('style="filled"')
                attrs.append('fillcolor="lightyellow"')
            # Join block attributes into one string
            attr_text = ", ".join(attrs)
            # Emit the DOT line for the block
            if attr_text:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}", {attr_text}];')
            else:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}"];')

        # Process each edge
        for edge in self.edges:
            # Edge labels may include both control-flow text and probability
            label_parts = []
            if edge.label:
                label_parts.append(edge.label)
            if edge.probability is not None:
                label_parts.append(f"{edge.probability:.2f}")
            # Add a DOT label if there is one
            attr_parts = []
            if label_parts:
                attr_parts.append(f'label="{" | ".join(label_parts)}"')
            # Trace edges are drawn red and thicker
            if edge.is_trace_edge:
                attr_parts.append('color="red"')
                attr_parts.append('penwidth=2.0')
            # Side entries or exits are blue and dashed
            elif edge.is_side_entry or edge.is_side_exit:
                attr_parts.append('color="blue"')
                attr_parts.append('style="dashed"')
            # Emit the DOT edge line
            attr = f' [{", ".join(attr_parts)}]' if attr_parts else ""
            lines.append(f"  B{edge.src} -> B{edge.dst}{attr};")
        # Close the graph and return the full DOT string
        lines.append("}")
        return "\n".join(lines)


class ProfilerTransformer(ast.NodeTransformer):
    """
        This class is the AST transformer that injects profiling calls into the target function so you can count how often each control decision is taken.
        For every if, it inserts __cfg_record(cfg_id, True) at the top of the then body and __cfg_record(cfg_id, False) at the top of the else body.
        For every while, it logs the True case whenever the loop body runs and logs the False case once when the loop terminates.
        For every for, it logs True on each iteration and False after iteration finishes.
        Those injected calls drive CFGBuilder.record_control_decision, which increments counts on the appropriate edges.
        It inherits from ast.NodeTransformer, which can replace or rewrite nodes.
    """
    # Handle nested nodes inside IF
    def visit_If(self, node):
        # First transform the nested contents
        self.generic_visit(node)

        # Build AST for the statement __cfg_record(cfg_id, True).
        # This will log that the true branch was taken.
        log_true = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(True)],
                keywords=[]
            )
        )
        # Build AST for logging the false branch
        log_false = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(False)],
                keywords=[]
            )
        )
        # Insert the true-logging call at the beginning of the if body
        node.body = [log_true] + node.body
        # Insert the false-logging call at the beginning of the else body
        node.orelse = [log_false] + node.orelse
        # Return the modified if
        return node

    # Handle nested nodes inside WHILE
    def visit_While(self, node):
        # First transform the nested contents
        self.generic_visit(node)

        # Save the original test and prepare a temporary variable name
        original_test = node.test
        test_name = f"__cfg_while_test_{node.cfg_id}"

        # Replace the loop condition with a named expression.
        # This lets the condition still be evaluated while being wrapped in a transformed form.
        node.test = ast.NamedExpr(
            target=ast.Name(id=test_name, ctx=ast.Store()),
            value=original_test
        )
        # Build the statement __cfg_record(cfg_id, True) for successful loop entry
        log_true = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(True)],
                keywords=[]
            )
        )

        # Insert that logger at the start of the loop body, so each taken iteration is counted
        node.body = [log_true] + node.body

        # Build a logging call for the false outcome when the loop stops
        log_false_after = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(False)],
                keywords=[]
            )
        )

        # Return both the modified loop and a trailing false logger.
        # Once the loop finishes, the false edge gets counted exactly once.
        return [node, log_false_after]

    # Handle nested nodes inside FOR
    def visit_For(self, node):
        # First transform the nested contents
        self.generic_visit(node)

        # Create a temporary variable name for this loop
        entered_name = f"__cfg_for_entered_{node.cfg_id}"

        # Insert a flag initialized to False before the loop
        init_flag = ast.Assign(
            targets=[ast.Name(id=entered_name, ctx=ast.Store())],
            value=ast.Constant(False)
        )

        # Build the runtime logger for taking another iteration
        log_next = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(True)],
                keywords=[]
            )
        )
        # Set the flag to True inside the loop once an iteration happens
        set_flag = ast.Assign(
            targets=[ast.Name(id=entered_name, ctx=ast.Store())],
            value=ast.Constant(True)
        )
        # Put these at the beginning of the loop body.
        node.body = [log_next, set_flag] + node.body

        # Build the logger for the loop-finished outcome.
        log_done = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__cfg_record", ctx=ast.Load()),
                args=[ast.Constant(node.cfg_id), ast.Constant(False)],
                keywords=[]
            )
        )
        # Return three AST pieces: initialize flag, run loop, then log done.
        # In practice, this records every entered iteration as true and loop completion as false.
        return [init_flag, node, log_done]


# Save CFG as a png
def render_dot_to_png(dot_text, output_name="cfg"):
    src = Source(dot_text, format="png")
    src.render(output_name, cleanup=True)


def call_with_input(func, one_input):
    # If the test case is a dictionary, call the function with keyword arguments.
    if isinstance(one_input, dict):
        return func(**one_input)
    # If it is a tuple, unpack as positional arguments.
    if isinstance(one_input, tuple):
        return func(*one_input)
    # If it is a list, unpack as positional arguments.
    if isinstance(one_input, list):
        return func(*one_input)
    # Otherwise pass it as a single argument.
    return func(one_input)


def compile_profiled_function(tree, function_name, builder):
    """
        Takes the AST, instruments it, compiles it, and returns the instrumented Python function object.
    """
    # Start with tree deepcopy. Changes made on instrumented_tree won't affect tree.
    instrumented_tree = copy.deepcopy(tree)

    # Instantiate transformer object and start the visiting of instrumented_tree
    transformer = ProfilerTransformer()
    # Visits applies the profiling logic
    instrumented_tree = transformer.visit(instrumented_tree)
    # Repair source-location metadata in the modified AST so Python can compile it correctly
    ast.fix_missing_locations(instrumented_tree)

    # Prepare the namespace where the instrumented code will execute.
    namespace = {}

    # Define the callback used by injected profiling code.
    # Each runtime control decision updates CFG edge counts.
    def __cfg_record(cfg_id, taken_true):
        builder.record_control_decision(cfg_id, taken_true)

    # Make the callback visible to the compiled code.
    namespace["__cfg_record"] = __cfg_record

    # Compile the instrumented AST into executable Python bytecode.
    code = compile(instrumented_tree, filename="<instrumented>", mode="exec")
    # Execute it so the function becomes defined inside namespace.
    exec(code, namespace)
    # Return the instrumented function object by name.
    return namespace[function_name]


def extract_target_function_and_inputs(tree):
    """
        Function parses input.py in order to extract target function and TEST_INPUTS
    """
    # Check whether input.py contains only one top level function
    function_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(function_defs) != 1:
        raise ValueError("input.py must contain exactly one top-level function.")

    # Save target function
    target_function = function_defs[0]

    # Extract TEST_INPUTS
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


# AST visualization
def ast_to_graphviz(node, graph=None, parent=None, counter=[0], depth=1, max_depth=None, skip_assign_names=("TEST_INPUTS",)):

    if graph is None:
        graph = graphviz.Digraph()
    node_id = str(counter[0])
    counter[0] += 1
    label = type(node).__name__
    if isinstance(node, ast.Name):
        label += f"\n{node.id}"
    graph.node(node_id, label)
    if parent is not None:
        graph.edge(parent, node_id)

    # To make TEST_INPUTS part of the graph smaller, add just that assign node, and skip all other assigns that are related
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in skip_assign_names for t in node.targets):
        stub_id = str(counter[0])
        counter[0] += 1
        graph.node(stub_id, "[data excluded]", shape="plaintext")
        graph.edge(node_id, stub_id)
        return graph

    # Depth limit reached: if this node still has children, add a single "..." placeholder so it's visually clear the tree was truncated here
    if max_depth is not None and depth >= max_depth:
        children = list(ast.iter_child_nodes(node))
        if children:
            stub_id = str(counter[0])
            counter[0] += 1
            graph.node(stub_id, "...", shape="plaintext")
            graph.edge(node_id, stub_id)
        return graph

    for child in ast.iter_child_nodes(node):
        ast_to_graphviz(child, graph, node_id, counter, depth + 1, max_depth, skip_assign_names)
    return graph


def build_profiled_cfg(input_file='input.py', output_dot=None, output_png=None):
    """
        Function that is used to handle one iteration of PythonToCFG. It's called both from main of this script and main.py
        Function takes input.py and builds output.dot and cfg_output.png.
    """
    # Open input file and read the script
    with open(input_file, 'r', encoding='utf-8') as f:
        source = f.read()

    # Tree building
    # Parse the python code and get AST (Abstract syntax tree) in return
    tree = ast.parse(source, filename=input_file)

    # Display AST
    g = ast_to_graphviz(tree)
    g.render("ast_tree", format="svg", cleanup=True)

    # Extract target function and TEST_INPUTS
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

    # Create an instrumented version of the function that records branch choices when executed.
    profiled_function = compile_profiled_function(tree, target_function.name, builder)

    # Run the instrumented function on all 100 inputs.
    # Each execution updates edge counts inside the builder.
    for one_input in test_inputs:
        call_with_input(profiled_function, one_input)

    # Convert counts to edge probabilities.
    builder.finalize_probabilities()

    # Convert instrumented tree into .dot format
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

    builder, entry_block, exit_block, dot = build_profiled_cfg(input_file=input_file, output_dot=output_file, output_png=output_png)


if __name__ == '__main__':
    main()
