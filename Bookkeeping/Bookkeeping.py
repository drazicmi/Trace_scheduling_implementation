class Bookkeeper:
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    def collect_bookkeeping(self, trace_id=0):
        side_entries = []
        side_exits = []

        for edge in self.edges:
            if getattr(edge, "is_side_entry", False):
                side_entries.append({
                    "src": edge.src,
                    "dst": edge.dst,
                    "label": edge.label
                })

            if getattr(edge, "is_side_exit", False):
                side_exits.append({
                    "src": edge.src,
                    "dst": edge.dst,
                    "label": edge.label
                })

        return {
            "trace_id": trace_id,
            "side_entries": side_entries,
            "side_exits": side_exits,
            "bookkeeping_block_count": len(side_entries) + len(side_exits)
        }