"""Compensation code generation for trace scheduling."""

from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional
from ..cfg.graph import CFGGraph, Edge
from ..cfg.builder import Block
from ..trace.selector import Trace
from ..schedule.list_scheduler import Schedule, Operation


@dataclass
class CompensationBlock:
    """A block of compensation code to be inserted."""
    target_block_id: int
    operations: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class BookkeepingResult:
    """Result of bookkeeping analysis."""
    compensation_blocks: List[CompensationBlock] = field(default_factory=list)
    blocks_added: int = 0
    instructions_added: int = 0
    original_size: int = 0
    final_size: int = 0
    
    def code_growth(self) -> int:
        return self.final_size - self.original_size


class CompensationGenerator:
    """Generates compensation code for trace scheduling."""
    
    def __init__(self, graph: CFGGraph, trace: Trace, schedule: Schedule):
        self.graph = graph
        self.trace = trace
        self.schedule = schedule
        self.trace_set = set(trace.block_ids)
    
    def analyze(self) -> BookkeepingResult:
        """Analyze and generate required compensation code."""
        result = BookkeepingResult()
        
        result.original_size = self._count_original_instructions()
        
        self._handle_side_entrances(result)
        
        self._handle_side_exits(result)
        
        result.blocks_added = len(result.compensation_blocks)
        result.instructions_added = sum(
            len(cb.operations) for cb in result.compensation_blocks
        )
        result.final_size = result.original_size + result.instructions_added
        
        return result
    
    def _count_original_instructions(self) -> int:
        """Count total instructions in the original CFG."""
        count = 0
        for block in self.graph.blocks:
            for stmt in block.statements:
                if stmt not in ('ENTRY', 'EXIT', 'JOIN'):
                    count += 1
        return count
    
    def _handle_side_entrances(self, result: BookkeepingResult) -> None:
        """Handle join compensation for side entrances to the trace."""
        for edge in self.trace.side_entrances:
            moved_ops = self._find_moved_operations_before_join(edge.dst)
            
            if moved_ops:
                cb = CompensationBlock(
                    target_block_id=edge.src,
                    operations=moved_ops,
                    reason=f"Join compensation: operations moved above join point B{edge.dst}"
                )
                result.compensation_blocks.append(cb)
    
    def _handle_side_exits(self, result: BookkeepingResult) -> None:
        """Handle split compensation for side exits from the trace."""
        for edge in self.trace.side_exits:
            speculated_ops = self._find_speculated_operations(edge.src)
            
            if speculated_ops:
                cb = CompensationBlock(
                    target_block_id=edge.dst,
                    operations=speculated_ops,
                    reason=f"Split compensation: speculated ops from B{edge.src} not taken"
                )
                result.compensation_blocks.append(cb)
    
    def _find_moved_operations_before_join(self, join_block_id: int) -> List[str]:
        """Find operations that were moved above a join point in scheduling."""
        moved: List[str] = []
        
        join_idx = self.trace.block_ids.index(join_block_id) if join_block_id in self.trace.block_ids else -1
        if join_idx <= 0:
            return moved
        
        original_block_ops: Dict[int, List[str]] = {}
        for op in self.schedule.operations:
            if op.block_id not in original_block_ops:
                original_block_ops[op.block_id] = []
            original_block_ops[op.block_id].append(op.text)
        
        scheduled_order: List[Operation] = []
        for cycle in sorted(self.schedule.cycles.keys()):
            scheduled_order.extend(self.schedule.cycles[cycle])
        
        for i, op in enumerate(scheduled_order):
            if op.block_id == join_block_id:
                for j in range(i):
                    prev_op = scheduled_order[j]
                    if prev_op.block_id != join_block_id:
                        original_idx = self.trace.block_ids.index(prev_op.block_id) if prev_op.block_id in self.trace.block_ids else -1
                        if original_idx >= join_idx:
                            moved.append(op.text)
                            break
        
        return moved
    
    def _find_speculated_operations(self, branch_block_id: int) -> List[str]:
        """Find operations speculatively executed past a branch point."""
        speculated: List[str] = []
        
        branch_idx = self.trace.block_ids.index(branch_block_id) if branch_block_id in self.trace.block_ids else -1
        if branch_idx < 0:
            return speculated
        
        scheduled_order: List[Operation] = []
        for cycle in sorted(self.schedule.cycles.keys()):
            scheduled_order.extend(self.schedule.cycles[cycle])
        
        branch_ops_done = False
        for op in scheduled_order:
            if op.block_id == branch_block_id:
                branch_ops_done = True
            elif branch_ops_done and op.block_id in self.trace_set:
                orig_idx = self.trace.block_ids.index(op.block_id)
                if orig_idx > branch_idx:
                    speculated.append(op.text)
        
        return speculated
    
    def generate_report(self) -> Dict:
        """Generate a detailed bookkeeping report."""
        result = self.analyze()
        
        return {
            "compensation_blocks": [
                {
                    "target_block": f"B{cb.target_block_id}",
                    "operations": cb.operations,
                    "reason": cb.reason
                }
                for cb in result.compensation_blocks
            ],
            "blocks_added": result.blocks_added,
            "instructions_added": result.instructions_added,
            "code_growth": result.code_growth(),
            "original_size": result.original_size,
            "final_size": result.final_size
        }
