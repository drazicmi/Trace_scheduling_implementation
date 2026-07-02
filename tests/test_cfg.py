"""Tests for CFG construction."""

import ast
import pytest
from trace_scheduling.cfg.builder import CFGBuilder, Block
from trace_scheduling.cfg.graph import CFGGraph


def build_cfg(source: str) -> CFGGraph:
    """Helper to build CFG from source string."""
    tree = ast.parse(source)
    builder = CFGBuilder()
    builder.build_module(tree)
    return CFGGraph(builder)


class TestCFGBuilder:
    """Tests for CFGBuilder."""
    
    def test_simple_assignment(self):
        """Test CFG for simple assignment."""
        source = "x = 1"
        graph = build_cfg(source)
        
        assert len(graph.blocks) >= 3  # ENTRY, assignment, EXIT
        assert graph.blocks[0].statements == ["ENTRY"]
        assert graph.blocks[-1].statements == ["EXIT"]
    
    def test_if_else(self):
        """Test CFG for if-else statement."""
        source = """
if x > 0:
    y = 1
else:
    y = 2
"""
        graph = build_cfg(source)
        
        has_if = any("IF" in " ".join(b.statements) for b in graph.blocks)
        has_join = any("JOIN" in b.statements for b in graph.blocks)
        
        assert has_if
        assert has_join
        
        true_edges = [e for e in graph.edges if e.label == "True"]
        false_edges = [e for e in graph.edges if e.label == "False"]
        
        assert len(true_edges) >= 1
        assert len(false_edges) >= 1
    
    def test_while_loop(self):
        """Test CFG for while loop."""
        source = """
while x > 0:
    x = x - 1
"""
        graph = build_cfg(source)
        
        has_while = any("WHILE" in " ".join(b.statements) for b in graph.blocks)
        back_edges = [e for e in graph.edges if e.label == "back"]
        
        assert has_while
        assert len(back_edges) >= 1
    
    def test_for_loop(self):
        """Test CFG for for loop."""
        source = """
for i in range(10):
    x = i
"""
        graph = build_cfg(source)
        
        has_for = any("FOR" in " ".join(b.statements) for b in graph.blocks)
        next_edges = [e for e in graph.edges if e.label == "next"]
        done_edges = [e for e in graph.edges if e.label == "done"]
        
        assert has_for
        assert len(next_edges) >= 1
        assert len(done_edges) >= 1
    
    def test_break_statement(self):
        """Test that break correctly exits the loop."""
        source = """
while True:
    break
"""
        graph = build_cfg(source)
        
        break_edges = [e for e in graph.edges if e.label == "break"]
        assert len(break_edges) >= 1
        
        back_edges = [e for e in graph.edges if e.label == "back"]
        assert len(back_edges) == 0
    
    def test_continue_statement(self):
        """Test that continue correctly jumps to loop header."""
        source = """
for i in range(5):
    if i == 2:
        continue
    x = i
"""
        graph = build_cfg(source)
        
        continue_edges = [e for e in graph.edges if e.label == "continue"]
        assert len(continue_edges) >= 1


class TestCFGGraph:
    """Tests for CFGGraph wrapper."""
    
    def test_predecessors_successors(self):
        """Test predecessor and successor computation."""
        source = """
if x > 0:
    y = 1
"""
        graph = build_cfg(source)
        
        for block in graph.blocks[1:-1]:
            succs = graph.get_successors(block.id)
            for edge in succs:
                preds = graph.get_predecessors(edge.dst)
                assert any(p.src == block.id for p in preds)
    
    def test_entry_exit_ids(self):
        """Test entry and exit block identification."""
        source = "x = 1"
        graph = build_cfg(source)
        
        entry = graph.get_block(graph.entry_id)
        exit_block = graph.get_block(graph.exit_id)
        
        assert entry is not None
        assert exit_block is not None
        assert "ENTRY" in entry.statements
        assert "EXIT" in exit_block.statements
