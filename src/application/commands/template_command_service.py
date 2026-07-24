"""Dịch vụ command quản lý template."""

from __future__ import annotations

import asyncio
import os

from astrbot.api.message_components import Image, Node, Nodes, Plain


class TemplateCommandService:
    """Đóng gói logic filesystem và tạo tin nhắn cho command template."""

    _CIRCLE_NUMBERS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]

    def __init__(self, plugin_root: str):
        self.plugin_root = plugin_root

    def resolve_template_base_dir(self) -> str:
        """Xác định thư mục template báo cáo, tương thích cấu trúc cũ và mới."""
        candidate_dirs = [
            os.path.join(
                self.plugin_root, "src", "infrastructure", "reporting", "templates"
            ),
            os.path.join(self.plugin_root, "src", "reports", "templates"),
        ]
        for candidate in candidate_dirs:
            if os.path.isdir(candidate):
                return candidate
        return candidate_dirs[0]

    def resolve_template_preview_path(self, template_name: str) -> str | None:
        """Xác định đường dẫn ảnh xem trước template."""
        candidate_paths = [
            os.path.join(self.plugin_root, "assets", f"{template_name}-demo.jpg"),
        ]
        for candidate in candidate_paths:
            if os.path.exists(candidate):
                return candidate
        return None

    async def list_available_templates(self) -> list[str]:
        """Liệt kê mọi template khả dụng."""
        template_base_dir = self.resolve_template_base_dir()

        def _list_templates_sync() -> list[str]:
            if os.path.exists(template_base_dir):
                return sorted(
                    [
                        d
                        for d in os.listdir(template_base_dir)
                        if os.path.isdir(os.path.join(template_base_dir, d))
                        and not d.startswith("__")
                    ]
                )
            return []

        return await asyncio.to_thread(_list_templates_sync)

    async def template_exists(self, template_name: str) -> bool:
        """Kiểm tra thư mục template có tồn tại hay không."""
        template_dir = os.path.join(self.resolve_template_base_dir(), template_name)
        return await asyncio.to_thread(os.path.exists, template_dir)

    def parse_template_input(
        self, template_input: str, available_templates: list[str]
    ) -> tuple[str | None, str | None]:
        """Phân tích đầu vào theo tên hoặc số thứ tự template."""
        if not template_input:
            return None, "❌ Tham số mẫu không được để trống"

        if template_input.isdigit():
            index = int(template_input)
            if 1 <= index <= len(available_templates):
                return available_templates[index - 1], None
            return (
                None,
                f"❌ Số thứ tự '{template_input}' không hợp lệ; phạm vi hợp lệ: "
                f"1-{len(available_templates)}",
            )

        return template_input, None

    def build_template_preview_nodes(
        self,
        available_templates: list[str],
        current_template: str,
        bot_id: str,
    ) -> Nodes:
        """Tạo các node tin nhắn tổng hợp để xem trước template."""
        node_list = []

        header_content = [
            Plain(
                "🎨 Danh sách mẫu báo cáo khả dụng\n"
                f"📌 Đang sử dụng: {current_template}\n"
                "💡 Dùng /maubc [số thứ tự] để chuyển mẫu"
            )
        ]
        node_list.append(Node(uin=bot_id, name="Xem trước mẫu", content=header_content))

        for index, template_name in enumerate(available_templates):
            current_mark = " ✅" if template_name == current_template else ""
            num_label = (
                self._CIRCLE_NUMBERS[index]
                if index < len(self._CIRCLE_NUMBERS)
                else f"({index + 1})"
            )

            node_content = [Plain(f"{num_label} {template_name}{current_mark}")]
            preview_image_path = self.resolve_template_preview_path(template_name)
            if preview_image_path:
                node_content.append(Image.fromFileSystem(preview_image_path))

            node_list.append(Node(uin=bot_id, name=template_name, content=node_content))

        return Nodes(node_list)
