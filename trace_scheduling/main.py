"""Main entry point for trace scheduling pipeline."""

import ast
import argparse
import json
import os
from pathlib import Path
from typing import Optional

from .cfg.builder import CFGBuilder
from .cfg.graph import CFGGraph
from .analysis.probabilities import BranchProbabilityAnalyzer
from .trace.selector import TraceSelector
from .schedule.list_scheduler import ListScheduler
from .bookkeeping.compensation import CompensationGenerator
from .metrics.evaluator import MetricsEvaluator


def render_dot_to_png(dot_text: str, output_name: str) -> bool:
    """Render DOT text to PNG using Graphviz."""
    try:
        from graphviz import Source
        src = Source(dot_text, format="png")
        src.render(output_name, cleanup=True)
        return True
    except ImportError as e:
        print(f"Warning: Python 'graphviz' package not installed. Run: pip install graphviz")
        print(f"  Import error details: {e}")
        return False
    except Exception as e:
        print(f"Warning: Failed to render PNG: {e}")
        return False


def run_pipeline(source_path: str, profile_path: Optional[str] = None,
                 output_dir: str = ".", render_png: bool = True) -> dict:
    """Run the full trace scheduling pipeline on a source file."""
    
    with open(source_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    tree = ast.parse(source, filename=source_path)
    builder = CFGBuilder()
    builder.build_module(tree)
    
    graph = CFGGraph(builder)
    
    prob_analyzer = BranchProbabilityAnalyzer(graph, profile_path)
    prob_analyzer.analyze()
    
    selector = TraceSelector(graph)
    trace = selector.select_trace()
    
    scheduler = ListScheduler(graph)
    optimized_schedule = scheduler.schedule_trace(trace)
    baseline_schedule = scheduler.schedule_baseline(trace)
    
    compensation = CompensationGenerator(graph, trace, optimized_schedule)
    bookkeeping_result = compensation.analyze()
    
    program_name = Path(source_path).stem
    evaluator = MetricsEvaluator(graph, program_name)
    metrics = evaluator.evaluate(trace, optimized_schedule, baseline_schedule, bookkeeping_result)
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    cfg_dot = graph.to_dot(show_probabilities=True)
    cfg_dot_path = output_dir / f"{program_name}_cfg.dot"
    with open(cfg_dot_path, 'w', encoding='utf-8') as f:
        f.write(cfg_dot)
    
    trace_dot = graph.to_dot(highlight_blocks=trace.block_ids, show_probabilities=True)
    trace_dot_path = output_dir / f"{program_name}_trace.dot"
    with open(trace_dot_path, 'w', encoding='utf-8') as f:
        f.write(trace_dot)
    
    if render_png:
        render_dot_to_png(cfg_dot, str(output_dir / f"{program_name}_cfg"))
        render_dot_to_png(trace_dot, str(output_dir / f"{program_name}_trace"))
    
    report = {
        "program": source_path,
        "metrics": evaluator.to_dict(metrics),
        "trace": {
            "blocks": [f"B{bid}" for bid in trace.block_ids],
            "probability": trace.total_probability,
            "side_exits": [f"B{e.src}->B{e.dst}" for e in trace.side_exits],
            "side_entrances": [f"B{e.src}->B{e.dst}" for e in trace.side_entrances]
        },
        "schedule": {
            "optimized_makespan": optimized_schedule.makespan,
            "baseline_makespan": baseline_schedule.makespan,
            "operations": [
                {"cycle": c, "ops": [op.text for op in ops]}
                for c, ops in sorted(optimized_schedule.cycles.items())
            ]
        },
        "bookkeeping": compensation.generate_report()
    }
    
    report_path = output_dir / f"{program_name}_report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    return {
        "metrics": metrics,
        "report": report,
        "evaluator": evaluator
    }


def run_all_examples(examples_dir: str = "examples", output_dir: str = "output") -> None:
    """Run pipeline on all example files and print comparison."""
    examples_path = Path(examples_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    results = []
    
    for py_file in sorted(examples_path.glob("*.py")):
        print(f"\n{'='*60}")
        print(f"Processing: {py_file.name}")
        print('='*60)
        
        try:
            result = run_pipeline(
                str(py_file),
                output_dir=str(output_path / py_file.stem)
            )
            
            print(result["evaluator"].format_comparison_table(result["metrics"]))
            results.append(result)
            
        except Exception as e:
            print(f"Error processing {py_file.name}: {e}")
    
    if results:
        print(f"\n{'='*60}")
        print("SUMMARY")
        print('='*60)
        print(f"\nProcessed {len(results)} programs successfully.")
        
        total_cycle_reduction = sum(r["metrics"].cycle_reduction for r in results) / len(results)
        total_wsl_improvement = sum(r["metrics"].wsl_improvement for r in results) / len(results)
        total_efficiency_improvement = sum(r["metrics"].efficiency_improvement for r in results) / len(results)
        
        print(f"Average cycle reduction: {total_cycle_reduction:.1f}%")
        print(f"Average WSL improvement: {total_wsl_improvement:.1f}%")
        print(f"Average efficiency improvement: {total_efficiency_improvement:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trace Scheduling Pipeline for Python Programs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m trace_scheduling.main --source examples/if_dominant.py
  python -m trace_scheduling.main --source input.py --profile profile.json
  python -m trace_scheduling.main --all --examples examples/
        """
    )
    
    parser.add_argument(
        "--source", "-s",
        help="Path to Python source file to analyze"
    )
    parser.add_argument(
        "--profile", "-p",
        help="Path to profile JSON file with edge probabilities"
    )
    parser.add_argument(
        "--output", "-o",
        default="output",
        help="Output directory (default: output)"
    )
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="Skip PNG rendering"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run on all examples in examples/ directory"
    )
    parser.add_argument(
        "--examples",
        default="examples",
        help="Examples directory (default: examples)"
    )
    
    args = parser.parse_args()
    
    if args.all:
        run_all_examples(args.examples, args.output)
    elif args.source:
        result = run_pipeline(
            args.source,
            profile_path=args.profile,
            output_dir=args.output,
            render_png=not args.no_png
        )
        print(result["evaluator"].format_comparison_table(result["metrics"]))
        print(f"\nReports written to: {args.output}/")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
