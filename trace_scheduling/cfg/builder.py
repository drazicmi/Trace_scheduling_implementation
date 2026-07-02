"""CFG Builder: Constructs control flow graphs from Python AST."""

import ast
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class Block:
    """Basic block in a control flow graph."""
    id: int
    statements: List[str] = field(default_factory=list)


@dataclass
class LoopContext:
    """Tracks loop header and exit block for break/continue handling."""
    header: Block
    exit_block: Block


class CFGBuilder:
    """Builds a control flow graph from Python AST."""
    
    def __init__(self):
        self.blocks: List[Block] = []
        self.edges: List[Tuple[int, int, str]] = []
        self.next_id: int = 0
        self.loop_stack: List[LoopContext] = []

    def new_block(self, statements: Optional[List[str]] = None) -> Block:
        """Create a new block and add it to the CFG."""
        b = Block(self.next_id, statements or [])
        self.next_id += 1
        self.blocks.append(b)
        return b

    def add_edge(self, src: Block, dst: Block, label: str = "") -> None:
        """Add a directed edge between two blocks."""
        self.edges.append((src.id, dst.id, label))

    def stmt_text(self, node: ast.AST) -> str:
        """Convert an AST node to source text."""
        try:
            return ast.unparse(node)
        except Exception:
            return node.__class__.__name__

    def append_stmt(self, block: Block, stmt: ast.stmt) -> None:
        """Append a statement's text to a block."""
        block.statements.append(self.stmt_text(stmt))

    def is_simple_statement(self, stmt: ast.stmt) -> bool:
        """Check if statement can stay in current block (no control flow change)."""
        return not isinstance(stmt, (ast.If, ast.While, ast.For, ast.Return, ast.Break, ast.Continue))

    def ensure_block(self, block: Optional[Block]) -> Block:
        """Ensure block exists; create one if None."""
        if block is None:
            return self.new_block()
        return block

    def build_module(self, tree: ast.Module) -> Tuple[Block, Block]:
        """Build CFG from module AST. Returns (entry_block, exit_block)."""
        entry = self.new_block(["ENTRY"])
        body_block = self.new_block()
        self.add_edge(entry, body_block)
        exits = self.build_statements(tree.body, body_block)
        exit_block = self.new_block(["EXIT"])
        for b in exits:
            self.add_edge(b, exit_block)
        return entry, exit_block

    def build_statements(self, stmts: List[ast.stmt], start_block: Block) -> List[Block]:
        """Process a sequence of statements, returning exit blocks."""
        current_exits = [start_block]
        for stmt in stmts:
            if not current_exits:
                break
            if self.is_simple_statement(stmt):
                for i, b in enumerate(current_exits):
                    current_exits[i] = self.ensure_block(b)
                    self.append_stmt(current_exits[i], stmt)
            else:
                new_exits = []
                for b in current_exits:
                    b = self.ensure_block(b)
                    new_exits.extend(self.build_statement(stmt, b))
                current_exits = new_exits
        return current_exits

    def build_statement(self, stmt: ast.stmt, prev_block: Block) -> List[Block]:
        """Build CFG fragment for a single control-flow statement."""
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
            return [join]

        elif isinstance(stmt, ast.While):
            header = self.new_block([f"WHILE {self.stmt_text(stmt.test)}"])
            self.add_edge(prev_block, header)

            body_entry = self.new_block()
            after_loop = self.new_block()
            self.add_edge(header, body_entry, "True")
            self.add_edge(header, after_loop, "False")

            self.loop_stack.append(LoopContext(header=header, exit_block=after_loop))
            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            self.loop_stack.pop()

            for b in body_exits:
                self.add_edge(b, header, "back")
            return [after_loop]

        elif isinstance(stmt, ast.For):
            header = self.new_block([f"FOR {self.stmt_text(stmt.target)} in {self.stmt_text(stmt.iter)}"])
            self.add_edge(prev_block, header)

            body_entry = self.new_block()
            after_loop = self.new_block()
            self.add_edge(header, body_entry, "next")
            self.add_edge(header, after_loop, "done")

            self.loop_stack.append(LoopContext(header=header, exit_block=after_loop))
            body_exits = self.build_statements(stmt.body, body_entry) if stmt.body else [body_entry]
            self.loop_stack.pop()

            for b in body_exits:
                self.add_edge(b, header, "back")
            return [after_loop]

        elif isinstance(stmt, ast.Break):
            if self.loop_stack:
                ctx = self.loop_stack[-1]
                prev_block.statements.append("break")
                self.add_edge(prev_block, ctx.exit_block, "break")
            return []

        elif isinstance(stmt, ast.Continue):
            if self.loop_stack:
                ctx = self.loop_stack[-1]
                prev_block.statements.append("continue")
                self.add_edge(prev_block, ctx.header, "continue")
            return []

        elif isinstance(stmt, ast.Return):
            prev_block.statements.append(self.stmt_text(stmt))
            return [prev_block]

        else:
            block = self.new_block([self.stmt_text(stmt)])
            self.add_edge(prev_block, block)
            return [block]

    def to_dot(self, highlight_blocks: Optional[List[int]] = None) -> str:
        """Export CFG to Graphviz DOT format. Optionally highlight trace blocks."""
        highlight_set = set(highlight_blocks or [])
        lines = ["digraph CFG {", "  rankdir=TB;", "  node [shape=box, fontname=Helvetica];"]
        for b in self.blocks:
            label = "\\n".join(b.statements) if b.statements else f"B{b.id}"
            style = ', style=filled, fillcolor=lightyellow' if b.id in highlight_set else ''
            lines.append(f'  B{b.id} [label="B{b.id}: {label}"{style}];')
        for s, d, lbl in self.edges:
            attr = f' [label="{lbl}"]' if lbl else ""
            lines.append(f"  B{s} -> B{d}{attr};")
        lines.append("}")
        return "\n".join(lines)
