"""
Trình gửi tin nhắn thuộc tầng infrastructure.

Cung cấp giao diện gửi cấp cao với khả năng định tuyến đa nền tảng.
"""

from ...utils.logger import logger


class MessageSender:
    """Đóng gói lệnh gọi PlatformAdapter và cung cấp giao diện gửi cấp cao."""

    def __init__(self, bot_manager, config_manager):
        self.bot_manager = bot_manager
        self.config_manager = config_manager

    async def send_text(
        self, group_id: str, text: str, platform_id: str | None = None
    ) -> bool:
        """Gửi tin nhắn văn bản."""
        adapter = self.bot_manager.get_adapter(platform_id)
        if not adapter:
            logger.error(
                f"[MessageSender] Không tìm thấy adapter cho nền tảng {platform_id}"
            )
            return False
        return await adapter.send_text(group_id, text)

    async def send_image_smart(
        self,
        group_id: str,
        image_url: str,
        caption: str = "",
        platform_id: str | None = None,
    ) -> bool:
        """Gửi ảnh và tự động chọn adapter phù hợp."""
        adapter = self.bot_manager.get_adapter(platform_id)
        if not adapter:
            logger.error(
                f"[MessageSender] Không tìm thấy adapter cho nền tảng {platform_id}"
            )
            return False
        return await adapter.send_image(group_id, image_url, caption)

    async def send_file(
        self,
        group_id: str,
        file_path: str,
        caption: str = "",
        platform_id: str | None = None,
    ) -> bool:
        """Gửi tệp HTML, PDF hoặc loại khác với caption tuỳ chọn."""
        adapter = self.bot_manager.get_adapter(platform_id)
        if not adapter:
            logger.error(
                f"[MessageSender] Không tìm thấy adapter cho nền tảng {platform_id}"
            )
            return False

        # Gửi tệp trước; kết quả phương thức chỉ phản ánh việc gửi tệp.
        file_sent = await adapter.send_file(group_id, file_path)

        if not file_sent:
            # Adapter trả False nghĩa là tệp chưa được gửi thành công.
            return False

        # Sau khi gửi tệp, caption được gửi theo nỗ lực tối đa và không đổi kết quả.
        if caption:
            try:
                caption_sent = await adapter.send_text(group_id, f"{caption}")
                if not caption_sent:
                    logger.warning(
                        "[MessageSender] Đã gửi tệp nhưng gửi caption thất bại (adapter trả False)"
                    )
            except Exception as e:
                logger.warning(
                    f"[MessageSender] Đã gửi tệp nhưng xảy ra lỗi khi gửi caption: {e}"
                )

        return True

    def _get_available_platforms(self, group_id: str):
        """Lấy danh sách nền tảng khả dụng cho Dispatcher."""
        # Triển khai đơn giản: trả về mọi nền tảng đã nạp.
        return [(pid, None) for pid in self.bot_manager.get_platform_ids()]
