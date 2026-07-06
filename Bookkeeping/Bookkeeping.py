import copy


class Bookkeeper:
    def __init__(self, blocks, edges, block_factory=None, edge_factory=None):
        self.blocks = blocks
        self.edges = edges
        self.block_factory = block_factory
        self.edge_factory = edge_factory

    def get_trace_block_ids(self, trace_id=0):
        return {block.id for block in self.blocks if block.trace_id == trace_id}

    def collect_side_edges(self):
        side_entries = []
        side_exits = []

        for edge in self.edges:
            if getattr(edge, "is_side_entry", False):
                side_entries.append(edge)
            if getattr(edge, "is_side_exit", False):
                side_exits.append(edge)

        return side_entries, side_exits

    def collect_moved_operations(self, schedule_result):
        moved_ops = []

        for item in schedule_result["scheduled_instructions"]:
            if item["schedule_index"] < item["original_index"]:
                moved_ops.append(item)

        return moved_ops

    def clone_cfg(self):
        new_blocks = copy.deepcopy(self.blocks)
        new_edges = copy.deepcopy(self.edges)
        return new_blocks, new_edges

    def next_block_id(self, blocks):
        if not blocks:
            return 0
        return max(block.id for block in blocks) + 1

    def make_block(self, block_id, statements, trace_id=None, is_bookkeeping=True):
        if self.block_factory is not None:
            block = self.block_factory(block_id, statements)
            block.is_bookkeeping = is_bookkeeping
            block.trace_id = trace_id
            return block

        block = type("DynamicBlock", (), {})()
        block.id = block_id
        block.statements = list(statements)
        block.instructions = list(statements)
        block.is_bookkeeping = is_bookkeeping
        block.trace_id = trace_id
        return block

    def make_edge(
        self,
        src,
        dst,
        label="",
        count=0,
        probability=None,
        is_trace_edge=False,
        is_side_entry=False,
        is_side_exit=False
    ):
        if self.edge_factory is not None:
            edge = self.edge_factory(src, dst, label)
            edge.count = count
            edge.probability = probability
            edge.is_trace_edge = is_trace_edge
            edge.is_side_entry = is_side_entry
            edge.is_side_exit = is_side_exit
            return edge

        edge = type("DynamicEdge", (), {})()
        edge.src = src
        edge.dst = dst
        edge.label = label
        edge.count = count
        edge.probability = probability
        edge.is_trace_edge = is_trace_edge
        edge.is_side_entry = is_side_entry
        edge.is_side_exit = is_side_exit
        return edge

    def build_optimized_cfg(self, schedule_result, trace_id=0):
        new_blocks, new_edges = self.clone_cfg()
        side_entries, side_exits = self.collect_side_edges()
        moved_ops = self.collect_moved_operations(schedule_result)

        block_id_counter = self.next_block_id(new_blocks)
        compensation_blocks = []
        compensation_edges = []

        for moved in moved_ops:
            instruction = moved["instruction"]

            for exit_edge in side_exits:
                compensation_block = self.make_block(
                    block_id_counter,
                    [f"COMPENSATE_SPLIT: {instruction}"],
                    trace_id=None,
                    is_bookkeeping=True
                )
                block_id_counter += 1
                compensation_blocks.append(compensation_block)

                compensation_edges.append(
                    self.make_edge(
                        exit_edge.src,
                        compensation_block.id,
                        label=f"comp_split {exit_edge.label}"
                    )
                )
                compensation_edges.append(
                    self.make_edge(
                        compensation_block.id,
                        exit_edge.dst,
                        label="resume"
                    )
                )

            for entry_edge in side_entries:
                compensation_block = self.make_block(
                    block_id_counter,
                    [f"COMPENSATE_JOIN: {instruction}"],
                    trace_id=None,
                    is_bookkeeping=True
                )
                block_id_counter += 1
                compensation_blocks.append(compensation_block)

                compensation_edges.append(
                    self.make_edge(
                        entry_edge.src,
                        compensation_block.id,
                        label=f"comp_join {entry_edge.label}"
                    )
                )
                compensation_edges.append(
                    self.make_edge(
                        compensation_block.id,
                        entry_edge.dst,
                        label="resume"
                    )
                )

        new_blocks.extend(compensation_blocks)
        new_edges.extend(compensation_edges)

        return {
            "trace_id": trace_id,
            "blocks": new_blocks,
            "edges": new_edges,
            "side_entries": side_entries,
            "side_exits": side_exits,
            "moved_operations": moved_ops,
            "added_compensation_blocks": compensation_blocks,
            "added_compensation_edges": compensation_edges
        }

    def to_dot(self, blocks, edges):
        lines = [
            "digraph CFG {",
            "  rankdir=TB;",
            "  node [shape=box, fontname=Helvetica];"
        ]

        for b in blocks:
            label = "\\n".join(b.statements) if getattr(b, "statements", None) else f"B{b.id}"
            attrs = []

            if getattr(b, "trace_id", None) is not None:
                attrs.append('style="filled"')
                attrs.append('fillcolor="lightyellow"')

            if getattr(b, "is_bookkeeping", False):
                attrs.append('color="purple"')
                attrs.append('style="filled"')
                attrs.append('fillcolor="lavender"')

            attr_text = ", ".join(dict.fromkeys(attrs))
            if attr_text:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}", {attr_text}];')
            else:
                lines.append(f'  B{b.id} [label="B{b.id}: {label}"];')

        for edge in edges:
            label_parts = []
            if getattr(edge, "label", ""):
                label_parts.append(edge.label)
            if getattr(edge, "probability", None) is not None:
                label_parts.append(f"{edge.probability:.2f}")

            attr_parts = []
            if label_parts:
                attr_parts.append(f'label="{" | ".join(label_parts)}"')

            if getattr(edge, "is_trace_edge", False):
                attr_parts.append('color="red"')
                attr_parts.append('penwidth=2.0')
            elif getattr(edge, "is_side_entry", False) or getattr(edge, "is_side_exit", False):
                attr_parts.append('color="blue"')
                attr_parts.append('style="dashed"')

            if getattr(edge, "label", "").startswith("comp_split") or getattr(edge, "label", "").startswith("comp_join"):
                attr_parts.append('color="purple"')
                attr_parts.append('penwidth=2.0')

            attr = f' [{", ".join(attr_parts)}]' if attr_parts else ""
            lines.append(f"  B{edge.src} -> B{edge.dst}{attr};")

        lines.append("}")
        return "\n".join(lines)