import ast
from dataclasses import dataclass, field
from typing import List, Tuple

# Dataclass annotation provides dunder method (like __init__, __repr__, etc.)
@dataclass
class Block:
    """
        BLock class representing a basic block
        Each block has a id and list of statements (lines of code basically)
    """
    id: int
    statements: List[str] = field(default_factory=list)


# CFG Builder class
class CFGBuilder:
    def __init__(self):
        # Stores every block object created in CFG
        self.blocks = []
        # Stores directed edges between blocks, currently as tuples
        self.edges = []
        # Auto ID for new blocks
        self.next_id = 0

    # Create a new block and add it to the global list of blocks
    def new_block(self, statements=None):
        b = Block(self.next_id, statements or [])
        self.next_id += 1
        self.blocks.append(b)
        return b

    # Add edge in a global list
    def add_edge(self, src, dst, label=""):
        self.edges.append((src.id, dst.id, label))

    # Grab a statement text as a string, if error is raised, it falls back to returning the AST class name
    def stmt_text(self, node):
        try:
            return ast.unparse(node)
        except Exception:
            return node.__class__.__name__

    # Append a statement to the current block
    def append_stmt(self, block, stmt):
        block.statements.append(self.stmt_text(stmt))

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
    def build_module(self, tree):
        entry = self.new_block(["ENTRY"])
        body_block = self.new_block()
        self.add_edge(entry, body_block)
        exits = self.build_statements(tree.body, body_block)
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
            prev_block.statements.append(f"IF {self.stmt_text(stmt.test)}")

            then_entry = self.new_block()
            else_entry = self.new_block()
            self.add_edge(prev_block, then_entry, "True")
            self.add_edge(prev_block, else_entry, "False")

            then_exits = self.build_statements(stmt.body, then_entry) if stmt.body else [then_entry]
            else_exits = self.build_statements(stmt.orelse, else_entry) if stmt.orelse else [else_entry]

            join = self.new_block(["JOIN"])
            for b in then_exits:
                self.add_edge(b, join)
            for b in else_exits:
                self.add_edge(b, join)
            print("IF Block")
            return [join]

        elif isinstance(stmt, ast.While):
            prev_block.statements.append(f"WHILE {self.stmt_text(stmt.test)}")

            body_entry = self.new_block()
            after_loop = self.new_block(["AFTER_LOOP"])
            self.add_edge(prev_block, body_entry, "True")
            self.add_edge(prev_block, after_loop, "False")

            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            for b in body_exits:
                self.add_edge(b, prev_block, "back")

            print("WHILE")
            return [after_loop]

        elif isinstance(stmt, ast.For):
            prev_block.statements.append(f"FOR {self.stmt_text(stmt.target)} in {self.stmt_text(stmt.iter)}")

            body_entry = self.new_block()
            after_loop = self.new_block(["AFTER_FOR"])
            self.add_edge(prev_block, body_entry, "next")
            self.add_edge(prev_block, after_loop, "done")

            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            for b in body_exits:
                self.add_edge(b, prev_block, "back")
            print("FOR")
            return [after_loop]

        elif isinstance(stmt, ast.Return):
            prev_block.statements.append(self.stmt_text(stmt))
            print("RETURN")
            return [prev_block]

        else:
            block = self.new_block([self.stmt_text(stmt)])
            self.add_edge(prev_block, block)
            return [block]

    """
        Creates dot file from CFG
    """
    def to_dot(self):
        lines = ["digraph CFG {", "  rankdir=TB;", "  node [shape=box, fontname=Helvetica];"]
        for b in self.blocks:
            label = "\\n".join(b.statements) if b.statements else f"B{b.id}"
            lines.append(f'  B{b.id} [label="B{b.id}: {label}"];')
        for s, d, lbl in self.edges:
            attr = f' [label="{lbl}"]' if lbl else ""
            lines.append(f"  B{s} -> B{d}{attr};")
        lines.append("}")
        return "\n".join(lines)

# Save CFG as a png
from graphviz import Source

def render_dot_to_png(dot_text, output_name="cfg"):
    src = Source(dot_text, format="png")
    src.render(output_name, cleanup=True)



def main():
    # input and output files
    # .dot format is used for representation of a CFG using Graphviz
    input_file = 'input.py'
    output_file = 'output.dot'

    # Open input file and read the script
    with open(input_file, 'r', encoding='utf-8') as f:
        source = f.read()

    # Tree building
    # Parse the python code and get AST (Abstract syntax tree) in return
    tree = ast.parse(source, filename=input_file)
    # Instantiate CFG builder class
    builder = CFGBuilder()
    # AST -> CFG
    builder.build_module(tree)
    # Create .dot file for displaying using Graphviz
    dot = builder.to_dot()

    render_dot_to_png(dot, "cfg_output")

    # Output the result CFG in a .dot format
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(dot)

if __name__ == '__main__':
    main()