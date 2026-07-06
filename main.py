from pathlib import Path
import json

ROOT_DIR = Path(__file__).resolve().parents[0]
PYTHON_TO_CFG_DIR = ROOT_DIR / "PythonToCFG"
TRACE_SELECTION_DIR = ROOT_DIR / "TraceSelection"
TRACE_SCHEDULING_DIR = ROOT_DIR / "TraceScheduling"
BOOKKEEPING_DIR = ROOT_DIR / "Bookkeeping"

from PythonToCFG.PythonToCFG import build_profiled_cfg, render_dot_to_png, Block, Edge
from TraceSelection.TraceSelection import TraceSelector
from TraceScheduling.TraceScheduling import TraceScheduler
from Bookkeeping.Bookkeeping import Bookkeeper


def print_section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def print_pretty(data):
    print(json.dumps(data, indent=4))


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

    builder, entry_block, exit_block, _ = build_profiled_cfg(
        input_file=str(input_file),
        output_dot=str(cfg_output_dot),
        output_png=str(cfg_output_png)
    )

    selector = TraceSelector(builder.blocks, builder.edges)
    selector.select_trace(start_block_id=entry_block.id, trace_id=0)

    trace_dot = builder.to_dot()
    with open(trace_output_dot, "w", encoding="utf-8") as f:
        f.write(trace_dot)
    render_dot_to_png(trace_dot, str(trace_output_png))

    scheduler = TraceScheduler(builder.blocks, builder.edges)
    
    # Get both baseline (sequential) and optimized schedules for comparison
    baseline_result = scheduler.schedule_baseline(trace_id=0)
    schedule_result = scheduler.schedule_trace(trace_id=0, num_units=2)

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

    print_section("Pipeline completed successfully")
    print(f"CFG DOT written to: {cfg_output_dot}")
    print(f"CFG PNG written to: {cfg_output_png}.png")
    print(f"Trace DOT written to: {trace_output_dot}")
    print(f"Trace PNG written to: {trace_output_png}.png")
    print(f"Optimized CFG DOT written to: {optimized_output_dot}")
    print(f"Optimized CFG PNG written to: {optimized_output_png}.png")

    print_section("Schedule Comparison: Baseline vs Optimized")
    baseline_makespan = baseline_result.get("makespan", 0)
    optimized_makespan = schedule_result.get("makespan", 0)
    improvement = ((baseline_makespan - optimized_makespan) / baseline_makespan * 100) if baseline_makespan > 0 else 0
    
    print_pretty({
        "baseline": {
            "makespan_cycles": baseline_makespan,
            "functional_units": baseline_result.get("num_functional_units"),
            "instruction_count": baseline_result.get("original_instruction_count")
        },
        "optimized": {
            "makespan_cycles": optimized_makespan,
            "functional_units": schedule_result.get("num_functional_units"),
            "instruction_count": schedule_result.get("original_instruction_count")
        },
        "improvement_percent": round(improvement, 2),
        "instruction_movements_count": len(schedule_result.get("instruction_movements", []))
    })
    
    print_section("Trace Scheduling Details")
    print_pretty({
        "trace_id": schedule_result.get("trace_id"),
        "block_ids": schedule_result.get("block_ids"),
        "scheduled_instructions": [
            {
                "cycle": instr.get("schedule_cycle"),
                "instruction": instr.get("instruction"),
                "block_id": instr.get("block_id")
            }
            for instr in schedule_result.get("scheduled_instructions", [])
        ]
    })

    print_section("Optimized CFG Summary")
    print_pretty({
        "trace_id": optimized_cfg["trace_id"],
        "moved_operations": [
            {
                "instruction": item["instruction"],
                "original_index": item.get("original_index", 0),
                "scheduled_cycle": item.get("schedule_cycle", item.get("schedule_index", 0))
            }
            for item in optimized_cfg["moved_operations"]
        ],
        "split_compensation_count": optimized_cfg.get("split_compensation_count", 0),
        "join_compensation_count": optimized_cfg.get("join_compensation_count", 0),
        "total_compensation_instructions": optimized_cfg.get("total_compensation_instructions", 0),
        "added_compensation_blocks": [
            {
                "block_id": block.id,
                "statements": block.statements
            }
            for block in optimized_cfg["added_compensation_blocks"]
        ],
        "added_compensation_edges": [
            {
                "src": edge.src,
                "dst": edge.dst,
                "label": edge.label
            }
            for edge in optimized_cfg["added_compensation_edges"]
        ]
    })


if __name__ == "__main__":
    main()