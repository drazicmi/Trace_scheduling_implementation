
# This class computes quality and cost metrics for trace scheduling optimization. 
# These metrics help evaluate the trade-off between optimization benefit and bookkeeping cost.

class MetricsComputer:
    # Initialize the metrics computer with CFG data
    def __init__(self, blocks, edges):

        self.blocks = blocks
        self.edges = edges
    
    # Compute all metrics for a scheduled trace
    def compute(self, trace_schedule_result, bookkeeping_result, trace_id=0):
        
        # Filter blocks that belong to the specified trace
        trace_blocks = [block for block in self.blocks if block.trace_id == trace_id]
        
        # Filter edges that are part of the trace path
        trace_edges = [edge for edge in self.edges if getattr(edge, "is_trace_edge", False)]

        # Calculate total trace probability by summing edge probabilities
        total_trace_probability = 0.0
        for edge in trace_edges:
            if edge.probability is not None:
                total_trace_probability += edge.probability

        # Compile and return all metrics
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