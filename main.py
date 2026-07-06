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
    schedule_result = scheduler.schedule_trace(trace_id=0)

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

    print_section("Trace Scheduling Result")
    print_pretty(schedule_result)

    print_section("Optimized CFG Summary")
    print_pretty({
        "trace_id": optimized_cfg["trace_id"],
        "moved_operations": [
            {
                "instruction": item["instruction"],
                "original_index": item["original_index"],
                "new_index": item["schedule_index"]
            }
            for item in optimized_cfg["moved_operations"]
        ],
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