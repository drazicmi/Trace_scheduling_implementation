class TraceScheduler:
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    def get_trace_blocks(self, trace_id=0):
        return [block for block in self.blocks if block.trace_id == trace_id]

    def collect_trace_instructions(self, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        scheduled = []

        for block in trace_blocks:
            for instr in block.instructions:
                if instr not in ("ENTRY", "EXIT", "JOIN", "AFTER_LOOP", "AFTER_FOR"):
                    scheduled.append({
                        "block_id": block.id,
                        "instruction": instr
                    })

        return scheduled

    def schedule_trace(self, trace_id=0):
        trace_blocks = self.get_trace_blocks(trace_id)
        scheduled_instructions = self.collect_trace_instructions(trace_id)

        return {
            "trace_id": trace_id,
            "block_ids": [block.id for block in trace_blocks],
            "scheduled_instructions": scheduled_instructions,
            "schedule_length": len(scheduled_instructions)
        }