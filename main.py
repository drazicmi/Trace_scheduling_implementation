from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[0]
PYTHON_TO_CFG_DIR = ROOT_DIR / "PythonToCFG"
TRACE_SELECTION_DIR = ROOT_DIR / "TraceSelection"
TRACE_SCHEDULING_DIR = ROOT_DIR / "TraceScheduling"
BOOKKEEPING_DIR = ROOT_DIR / "Bookkeeping"

from PythonToCFG.PythonToCFG import build_profiled_cfg, render_dot_to_png, Block, Edge
from TraceSelection.TraceSelection import TraceSelector
from TraceScheduling.TraceScheduling import TraceScheduler
from Bookkeeping.Bookkeeping import Bookkeeper
from MetricsComputation.MetricsComputation import MetricsComputer

# Set to True to also print the old raw, detailed dumps (schedules,
# per-instruction listings, moved-operation lists, etc.) after the formal
# summary. Off by default to keep console output clean per the formal spec.
VERBOSE = False


# ----------------------------------------------------------------------
# Generic report-printing helpers (standard library only)
# ----------------------------------------------------------------------

REPORT_WIDTH = 66


def print_title(title):
    print("=" * REPORT_WIDTH)
    print(title.center(REPORT_WIDTH))
    print("=" * REPORT_WIDTH)


def print_section(title):
    print()
    print(title)
    print("-" * len(title))


def print_row(label, value, unit="", label_width=34, value_width=12):
    """Print a label/value row with the value right-aligned in a fixed
    column, so numbers line up regardless of label length."""
    value_text = f"{value}{unit}"
    print(f"  {label:<{label_width}} {value_text:>{value_width}}")


def print_kv_table(rows, label_width=34, value_width=12):
    for label, value, *unit in rows:
        u = unit[0] if unit else ""
        print_row(label, value, u, label_width, value_width)


def format_float(value, decimals=3):
    return f"{value:.{decimals}f}"


def verdict(condition, true_text="IMPROVED", false_text="NOT IMPROVED", neutral_text="NO CHANGE"):
    if condition is None:
        return neutral_text
    return true_text if condition else false_text


def print_two_column_header(col1, col2, width1=34, width2=15):
    print(f"  {col1:<{width1}} {col2:>{width2}}")
    print(f"  {'-' * width1} {'-' * width2}")


def print_path_table(paths, title):
    print(f"  {title}:")
    print(f"    {'path':<28} {'weight':>10} {'length':>10} {'w*len':>10}")
    total = 0.0
    for p in paths:
        contrib = p["weight"] * p["length"]
        total += contrib
        print(f"    {p['label']:<28} {p['weight']:>10.3f} {p['length']:>10} {contrib:>10.3f}")
    print(f"    {'TOTAL (WSL)':<28} {'':>10} {'':>10} {total:>10.3f}")


# ----------------------------------------------------------------------
# Formal report sections
# ----------------------------------------------------------------------

def print_pipeline_summary(paths):
    print_section("Pipeline Artifacts")
    for label, path in paths:
        print(f"  {label:<28} {path}")


def print_scheduling_quality_section(report):
    quality = report["quality"]
    print_section("1. Scheduling Quality")

    print_two_column_header("Metric", "Value")
    print_row("Unoptimized program cycles", quality["baseline_cycles"])
    print_row("Optimized program cycles", quality["optimized_cycles"])
    print_row("Cycles reduced", quality["cycles_reduced"])
    print_row(
        "Critical path status",
        verdict(quality["critical_path_reduced"], "REDUCED", "NOT REDUCED"),
    )
    print()
    print_row("Baseline WSL  W(S_base)", format_float(quality["baseline_wsl"]))
    print_row("Optimized WSL W(S_opt)", format_float(quality["optimized_wsl"]))
    print_row("WSL result", verdict(quality["wsl_reduced"]))

    if VERBOSE:
        print()
        print_path_table(quality["baseline_paths"], "Baseline path breakdown")
        print()
        print_path_table(quality["optimized_paths"], "Optimized path breakdown")


def print_optimization_cost_section(report):
    cost = report["cost"]
    print_section("2. Optimization Cost")

    print_two_column_header("Metric", "Value")
    print_row("Original code size (instr.)", cost["original_instruction_count"])
    print_row("Optimized code size (instr.)", cost["optimized_instruction_count"])
    print_row("Total code size increase", cost["code_size_increase"])
    print_row("Added bookkeeping blocks", cost["added_bookkeeping_blocks"])
    print_row("  - split-compensation blocks", cost["split_compensation_count"])
    print_row("  - join-compensation blocks", cost["join_compensation_count"])
    print_row("Added bookkeeping instructions", cost["added_bookkeeping_instructions"])


