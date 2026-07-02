"""Tests for trace selection."""

import ast
import pytest
from trace_scheduling.cfg.builder import CFGBuilder
from trace_scheduling.cfg.graph import CFGGraph
from trace_scheduling.analysis.probabilities import BranchProbabilityAnalyzer
from trace_scheduling.trace.selector import TraceSelector, Trace


def build_and_analyze(source: str) -> CFGGraph:
    """Build CFG and analyze probabilities."""
    tree = ast.parse(source)
    builder = CFGBuilder()
    builder.build_module(tree)
    graph = CFGGraph(builder)
    
    analyzer = BranchProbabilityAnalyzer(graph)
    analyzer.analyze()
    
    return graph


class TestTraceSelector:
    """Tests for TraceSelector."""
    
    def test_sequential_trace(self):
        """Test trace selection for sequential code."""
        source = """
x = 1
y = 2
z = 3
"""
        graph = build_and_analyze(source)
        selector = TraceSelector(graph)
        trace = selector.select_trace()
        
        assert graph.entry_id in trace.block_ids
        assert graph.exit_id in trace.block_ids
        assert trace.total_probability == 1.0
    
    def test_if_dominant_branch_selected(self):
        """Test that dominant (True) branch is selected for if statement."""
        source = """
if x > 0:
    y = 1
else:
    y = 2
"""
        graph = build_and_analyze(source)
        selector = TraceSelector(graph)
        trace = selector.select_trace()
        
        true_block_in_trace = False
        false_block_in_trace = False
        
        for block_id in trace.block_ids:
            block = graph.get_block(block_id)
            if block and "y = 1" in block.statements:
                true_block_in_trace = True
            if block and "y = 2" in block.statements:
                false_block_in_trace = True
        
        assert true_block_in_trace
    
    def test_side_exits_identified(self):
        """Test that side exits are correctly identified."""
        source = """
if x > 0:
    y = 1
else:
    y = 2
"""
        graph = build_and_analyze(source)
        selector = TraceSelector(graph)
        trace = selector.select_trace()
        
        assert len(trace.side_exits) >= 1
    
    def test_join_bookkeeping_side_entrances(self):
        """Test side entrance detection for join bookkeeping case."""
        source = """
cond = True
if cond:
    b = 1
else:
    c = 2
d = 10
"""
        graph = build_and_analyze(source)
        selector = TraceSelector(graph)
        trace = selector.select_trace()
        
        join_block_in_trace = any(
            "JOIN" in graph.get_block(bid).statements
            for bid in trace.block_ids
            if graph.get_block(bid)
        )
        
        if join_block_in_trace:
            assert len(trace.side_entrances) >= 1 or len(trace.side_exits) >= 1


class TestTracePaths:
    """Tests for path enumeration."""
    
    def test_all_paths_enumeration(self):
        """Test that all paths are found."""
        source = """
if x > 0:
    y = 1
else:
    y = 2
"""
        graph = build_and_analyze(source)
        selector = TraceSelector(graph)
        paths = selector.get_all_paths()
        
        assert len(paths) == 2
    
    def test_path_probabilities_sum_to_one(self):
        """Test that path probabilities approximately sum to 1."""
        source = """
if x > 0:
    y = 1
else:
    y = 2
"""
        graph = build_and_analyze(source)
        selector = TraceSelector(graph)
        paths = selector.get_all_paths()
        
        total_prob = sum(p.total_probability for p in paths)
        assert abs(total_prob - 1.0) < 0.01
