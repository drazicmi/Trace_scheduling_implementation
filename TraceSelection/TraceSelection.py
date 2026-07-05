class TraceSelector:
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    def get_block(self, block_id):
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None

    def get_outgoing_edges(self, block_id):
        return [edge for edge in self.edges if edge.src == block_id]

    def select_trace(self, start_block_id=0, trace_id=0):
        trace_blocks = []
        trace_edges = []
        visited = set()
        current_id = start_block_id

        while current_id not in visited:
            current_block = self.get_block(current_id)
            if current_block is None:
                break

            visited.add(current_id)
            trace_blocks.append(current_block)

            outgoing = self.get_outgoing_edges(current_id)
            if not outgoing:
                break

            candidate_edges = [edge for edge in outgoing if edge.label != "back"]
            if not candidate_edges:
                break

            def edge_score(edge):
                return edge.probability if edge.probability is not None else -1.0

            best_edge = max(candidate_edges, key=edge_score)

            if best_edge.dst in visited:
                break

            trace_edges.append(best_edge)
            current_id = best_edge.dst

        self.mark_trace(trace_blocks, trace_edges, trace_id)
        self.mark_side_entries_and_exits(trace_blocks, trace_edges)

        return trace_blocks, trace_edges

    def mark_trace(self, trace_blocks, trace_edges, trace_id=0):
        for block in trace_blocks:
            block.trace_id = trace_id

        for edge in trace_edges:
            edge.is_trace_edge = True

    def mark_side_entries_and_exits(self, trace_blocks, trace_edges):
        trace_block_ids = {block.id for block in trace_blocks}
        trace_edge_keys = {(edge.src, edge.dst, edge.label) for edge in trace_edges}

        for edge in self.edges:
            if (edge.src, edge.dst, edge.label) in trace_edge_keys:
                continue

            if edge.src not in trace_block_ids and edge.dst in trace_block_ids:
                edge.is_side_entry = True

            if edge.src in trace_block_ids and edge.dst not in trace_block_ids:
                edge.is_side_exit = True