"""Greedy trace selection from weighted CFG."""

from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional
from ..cfg.graph import CFGGraph, Edge


@dataclass
class Trace:
    """A selected execution trace through the CFG."""
    block_ids: List[int] = field(default_factory=list)
    side_exits: List[Edge] = field(default_factory=list)
    side_entrances: List[Edge] = field(default_factory=list)
    total_probability: float = 1.0


class TraceSelector:
    """Selects traces from a CFG using greedy highest-probability path selection."""
    
    def __init__(self, graph: CFGGraph):
        self.graph = graph
    
    def select_trace(self) -> Trace:
        """Select the most likely trace starting from ENTRY."""
        trace = Trace()
        visited: Set[int] = set()
        current_id = self.graph.entry_id
        probability = 1.0
        
        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            trace.block_ids.append(current_id)
            
            if current_id == self.graph.exit_id:
                break
            
            successors = self.graph.get_successors(current_id)
            if not successors:
                break
            
            best_edge: Optional[Edge] = None
            best_prob = -1.0
            
            for edge in successors:
                if edge.label == 'back':
                    continue
                if edge.probability > best_prob:
                    best_prob = edge.probability
                    best_edge = edge
            
            if best_edge is None:
                break
            
            probability *= best_edge.probability
            
            for edge in successors:
                if edge != best_edge:
                    trace.side_exits.append(edge)
            
            current_id = best_edge.dst
        
        trace.total_probability = probability
        self._find_side_entrances(trace)
        
        return trace
    
    def _find_side_entrances(self, trace: Trace) -> None:
        """Find edges entering the trace from outside (excluding the first block)."""
        trace_set = set(trace.block_ids)
        
        for block_id in trace.block_ids[1:]:
            for edge in self.graph.get_predecessors(block_id):
                if edge.src not in trace_set:
                    trace.side_entrances.append(edge)
    
    def get_all_paths(self, max_depth: int = 20) -> List[Trace]:
        """Enumerate all acyclic paths through the CFG up to max_depth."""
        paths: List[Trace] = []
        
        def dfs(current: int, path: List[int], prob: float, visited: Set[int]) -> None:
            if len(path) > max_depth:
                return
            
            path.append(current)
            visited.add(current)
            
            if current == self.graph.exit_id:
                trace = Trace(block_ids=list(path), total_probability=prob)
                paths.append(trace)
            else:
                successors = self.graph.get_successors(current)
                for edge in successors:
                    if edge.dst not in visited:
                        dfs(edge.dst, path, prob * edge.probability, visited)
            
            path.pop()
            visited.discard(current)
        
        dfs(self.graph.entry_id, [], 1.0, set())
        return paths
