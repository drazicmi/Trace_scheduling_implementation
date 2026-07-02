"""CFG Graph wrapper with adjacency and analysis utilities."""

from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple
from .builder import Block, CFGBuilder


@dataclass
class Edge:
    """Directed edge in a CFG with optional label and probability."""
    src: int
    dst: int
    label: str = ""
    probability: float = 1.0


class CFGGraph:
    """Wrapper around CFGBuilder providing graph analysis utilities."""
    
    def __init__(self, builder: CFGBuilder):
        self.blocks: List[Block] = builder.blocks
        self.block_map: Dict[int, Block] = {b.id: b for b in builder.blocks}
        
        self.edges: List[Edge] = []
        self.successors: Dict[int, List[Edge]] = {b.id: [] for b in builder.blocks}
        self.predecessors: Dict[int, List[Edge]] = {b.id: [] for b in builder.blocks}
        
        for src_id, dst_id, label in builder.edges:
            edge = Edge(src=src_id, dst=dst_id, label=label)
            self.edges.append(edge)
            self.successors[src_id].append(edge)
            self.predecessors[dst_id].append(edge)
        
        self.entry_id: int = 0
        self.exit_id: int = len(builder.blocks) - 1

    def get_block(self, block_id: int) -> Optional[Block]:
        """Get block by ID."""
        return self.block_map.get(block_id)

    def get_successors(self, block_id: int) -> List[Edge]:
        """Get outgoing edges from a block."""
        return self.successors.get(block_id, [])

    def get_predecessors(self, block_id: int) -> List[Edge]:
        """Get incoming edges to a block."""
        return self.predecessors.get(block_id, [])

    def set_edge_probability(self, src: int, dst: int, prob: float) -> None:
        """Set probability for a specific edge."""
        for edge in self.successors.get(src, []):
            if edge.dst == dst:
                edge.probability = prob
                break
        for edge in self.predecessors.get(dst, []):
            if edge.src == src:
                edge.probability = prob
                break

    def get_edge(self, src: int, dst: int) -> Optional[Edge]:
        """Get edge between two blocks."""
        for edge in self.successors.get(src, []):
            if edge.dst == dst:
                return edge
        return None

    def all_block_ids(self) -> List[int]:
        """Return all block IDs in order."""
        return [b.id for b in self.blocks]

    def compute_block_frequencies(self) -> Dict[int, float]:
        """Compute block execution frequencies via forward dataflow from ENTRY."""
        freq: Dict[int, float] = {b.id: 0.0 for b in self.blocks}
        freq[self.entry_id] = 1.0
        
        visited: Set[int] = set()
        worklist = [self.entry_id]
        
        max_iterations = len(self.blocks) * 10
        iteration = 0
        
        while worklist and iteration < max_iterations:
            iteration += 1
            block_id = worklist.pop(0)
            
            if block_id in visited:
                continue
            
            all_preds_visited = all(
                e.src in visited or e.src == block_id
                for e in self.predecessors.get(block_id, [])
            )
            if not all_preds_visited and block_id != self.entry_id:
                worklist.append(block_id)
                continue
            
            visited.add(block_id)
            
            for edge in self.successors.get(block_id, []):
                contribution = freq[block_id] * edge.probability
                freq[edge.dst] += contribution
                if edge.dst not in visited:
                    worklist.append(edge.dst)
        
        return freq

    def to_dot(self, highlight_blocks: Optional[List[int]] = None,
               show_probabilities: bool = False) -> str:
        """Export CFG to Graphviz DOT format."""
        highlight_set = set(highlight_blocks or [])
        lines = ["digraph CFG {", "  rankdir=TB;", "  node [shape=box, fontname=Helvetica];"]
        
        for b in self.blocks:
            label = "\\n".join(b.statements) if b.statements else f"B{b.id}"
            style = ', style=filled, fillcolor=lightyellow' if b.id in highlight_set else ''
            lines.append(f'  B{b.id} [label="B{b.id}: {label}"{style}];')
        
        for edge in self.edges:
            label_parts = []
            if edge.label:
                label_parts.append(edge.label)
            if show_probabilities and edge.probability < 1.0:
                label_parts.append(f"p={edge.probability:.2f}")
            label_str = "\\n".join(label_parts)
            attr = f' [label="{label_str}"]' if label_str else ""
            lines.append(f"  B{edge.src} -> B{edge.dst}{attr};")
        
        lines.append("}")
        return "\n".join(lines)
