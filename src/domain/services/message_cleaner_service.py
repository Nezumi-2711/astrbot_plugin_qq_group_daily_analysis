"""
Dịch vụ làm sạch tin nhắn thuộc tầng domain.

Phụ trách lọc tin nhắn của bot, lệnh, nội dung kỹ thuật như mã biểu cảm gốc
và các nội dung nhạy cảm.
"""

import re
from dataclasses import replace

from ..value_objects.unified_message import (
    MessageContent,
    MessageContentType,
    UnifiedMessage,
)

# Regex biểu cảm tuỳ chỉnh Discord: <:name:id> hoặc <a:name:id>
_DISCORD_CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:.+?:\d+>")
# Regex lệnh: khớp tin nhắn bắt đầu bằng / hoặc @thành viên /.
_COMMAND_PATTERN = re.compile(r"^\s*(?:<@\d+>\s+)?/")


class MessageCleanerService:
    """Dịch vụ làm sạch tin nhắn."""

    def clean_messages(
        self,
        messages: list[UnifiedMessage],
        bot_self_ids: list[str] = None,
        filter_commands: bool = True,
    ) -> list[UnifiedMessage]:
        """
        Làm sạch và lọc danh sách tin nhắn.

        Args:
            messages: Danh sách tin nhắn thống nhất ban đầu.
            bot_self_ids: Danh sách ID của bot.
            filter_commands: Có lọc tin nhắn lệnh hay không.

        Returns:
            Danh sách tin nhắn sau khi làm sạch.
        """
        bot_ids = set(bot_self_ids or [])
        cleaned_list = []

        for msg in messages:
            # 1. Lọc tin nhắn do bot gửi
            if msg.sender_id in bot_ids:
                continue

            # 2. Kiểm tra trước tin nhắn lệnh (khối nội dung đầu thường là văn bản)
            is_command = False
            first_text = msg.text_content
            if filter_commands and first_text and _COMMAND_PATTERN.match(first_text):
                is_command = True

            if is_command:
                continue

            # 3. Làm sạch nhiễu kỹ thuật trong nội dung tin nhắn
            cleaned_contents = []
            has_meaningful_content = False

            for content in msg.contents:
                if content.type == MessageContentType.TEXT:
                    text = content.text or ""

                    # Xoá mã biểu cảm gốc của Discord
                    text = _DISCORD_CUSTOM_EMOJI_PATTERN.sub("", text)

                    # Xoá văn bản @mention, ví dụ <@123456>
                    text = re.sub(r"<@\d+>", "", text)

                    # Xoá khoảng trắng thừa
                    text = text.strip()

                    if text:
                        cleaned_contents.append(
                            MessageContent(type=MessageContentType.TEXT, text=text)
                        )
                        has_meaningful_content = True
                else:
                    # Tạm giữ loại khác như ảnh và reply; analyzer quyết định việc sử dụng
                    cleaned_contents.append(content)
                    if content.type != MessageContentType.REPLY:
                        has_meaningful_content = True

            # 4. Chỉ giữ tin nhắn nếu vẫn còn nội dung sau khi làm sạch
            if has_meaningful_content:
                # Ghép lại text_content để phân tích bằng LLM
                new_text_content = "".join(
                    [
                        c.text
                        for c in cleaned_contents
                        if c.type == MessageContentType.TEXT
                    ]
                ).strip()

                # Dùng replace để tạo instance mới (dataclass frozen yêu cầu như vậy)
                new_msg = replace(
                    msg, contents=tuple(cleaned_contents), text_content=new_text_content
                )
                cleaned_list.append(new_msg)

        return cleaned_list
