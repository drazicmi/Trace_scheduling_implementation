from pathlib import Path
from PythonToCFG.PythonToCFG import build_profiled_cfg, render_dot_to_png, Block, Edge
from TraceSelection.TraceSelection import TraceSelector
from TraceScheduling.TraceScheduling import TraceScheduler
from Bookkeeping.Bookkeeping import Bookkeeper
from MetricsComputation.MetricsComputation import MetricsComputer

ROOT_DIR = Path(__file__).resolve().parents[0]
PYTHON_TO_CFG_DIR = ROOT_DIR / "PythonToCFG"
TRACE_SELECTION_DIR = ROOT_DIR / "TraceSelection"
TRACE_SCHEDULING_DIR = ROOT_DIR / "TraceScheduling"
BOOKKEEPING_DIR = ROOT_DIR / "Bookkeeping"


# Formal report print
def print_formal_report(report):
    print(f"Trace ID: {report['trace_id']}")

    # Scheduling quality section
    quality = report["quality"]
    print('\n' + "1. Scheduling Quality")
    print("-" * len("1. Scheduling Quality"))

    print(f"Unoptimized program cycles:\t {quality['baseline_cycles']}")
    print(f"Optimized program cycles:\t {quality['optimized_cycles']}")
    print(f"Cycles reduced:\t {quality['cycles_reduced']}")

    if quality["critical_path_reduced"] is None:
        print(f"Critical path status:\t {'NO CHANGE'}")
    if quality["critical_path_reduced"]:
        print(f"Critical path status:\t {'REDUCED'}")
    else:
        print(f"Critical path status:\t {'NOT REDUCED'}")

    print("Baseline WSL  W(S_base):\t" + f"{quality['baseline_wsl']:.{3}f}")
    print("Optimized WSL W(S_opt):\t" + f"{quality['optimized_wsl']:.{3}f}")

    if quality["wsl_reduced"] is None:
        print(f"WSL result:\t {'NO CHANGE'}")
    if quality["wsl_reduced"]:
        print(f"WSL result:\t {'IMPROVED'}")
    else:
        print(f"WSL result:\t {'NOT IMPROVED'}")

    # Optimization cost section
    cost = report["cost"]
    print('\n' + "2. Optimization Cost")
    print("-" * len("2. Optimization Cost"))
    print(f"Original code size (instr.):\t {cost['original_instruction_count']}")
    print(f"Optimized code size (instr.):\t {cost['optimized_instruction_count']}")
    print(f"Total code size increase:\t {cost['code_size_increase']}")
    print(f"Added bookkeeping blocks:\t {cost['added_bookkeeping_blocks']}")
    print(f"  - split-compensation blocks:\t {cost['split_compensation_count']}")
    print(f"  - join-compensation blocks:\t {cost['join_compensation_count']}")
    print(f"Added bookkeeping instructions:\t {cost['added_bookkeeping_instructions']}")


# Main pipeline
def block_factory(block_id, statements):
    block = Block(block_id, list(statements), list(statements))
    block.is_bookkeeping = True
    block.trace_id = None
    return block


def edge_factory(src, dst, label=""):
    return Edge(src, dst, label)


def main():
    input_file = PYTHON_TO_CFG_DIR / "input.py"

    cfg_output_dot = PYTHON_TO_CFG_DIR / "output.dot"
    cfg_output_png = PYTHON_TO_CFG_DIR / "cfg_output"

    trace_output_dot = TRACE_SELECTION_DIR / "trace_output.dot"
    trace_output_png = TRACE_SELECTION_DIR / "trace_output"

    optimized_output_dot = TRACE_SCHEDULING_DIR / "optimized_trace.dot"
    optimized_output_png = TRACE_SCHEDULING_DIR / "optimized_trace"

    # --- PythonToCFG ---
    builder, entry_block, exit_block, _ = build_profiled_cfg(
        input_file=str(input_file),
        output_dot=str(cfg_output_dot),
        output_png=str(cfg_output_png)
    )

    # --- TraceSelection ---
    selector = TraceSelector(builder.blocks, builder.edges)
    selector.select_trace(start_block_id=entry_block.id, trace_id=0)

    trace_dot = builder.to_dot()
    with open(trace_output_dot, "w", encoding="utf-8") as f:
        f.write(trace_dot)
    render_dot_to_png(trace_dot, str(trace_output_png))

    # --- TraceScheduling ---
    scheduler = TraceScheduler(builder.blocks, builder.edges)
    baseline_result = scheduler.schedule_baseline(trace_id=0)
    schedule_result = scheduler.schedule_trace(trace_id=0, num_units=2)

    # --- Bookkeeping ---
    bookkeeper = Bookkeeper(builder.blocks, builder.edges, block_factory=block_factory, edge_factory=edge_factory)
    optimized_cfg = bookkeeper.build_optimized_cfg(schedule_result, trace_id=0)
    optimized_dot = bookkeeper.to_dot(optimized_cfg["blocks"], optimized_cfg["edges"])

    with open(optimized_output_dot, "w", encoding="utf-8") as f:
        f.write(optimized_dot)
    render_dot_to_png(optimized_dot, str(optimized_output_png))

    # --- MetricsComputation ---
    metrics = MetricsComputer(builder.blocks, builder.edges)
    report = metrics.compute_formal_report(baseline_result=baseline_result, schedule_result=schedule_result, bookkeeping_result=optimized_cfg, trace_id=0,)

    print_formal_report(report)


if __name__ == "__main__":
    main()
    