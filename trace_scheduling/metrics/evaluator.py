"""Metrics evaluation for trace scheduling optimization."""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from ..cfg.graph import CFGGraph
from ..trace.selector import Trace, TraceSelector
from ..schedule.list_scheduler import Schedule, ListScheduler, Operation
from ..bookkeeping.compensation import BookkeepingResult


@dataclass
class ScheduleMetrics:
    """Comprehensive metrics for schedule comparison."""
    program: str = ""
    trace_blocks: List[str] = field(default_factory=list)
    trace_probability: float = 0.0
    
    cycles_unoptimized: int = 0
    cycles_optimized: int = 0
    cycle_reduction: float = 0.0
    
    wsl_unoptimized: float = 0.0
    wsl_optimized: float = 0.0
    wsl_improvement: float = 0.0
    
    critical_path: int = 0                    # Theoretical minimum (longest dep chain)
    efficiency_before: float = 0.0            # critical_path / cycles_unoptimized
    efficiency_after: float = 0.0             # critical_path / cycles_optimized
    efficiency_improvement: float = 0.0       # How much closer to optimal
    
    bookkeeping_blocks_added: int = 0
    bookkeeping_instructions_added: int = 0
    code_growth: int = 0
    code_growth_percent: float = 0.0


class MetricsEvaluator:
    """Evaluates and compares scheduling metrics."""
    
    def __init__(self, graph: CFGGraph, program_name: str = ""):
        self.graph = graph
        self.program_name = program_name
    
    def evaluate(self, trace: Trace, optimized: Schedule, baseline: Schedule,
                 bookkeeping: BookkeepingResult) -> ScheduleMetrics:
        """Compute all metrics comparing optimized vs baseline schedules."""
        metrics = ScheduleMetrics(program=self.program_name)
        
        metrics.trace_blocks = [f"B{bid}" for bid in trace.block_ids]
        metrics.trace_probability = trace.total_probability
        
        metrics.cycles_unoptimized = baseline.makespan
        metrics.cycles_optimized = optimized.makespan
        if baseline.makespan > 0:
            metrics.cycle_reduction = (baseline.makespan - optimized.makespan) / baseline.makespan * 100
        
        metrics.wsl_unoptimized = self._compute_wsl_baseline(baseline)
        metrics.wsl_optimized = self._compute_wsl_optimized(optimized, trace)
        if metrics.wsl_unoptimized > 0:
            metrics.wsl_improvement = (metrics.wsl_unoptimized - metrics.wsl_optimized) / metrics.wsl_unoptimized * 100
        
        # Critical path is the theoretical minimum (longest dependency chain)
        # It's the same for both schedules since dependencies don't change
        metrics.critical_path = self._compute_critical_path(optimized)
        
        # Efficiency = how close schedule is to theoretical minimum
        # 100% = schedule achieves critical path (perfect parallelization)
        if metrics.cycles_unoptimized > 0:
            metrics.efficiency_before = (metrics.critical_path / metrics.cycles_unoptimized) * 100
        if metrics.cycles_optimized > 0:
            metrics.efficiency_after = (metrics.critical_path / metrics.cycles_optimized) * 100
        metrics.efficiency_improvement = metrics.efficiency_after - metrics.efficiency_before
        
        metrics.bookkeeping_blocks_added = bookkeeping.blocks_added
        metrics.bookkeeping_instructions_added = bookkeeping.instructions_added
        metrics.code_growth = bookkeeping.code_growth()
        if bookkeeping.original_size > 0:
            metrics.code_growth_percent = bookkeeping.code_growth() / bookkeeping.original_size * 100
        
        return metrics
    
    def _compute_wsl_baseline(self, schedule: Schedule) -> float:
        """Compute weighted schedule length for baseline (single path, weight=1)."""
        return float(schedule.makespan)
    
    def _compute_wsl_optimized(self, schedule: Schedule, trace: Trace) -> float:
        """Compute WSL: W(S) = sum(weight_j * |S_j|) for optimized trace."""
        return trace.total_probability * schedule.makespan
    
    def _compute_critical_path(self, schedule: Schedule) -> int:
        """Compute critical path length from schedule."""
        if not schedule.operations:
            return 0
        
        op_map = {op.id: op for op in schedule.operations}
        deps = self._build_deps(schedule.operations)
        
        path_length: Dict[int, int] = {}
        
        def compute_path(op_id: int) -> int:
            if op_id in path_length:
                return path_length[op_id]
            
            op = op_map[op_id]
            max_pred = 0
            for pred_id in deps.get(op_id, set()):
                max_pred = max(max_pred, compute_path(pred_id))
            
            path_length[op_id] = max_pred + op.latency
            return path_length[op_id]
        
        return max(compute_path(op.id) for op in schedule.operations)
    
    def _build_deps(self, operations: List[Operation]) -> Dict[int, set]:
        """Build dependency map for operations."""
        deps: Dict[int, set] = {op.id: set() for op in operations}
        last_def: Dict[str, int] = {}
        
        for op in operations:
            for var in op.uses:
                if var in last_def:
                    deps[op.id].add(last_def[var])
            for var in op.defines:
                last_def[var] = op.id
        
        return deps
    
    def compute_wsl_all_paths(self, selector: TraceSelector, scheduler: ListScheduler) -> float:
        """Compute total WSL across all paths: W(S) = sum_j(w_j * |S_j|)."""
        paths = selector.get_all_paths()
        total_wsl = 0.0
        
        for path in paths:
            schedule = scheduler.schedule_trace(path)
            total_wsl += path.total_probability * schedule.makespan
        
        return total_wsl
    
    def to_dict(self, metrics: ScheduleMetrics) -> Dict:
        """Convert metrics to dictionary for JSON output."""
        return {
            "program": metrics.program,
            "trace": metrics.trace_blocks,
            "trace_probability": round(metrics.trace_probability, 4),
            "cycles": {
                "unoptimized": metrics.cycles_unoptimized,
                "optimized": metrics.cycles_optimized,
                "reduction_percent": round(metrics.cycle_reduction, 2)
            },
            "wsl": {
                "unoptimized": round(metrics.wsl_unoptimized, 4),
                "optimized": round(metrics.wsl_optimized, 4),
                "improvement_percent": round(metrics.wsl_improvement, 2)
            },
            "critical_path": {
                "length": metrics.critical_path,
                "efficiency_before_percent": round(metrics.efficiency_before, 2),
                "efficiency_after_percent": round(metrics.efficiency_after, 2),
                "efficiency_improvement": round(metrics.efficiency_improvement, 2)
            },
            "bookkeeping": {
                "blocks_added": metrics.bookkeeping_blocks_added,
                "instructions_added": metrics.bookkeeping_instructions_added
            },
            "code_growth": {
                "instructions": metrics.code_growth,
                "percent": round(metrics.code_growth_percent, 2)
            }
        }
    
    def format_comparison_table(self, metrics: ScheduleMetrics) -> str:
        """Format metrics as a comparison table."""
        lines = [
            f"=== Trace Scheduling Results: {metrics.program} ===",
            "",
            f"Selected Trace: {' -> '.join(metrics.trace_blocks)}",
            f"Trace Probability: {metrics.trace_probability:.4f}",
            "",
            "Metric                    | Unoptimized | Optimized | Improvement",
            "-" * 70,
            f"Cycles                    | {metrics.cycles_unoptimized:11} | {metrics.cycles_optimized:9} | {metrics.cycle_reduction:+.1f}%",
            f"WSL                       | {metrics.wsl_unoptimized:11.2f} | {metrics.wsl_optimized:9.2f} | {metrics.wsl_improvement:+.1f}%",
            f"Efficiency (CP/Cycles)    | {metrics.efficiency_before:10.1f}% | {metrics.efficiency_after:8.1f}% | {metrics.efficiency_improvement:+.1f}%",
            "",
            f"Critical Path (theoretical min): {metrics.critical_path} cycles",
            "",
            "Bookkeeping Cost:",
            f"  Blocks added: {metrics.bookkeeping_blocks_added}",
            f"  Instructions added: {metrics.bookkeeping_instructions_added}",
            f"  Code growth: {metrics.code_growth} ({metrics.code_growth_percent:+.1f}%)",
        ]
        return "\n".join(lines)
