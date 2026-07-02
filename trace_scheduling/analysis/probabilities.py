"""Branch probability heuristics for CFG edges."""

import ast
import json
import re
from typing import Dict, Optional, List, Tuple
from ..cfg.graph import CFGGraph, Edge
from ..cfg.builder import Block


class BranchProbabilityAnalyzer:
    """Assigns branch probabilities to CFG edges using heuristics or profile data."""
    
    DEFAULT_IF_TRUE_PROB = 0.7
    DEFAULT_IF_FALSE_PROB = 0.3
    
    def __init__(self, graph: CFGGraph, profile_path: Optional[str] = None):
        self.graph = graph
        self.profile: Dict[str, float] = {}
        
        if profile_path:
            self._load_profile(profile_path)
    
    def _load_profile(self, path: str) -> None:
        """Load edge probabilities from profile JSON."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.profile = data.get("edges", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    
    def _edge_key(self, src: int, dst: int) -> str:
        """Create edge key for profile lookup."""
        return f"B{src}->B{dst}"
    
    def _parse_range_trip_count(self, block: Block) -> Optional[int]:
        """Extract trip count from 'FOR x in range(N)' patterns."""
        for stmt in block.statements:
            match = re.search(r'FOR\s+\w+\s+in\s+range\((\d+)(?:,\s*(\d+))?\)', stmt)
            if match:
                if match.group(2):
                    start = int(match.group(1))
                    end = int(match.group(2))
                    return max(0, end - start)
                else:
                    return int(match.group(1))
        return None
    
    def _is_while_true(self, block: Block) -> bool:
        """Check if block is a 'while True' header."""
        for stmt in block.statements:
            if 'WHILE True' in stmt or 'WHILE 1' in stmt:
                return True
        return False
    
    def analyze(self) -> None:
        """Assign probabilities to all edges based on heuristics and profile."""
        for block in self.graph.blocks:
            successors = self.graph.get_successors(block.id)
            
            if len(successors) == 0:
                continue
            
            if len(successors) == 1:
                edge = successors[0]
                profile_key = self._edge_key(edge.src, edge.dst)
                prob = self.profile.get(profile_key, 1.0)
                self.graph.set_edge_probability(edge.src, edge.dst, prob)
                continue
            
            self._analyze_branch(block, successors)
    
    def _analyze_branch(self, block: Block, successors: List[Edge]) -> None:
        """Analyze and assign probabilities for a branching block."""
        stmt_text = " ".join(block.statements)
        
        if 'FOR ' in stmt_text:
            self._analyze_for_loop(block, successors)
        elif 'WHILE ' in stmt_text:
            self._analyze_while_loop(block, successors)
        elif 'IF ' in stmt_text:
            self._analyze_if_branch(block, successors)
        else:
            self._assign_uniform(successors)
    
    def _analyze_for_loop(self, block: Block, successors: List[Edge]) -> None:
        """Assign probabilities for a for-loop header."""
        trip_count = self._parse_range_trip_count(block)
        
        if trip_count is not None and trip_count > 0:
            back_prob = trip_count / (trip_count + 1)
            exit_prob = 1 / (trip_count + 1)
        else:
            back_prob = 0.9
            exit_prob = 0.1
        
        for edge in successors:
            profile_key = self._edge_key(edge.src, edge.dst)
            if profile_key in self.profile:
                self.graph.set_edge_probability(edge.src, edge.dst, self.profile[profile_key])
            elif edge.label in ('next', 'back'):
                self.graph.set_edge_probability(edge.src, edge.dst, back_prob)
            elif edge.label == 'done':
                self.graph.set_edge_probability(edge.src, edge.dst, exit_prob)
    
    def _analyze_while_loop(self, block: Block, successors: List[Edge]) -> None:
        """Assign probabilities for a while-loop header."""
        if self._is_while_true(block):
            for edge in successors:
                profile_key = self._edge_key(edge.src, edge.dst)
                if profile_key in self.profile:
                    self.graph.set_edge_probability(edge.src, edge.dst, self.profile[profile_key])
                elif edge.label == 'True':
                    self.graph.set_edge_probability(edge.src, edge.dst, 0.99)
                elif edge.label == 'False':
                    self.graph.set_edge_probability(edge.src, edge.dst, 0.01)
        else:
            back_prob = 0.9
            exit_prob = 0.1
            for edge in successors:
                profile_key = self._edge_key(edge.src, edge.dst)
                if profile_key in self.profile:
                    self.graph.set_edge_probability(edge.src, edge.dst, self.profile[profile_key])
                elif edge.label == 'True':
                    self.graph.set_edge_probability(edge.src, edge.dst, back_prob)
                elif edge.label == 'False':
                    self.graph.set_edge_probability(edge.src, edge.dst, exit_prob)
    
    def _analyze_if_branch(self, block: Block, successors: List[Edge]) -> None:
        """Assign probabilities for an if-statement."""
        for edge in successors:
            profile_key = self._edge_key(edge.src, edge.dst)
            if profile_key in self.profile:
                self.graph.set_edge_probability(edge.src, edge.dst, self.profile[profile_key])
            elif edge.label == 'True':
                self.graph.set_edge_probability(edge.src, edge.dst, self.DEFAULT_IF_TRUE_PROB)
            elif edge.label == 'False':
                self.graph.set_edge_probability(edge.src, edge.dst, self.DEFAULT_IF_FALSE_PROB)
    
    def _assign_uniform(self, successors: List[Edge]) -> None:
        """Assign uniform probabilities to edges."""
        prob = 1.0 / len(successors) if successors else 1.0
        for edge in successors:
            profile_key = self._edge_key(edge.src, edge.dst)
            if profile_key in self.profile:
                self.graph.set_edge_probability(edge.src, edge.dst, self.profile[profile_key])
            else:
                self.graph.set_edge_probability(edge.src, edge.dst, prob)
