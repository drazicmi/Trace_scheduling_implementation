"""List scheduling for trace optimization."""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple
from ..cfg.graph import CFGGraph
from ..cfg.builder import Block
from ..trace.selector import Trace


@dataclass
class Operation:
    """A single operation extracted from a statement."""
    id: int
    text: str
    block_id: int
    latency: int = 1
    defines: Set[str] = field(default_factory=set)
    uses: Set[str] = field(default_factory=set)


@dataclass
class Schedule:
    """Result of list scheduling."""
    cycles: Dict[int, List[Operation]] = field(default_factory=dict)
    makespan: int = 0
    operations: List[Operation] = field(default_factory=list)
    
    def total_operations(self) -> int:
        return len(self.operations)


class ListScheduler:
    """List scheduler for traces with data dependency analysis."""
    
    def __init__(self, graph: CFGGraph, default_latency: int = 1, num_units: int = 2):
        self.graph = graph
        self.default_latency = default_latency
        self.num_units = num_units  # Number of parallel functional units
        self.op_counter = 0
    
    def _extract_operations(self, trace: Trace) -> List[Operation]:
        """Extract operations from trace blocks."""
        operations: List[Operation] = []
        
        for block_id in trace.block_ids:
            block = self.graph.get_block(block_id)
            if not block:
                continue
            
            for stmt in block.statements:
                if stmt in ('ENTRY', 'EXIT', 'JOIN'):
                    continue
                if stmt.startswith('IF ') or stmt.startswith('WHILE ') or stmt.startswith('FOR '):
                    continue
                
                op = self._create_operation(stmt, block_id)
                operations.append(op)
        
        return operations
    
    def _create_operation(self, stmt: str, block_id: int) -> Operation:
        """Create an operation from a statement, extracting def/use info."""
        self.op_counter += 1
        op = Operation(
            id=self.op_counter,
            text=stmt,
            block_id=block_id,
            latency=self.default_latency
        )
        
        op.defines, op.uses = self._analyze_def_use(stmt)
        return op
    
    def _analyze_def_use(self, stmt: str) -> Tuple[Set[str], Set[str]]:
        """Extract variables defined and used in a statement."""
        defines: Set[str] = set()
        uses: Set[str] = set()
        
        if '=' in stmt and not any(op in stmt for op in ['==', '!=', '<=', '>=']):
            parts = stmt.split('=', 1)
            if len(parts) == 2:
                lhs = parts[0].strip()
                rhs = parts[1].strip()
                
                if '+=' in stmt or '-=' in stmt or '*=' in stmt or '/=' in stmt:
                    var = lhs.rstrip('+-*/').strip()
                    defines.add(var)
                    uses.add(var)
                    uses.update(self._extract_variables(rhs))
                else:
                    defines.add(lhs)
                    uses.update(self._extract_variables(rhs))
        else:
            uses.update(self._extract_variables(stmt))
        
        return defines, uses
    
    def _extract_variables(self, text: str) -> Set[str]:
        """Extract variable names from an expression."""
        variables: Set[str] = set()
        tokens = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', text)
        
        keywords = {'if', 'else', 'for', 'while', 'in', 'range', 'True', 'False',
                   'None', 'and', 'or', 'not', 'return', 'break', 'continue'}
        
        for token in tokens:
            if token not in keywords and not token.isnumeric():
                variables.add(token)
        
        return variables
    
    def _build_dependency_graph(self, operations: List[Operation]) -> Dict[int, Set[int]]:
        """Build a dependency graph: op_id -> set of predecessor op_ids."""
        deps: Dict[int, Set[int]] = {op.id: set() for op in operations}
        
        last_def: Dict[str, int] = {}
        last_use: Dict[str, List[int]] = {}
        
        for op in operations:
            for var in op.uses:
                if var in last_def:
                    deps[op.id].add(last_def[var])
            
            for var in op.defines:
                if var in last_def:
                    deps[op.id].add(last_def[var])
                if var in last_use:
                    for prev_op_id in last_use[var]:
                        deps[op.id].add(prev_op_id)
            
            for var in op.defines:
                last_def[var] = op.id
                last_use[var] = []
            
            for var in op.uses:
                if var not in last_use:
                    last_use[var] = []
                last_use[var].append(op.id)
        
        return deps
    
    def _compute_priorities(self, operations: List[Operation],
                           deps: Dict[int, Set[int]]) -> Dict[int, int]:
        """Compute scheduling priorities based on critical path length."""
        op_map = {op.id: op for op in operations}
        successors: Dict[int, Set[int]] = {op.id: set() for op in operations}
        
        for op_id, pred_ids in deps.items():
            for pred_id in pred_ids:
                successors[pred_id].add(op_id)
        
        priority: Dict[int, int] = {}
        
        def compute(op_id: int) -> int:
            if op_id in priority:
                return priority[op_id]
            
            op = op_map[op_id]
            max_succ = 0
            for succ_id in successors[op_id]:
                max_succ = max(max_succ, compute(succ_id))
            
            priority[op_id] = op.latency + max_succ
            return priority[op_id]
        
        for op in operations:
            compute(op.id)
        
        return priority
    
    def schedule_trace(self, trace: Trace) -> Schedule:
        """Schedule operations from a trace using list scheduling with multiple functional units."""
        operations = self._extract_operations(trace)
        
        if not operations:
            return Schedule()
        
        deps = self._build_dependency_graph(operations)
        priorities = self._compute_priorities(operations, deps)
        op_map = {op.id: op for op in operations}
        
        schedule = Schedule(operations=operations)
        scheduled: Set[int] = set()
        finish_time: Dict[int, int] = {}
        
        current_cycle = 0
        
        while len(scheduled) < len(operations):
            ready: List[int] = []
            for op in operations:
                if op.id in scheduled:
                    continue
                
                all_deps_done = all(
                    dep_id in scheduled and finish_time[dep_id] <= current_cycle
                    for dep_id in deps[op.id]
                )
                if all_deps_done:
                    ready.append(op.id)
            
            if ready:
                ready.sort(key=lambda x: -priorities[x])
                
                # Schedule up to num_units operations per cycle (parallel execution)
                scheduled_this_cycle = 0
                if current_cycle not in schedule.cycles:
                    schedule.cycles[current_cycle] = []
                
                for op_id in ready:
                    if scheduled_this_cycle >= self.num_units:
                        break
                    
                    op = op_map[op_id]
                    schedule.cycles[current_cycle].append(op)
                    scheduled.add(op_id)
                    finish_time[op_id] = current_cycle + op.latency
                    scheduled_this_cycle += 1
            
            current_cycle += 1
            
            if current_cycle > len(operations) * 10:
                break
        
        schedule.makespan = max(finish_time.values()) if finish_time else 0
        return schedule
    
    def schedule_baseline(self, trace: Trace) -> Schedule:
        """Create unoptimized baseline schedule (no cross-block motion)."""
        schedule = Schedule()
        current_cycle = 0
        
        for block_id in trace.block_ids:
            block = self.graph.get_block(block_id)
            if not block:
                continue
            
            for stmt in block.statements:
                if stmt in ('ENTRY', 'EXIT', 'JOIN'):
                    continue
                if stmt.startswith('IF ') or stmt.startswith('WHILE ') or stmt.startswith('FOR '):
                    continue
                
                op = self._create_operation(stmt, block_id)
                schedule.operations.append(op)
                
                if current_cycle not in schedule.cycles:
                    schedule.cycles[current_cycle] = []
                schedule.cycles[current_cycle].append(op)
                
                current_cycle += op.latency
        
        schedule.makespan = current_cycle
        return schedule
