class MetricsComputer:
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    def compute(self, trace_schedule_result, bookkeeping_result, trace_id=0):
        trace_blocks = [block for block in self.blocks if block.trace_id == trace_id]
        trace_edges = [edge for edge in self.edges if getattr(edge, "is_trace_edge", False)]

        total_trace_probability = 0.0
        for edge in trace_edges:
            if edge.probability is not None:
                total_trace_probability += edge.probability

        return {
            "trace_id": trace_id,
            "trace_block_count": len(trace_blocks),
            "trace_edge_count": len(trace_edges),
            "scheduled_instruction_count": len(trace_schedule_result["scheduled_instructions"]),
            "schedule_length": trace_schedule_result["schedule_length"],
            "side_entry_count": len(bookkeeping_result["side_entries"]),
            "side_exit_count": len(bookkeeping_result["side_exits"]),
            "trace_probability_sum": total_trace_probability
        }