def print_notes_section():
    print_section("Notes / Approximations")
    notes = [
        "- WSL paths = main trace + one path per side exit taken during",
        "  profiling (probability > 0). Path weight is the product of",
        "  in-trace branch probabilities (trace) or the single branch",
        "  probability of leaving the trace at that point (side exit).",
        "- Baseline WSL side-exit lengths exclude compensation code,",
        "  since bookkeeping only exists for the optimized schedule.",
        "- Bookkeeping instruction cost assumes 1 cycle per compensation",
        "  instruction (simple straight-line replay code, not further",
        "  list-scheduled).",
        "- Code size is measured in real instructions (structural",
        "  markers such as ENTRY/EXIT/JOIN are excluded).",
    ]
    for line in notes:
        print(line)


def print_formal_report(report):
    print()
    print_title("FORMAL TRACE SCHEDULING EVALUATION REPORT")
    print(f"  Trace ID: {report['trace_id']}")
    print_scheduling_quality_section(report)
    print_optimization_cost_section(report)
    print_notes_section()
    print("=" * REPORT_WIDTH)


# ----------------------------------------------------------------------
# Verbose/legacy detail dumps (only shown when VERBOSE = True)
# ----------------------------------------------------------------------

def print_verbose_details(baseline_result, schedule_result, optimized_cfg):
    print()
    print_title("VERBOSE DETAILS")

    print_section("Baseline Schedule")
    for instr in baseline_result.get("scheduled_instructions", []):
        print(f"  cycle {instr.get('schedule_cycle'):>3}  B{instr.get('block_id')}  {instr.get('instruction')}")

    print_section("Optimized Schedule")
    for instr in schedule_result.get("scheduled_instructions", []):
        print(f"  cycle {instr.get('schedule_cycle'):>3}  B{instr.get('block_id')}  {instr.get('instruction')}")

    print_section("Instruction Movements")
    movements = schedule_result.get("instruction_movements", [])
    if not movements:
        print("  (none)")
    for m in movements:
        print(f"  {m['instruction']:<40} from B{m['from_block']} -> cycle {m['schedule_cycle']}")

    print_section("Added Compensation Blocks")
    added_blocks = optimized_cfg.get("added_compensation_blocks", [])
    if not added_blocks:
        print("  (none)")
    for block in added_blocks:
        print(f"  B{block.id}:")
        for stmt in block.statements:
            print(f"      {stmt}")

    print_section("Added Compensation Edges")
    added_edges = optimized_cfg.get("added_compensation_edges", [])
    if not added_edges:
        print("  (none)")
    for edge in added_edges:
        print(f"  B{edge.src} -> B{edge.dst}  [{edge.label}]")


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------

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
    bookkeeper = Bookkeeper(
        builder.blocks,
        builder.edges,
        block_factory=block_factory,
        edge_factory=edge_factory
    )
    optimized_cfg = bookkeeper.build_optimized_cfg(schedule_result, trace_id=0)
    optimized_dot = bookkeeper.to_dot(optimized_cfg["blocks"], optimized_cfg["edges"])

    with open(optimized_output_dot, "w", encoding="utf-8") as f:
        f.write(optimized_dot)
    render_dot_to_png(optimized_dot, str(optimized_output_png))

    # --- MetricsComputation ---
    metrics = MetricsComputer(builder.blocks, builder.edges)
    report = metrics.compute_formal_report(
        baseline_result=baseline_result,
        schedule_result=schedule_result,
        bookkeeping_result=optimized_cfg,
        trace_id=0,
    )

    # --- Output ---
    print_pipeline_summary([
        ("CFG DOT", str(cfg_output_dot)),
        ("CFG PNG", f"{cfg_output_png}.png"),
        ("Trace DOT", str(trace_output_dot)),
        ("Trace PNG", f"{trace_output_png}.png"),
        ("Optimized CFG DOT", str(optimized_output_dot)),
        ("Optimized CFG PNG", f"{optimized_output_png}.png"),
    ])

    print_formal_report(report)

    if VERBOSE:
        print_verbose_details(baseline_result, schedule_result, optimized_cfg)


if __name__ == "__main__":
    main()