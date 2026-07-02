"""Tests for branch probability analysis."""

import ast
import pytest
from trace_scheduling.cfg.builder import CFGBuilder
from trace_scheduling.cfg.graph import CFGGraph
from trace_scheduling.analysis.probabilities import BranchProbabilityAnalyzer


def build_cfg(source: str) -> CFGGraph:
    """Helper to build CFG from source string."""
    tree = ast.parse(source)
    builder = CFGBuilder()
    builder.build_module(tree)
    return CFGGraph(builder)


class TestBranchProbabilities:
    """Tests for BranchProbabilityAnalyzer."""
    
    def test_if_default_probabilities(self):
        """Test default if-else probabilities (0.7/0.3)."""
        source = """
if x > 0:
    y = 1
else:
    y = 2
"""
        graph = build_cfg(source)
        analyzer = BranchProbabilityAnalyzer(graph)
        analyzer.analyze()
        
        for block in graph.blocks:
            if "IF" in " ".join(block.statements):
                succs = graph.get_successors(block.id)
                probs = {e.label: e.probability for e in succs}
                
                if "True" in probs:
                    assert abs(probs["True"] - 0.7) < 0.01
                if "False" in probs:
                    assert abs(probs["False"] - 0.3) < 0.01
    
    def test_for_loop_trip_count(self):
        """Test for loop probability based on trip count."""
        source = """
for i in range(10):
    x = i
"""
        graph = build_cfg(source)
        analyzer = BranchProbabilityAnalyzer(graph)
        analyzer.analyze()
        
        for block in graph.blocks:
            if "FOR" in " ".join(block.statements) and "range(10)" in " ".join(block.statements):
                succs = graph.get_successors(block.id)
                
                for edge in succs:
                    if edge.label == "next":
                        expected = 10 / 11
                        assert abs(edge.probability - expected) < 0.01
                    elif edge.label == "done":
                        expected = 1 / 11
                        assert abs(edge.probability - expected) < 0.01
    
    def test_while_true_probability(self):
        """Test while True gets very high body probability."""
        source = """
while True:
    x = 1
    break
"""
        graph = build_cfg(source)
        analyzer = BranchProbabilityAnalyzer(graph)
        analyzer.analyze()
        
        for block in graph.blocks:
            if "WHILE True" in " ".join(block.statements):
                succs = graph.get_successors(block.id)
                for edge in succs:
                    if edge.label == "True":
                        assert edge.probability >= 0.9


class TestBlockFrequencies:
    """Tests for block frequency computation."""
    
    def test_entry_frequency_is_one(self):
        """Test that ENTRY block has frequency 1.0."""
        source = "x = 1"
        graph = build_cfg(source)
        analyzer = BranchProbabilityAnalyzer(graph)
        analyzer.analyze()
        
        freq = graph.compute_block_frequencies()
        assert abs(freq[graph.entry_id] - 1.0) < 0.001
    
    def test_sequential_frequency_preserved(self):
        """Test frequencies along sequential path."""
        source = """
x = 1
y = 2
z = 3
"""
        graph = build_cfg(source)
        analyzer = BranchProbabilityAnalyzer(graph)
        analyzer.analyze()
        
        freq = graph.compute_block_frequencies()
        
        for block in graph.blocks:
            if "ENTRY" not in block.statements and "EXIT" not in block.statements:
                assert freq[block.id] > 0
