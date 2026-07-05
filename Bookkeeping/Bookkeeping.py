# This class handles bookkeeping analysis for trace scheduling.
class Bookkeeper:
    # Initialize the bookkeeper with CFG data
    def __init__(self, blocks, edges):

        self.blocks = blocks
        self.edges = edges

    # Collect all bookkeeping requirements for a given trace
    def collect_bookkeeping(self, trace_id=0):
        
        side_entries = []
        side_exits = []

        # Scan all edges to find side entries and exits
        for edge in self.edges:
            
            # Side entry: control flow entering the trace from outside
            if getattr(edge, "is_side_entry", False):
                side_entries.append({
                    "src": edge.src,
                    "dst": edge.dst,
                    "label": edge.label
                })

            # Side exit: control flow leaving the trace before completion
            if getattr(edge, "is_side_exit", False):
                side_exits.append({
                    "src": edge.src,
                    "dst": edge.dst,
                    "label": edge.label
                })

        # Return bookkeeping summary
        return {
            "trace_id": trace_id,
            "side_entries": side_entries,
            "side_exits": side_exits,
            "bookkeeping_block_count": len(side_entries) + len(side_exits)
        }