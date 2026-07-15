class TraceSelector:
    """
        This class implements greedy trace selection for trace scheduling optimization.
    """
    # Initialize the trace selector with CFG data
    def __init__(self, blocks, edges):
        self.blocks = blocks
        self.edges = edges

    # Retrieve a block by its ID from the block list
    def get_block(self, block_id):
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None

    # Get all outgoing edges from a given block
    def get_outgoing_edges(self, block_id):
        return [edge for edge in self.edges if edge.src == block_id]

    # Mark blocks and edges as belonging to a specific trace
    def mark_trace(self, trace_blocks, trace_edges, trace_id=0):

        # Assign trace ID to each block in the trace
        for block in trace_blocks:
            block.trace_id = trace_id

        # Mark edges as trace edges for visualization
        for edge in trace_edges:
            edge.is_trace_edge = True

    # Identify side entries (joins from outside) and side exits (branches leaving trace)
    def mark_side_entries_and_exits(self, trace_blocks, trace_edges):

        trace_block_ids = {block.id for block in trace_blocks}
        trace_edge_keys = {(edge.src, edge.dst, edge.label) for edge in trace_edges}

        # Check each edge in the CFG
        for edge in self.edges:
            # Skip edges that are part of the trace
            if (edge.src, edge.dst, edge.label) in trace_edge_keys:
                continue

            # Side entry: edge from outside trace into a trace block
            if edge.src not in trace_block_ids and edge.dst in trace_block_ids:
                edge.is_side_entry = True

            # Side exit: edge from trace block to outside trace
            if edge.src in trace_block_ids and edge.dst not in trace_block_ids:
                edge.is_side_exit = True

    # Select the most probable trace path using greedy algorithm
    def select_trace(self, start_block_id=0, trace_id=0):

        trace_blocks = []
        trace_edges = []
        visited = set()
        current_id = start_block_id

        # Greedy traversal: follow highest-probability edges until we hit a dead end or cycle
        while current_id not in visited:
            current_block = self.get_block(current_id)
            if current_block is None:
                break

            # Mark block as visited and add to trace
            visited.add(current_id)
            trace_blocks.append(current_block)

            # Get outgoing edges from current block
            outgoing = self.get_outgoing_edges(current_id)
            if not outgoing:
                break

            # Filter out back edges (loop iterations) to avoid cycles in trace
            candidate_edges = [edge for edge in outgoing if edge.label != "back"]
            if not candidate_edges:
                break

            # Score function: prefer edges with higher probability
            def edge_score(edge):
                return edge.probability if edge.probability is not None else -1.0

            # Select the edge with the highest probability (greedy choice)
            best_edge = max(candidate_edges, key=edge_score)

            # Stop if best edge leads to already-visited block (creates a cycle)
            if best_edge.dst in visited:
                break

            # Add edge to trace and move to next block
            trace_edges.append(best_edge)
            current_id = best_edge.dst

        # Mark selected blocks and edges as part of the trace
        self.mark_trace(trace_blocks, trace_edges, trace_id)

        # Identify side entries and exits for bookkeeping
        self.mark_side_entries_and_exits(trace_blocks, trace_edges)

        return trace_blocks, trace_edges

