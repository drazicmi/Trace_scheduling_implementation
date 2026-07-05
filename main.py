import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[0]
PYTHON_TO_CFG_DIR = ROOT_DIR / "PythonToCFG"
TRACE_SELECTION_DIR = ROOT_DIR / "TraceSelection"

from PythonToCFG.PythonToCFG import build_profiled_cfg, render_dot_to_png
from TraceSelection.TraceSelection import TraceSelector
from TraceScheduling.TraceScheduling import TraceScheduler
from Bookkeeping.Bookkeeping import Bookkeeper
from MetricsComputation.MetricsComputation import MetricsComputer


def print_section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def print_pretty(data):
    print(json.dumps(data, indent=4))


def main():
    input_file = PYTHON_TO_CFG_DIR / "input.py"

    cfg_output_dot = PYTHON_TO_CFG_DIR / "output.dot"
    cfg_output_png = PYTHON_TO_CFG_DIR / "cfg_output"

    trace_output_dot = TRACE_SELECTION_DIR / "trace_output.dot"
    trace_output_png = TRACE_SELECTION_DIR / "trace_output"

    builder, entry_block, exit_block, _ = build_profiled_cfg(
        input_file=str(input_file),
        output_dot=str(cfg_output_dot),
        output_png=str(cfg_output_png)
    )

    selector = TraceSelector(builder.blocks, builder.edges)
    trace_blocks, trace_edges = selector.select_trace(start_block_id=entry_block.id, trace_id=0)

    trace_dot = builder.to_dot()

    with open(trace_output_dot, "w", encoding="utf-8") as f:
        f.write(trace_dot)

    render_dot_to_png(trace_dot, str(trace_output_png))

    scheduler = TraceScheduler(builder.blocks, builder.edges)
    schedule_result = scheduler.schedule_trace(trace_id=0)

    bookkeeper = Bookkeeper(builder.blocks, builder.edges)
    bookkeeping_result = bookkeeper.collect_bookkeeping(trace_id=0)

    metrics_computer = MetricsComputer(builder.blocks, builder.edges)
    metrics_result = metrics_computer.compute(schedule_result, bookkeeping_result, trace_id=0)

    print_section("Pipeline completed successfully")
    print(f"CFG DOT written to: {cfg_output_dot}")
    print(f"CFG PNG written to: {cfg_output_png}.png")
    print(f"Trace DOT written to: {trace_output_dot}")
    print(f"Trace PNG written to: {trace_output_png}.png")

    print_section("Trace Scheduling Result")
    print_pretty(schedule_result)

    print_section("Bookkeeping Result")
    print_pretty(bookkeeping_result)

    print_section("Metrics Result")
    print_pretty(metrics_result)


if __name__ == "__main__":
    main()