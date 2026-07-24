"""Lớp cơ sở cho adapter nền tảng."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from ...domain.repositories.avatar_repository import IAvatarRepository
from ...domain.repositories.message_repository import (
    IGroupInfoRepository,
    IMessageRepository,
    IMessageSender,
)
from ...domain.value_objects.platform_capabilities import PlatformCapabilities
from ...domain.value_objects.unified_message import UnifiedMessage


class PlatformAdapter(
    IMessageRepository, IMessageSender, IGroupInfoRepository, IAvatarRepository, ABC
):
    """
    Lớp cơ sở adapter ở tầng infrastructure.

    Kế thừa các giao diện domain về repository, gửi tin nhắn, thông tin nhóm
    và avatar; làm cầu nối giữa domain với nền tảng như OneBot hoặc Discord.

    Attributes:
        bot: Instance SDK bot của nền tảng.
        config: Cấu hình riêng của nền tảng.
    """

    bot: Any

    def __init__(
        self,
        bot_instance: Any,
        config: Mapping[str, Any] | None = None,
    ):
        """
        Khởi tạo adapter nền tảng.

        Args:
            bot_instance: Instance bot backend.
            config: Cấu hình riêng của nền tảng.
        """
        self.bot = bot_instance
        self.config: dict[str, object] = dict(config) if config is not None else {}
        self.bot_self_ids: list[str] = []
        self._capabilities: PlatformCapabilities | None = None

    def set_context(self, context: Any):
        """
        Thiết lập context cho nền tảng cần context như Telegram.

        Args:
            context: Đối tượng context.
        """
        pass

    @property
    def capabilities(self) -> PlatformCapabilities:
        """
        Lấy mô tả năng lực của nền tảng hiện tại.

        Dùng lazy loading và gọi ``_init_capabilities`` khi truy cập lần đầu.

        Returns:
            Đối tượng năng lực nền tảng.
        """
        if self._capabilities is None:
            self._capabilities = self._init_capabilities()
        return self._capabilities

    @abstractmethod
    def _init_capabilities(self) -> PlatformCapabilities:
        """
        Khởi tạo và trả về định nghĩa năng lực nền tảng hiện tại.

        Lớp con phải khai báo hỗ trợ lịch sử, gửi ảnh và các tính năng khác.

        Returns:
            Đối tượng năng lực đã khởi tạo.
        """
        raise NotImplementedError

    def get_capabilities(self) -> PlatformCapabilities:
        """Điểm vào tiện lợi để lấy năng lực nền tảng."""
        return self.capabilities

    def get_platform_name(self) -> str:
        """Lấy tên định danh nền tảng của adapter hiện tại."""
        return self.capabilities.platform_name

    @abstractmethod
    def convert_to_raw_format(self, messages: list[UnifiedMessage]) -> list[dict]:
        """
        Chuyển tin nhắn thống nhất độc lập nền tảng về dict gốc của nền tảng.

        Dùng để tương thích ngược với logic phân tích cũ phụ thuộc cấu trúc gốc.

        Args:
            messages: Danh sách tin nhắn thống nhất cần chuyển.

        Returns:
            Danh sách dict tin nhắn gốc của nền tảng.
        """
        raise NotImplementedError

    async def send_forward_msg(
        self,
        group_id: str,
        nodes: list[dict],
    ) -> bool:
        """
        Gửi tin nhắn chuyển tiếp gộp.

        Mặc định chuyển thành văn bản có định dạng và chia đoạn; adapter có thể ghi đè.
        """
        if not nodes:
            return True

        # Fallback chung: ghép node thành văn bản dài dễ đọc.
        lines = []
        for node in nodes:
            data = node.get("data", node)
            name = data.get("name", "Daily Analysis")
            content = data.get("content", "")
            if content:
                lines.append(f"【{name}】\n{content}")

        full_text = "\n\n".join(lines)

        # Chia văn bản dài ở ngưỡng an toàn 1.800 ký tự.
        max_chunk_size = 1800
        if len(full_text) > max_chunk_size:
            # Thử tách tại ký tự xuống dòng.
            chunks = []
            curr = full_text
            while len(curr) > max_chunk_size:
                # Tìm ký tự xuống dòng gần nhất.
                split_idx = curr.rfind("\n", 0, max_chunk_size)
                if split_idx == -1:
                    split_idx = max_chunk_size
                chunks.append(curr[:split_idx].strip())
                curr = curr[split_idx:].strip()
            if curr:
                chunks.append(curr)

            for chunk in chunks:
                if not await self.send_text(group_id, chunk):
                    return False
            return True
        else:
            return await self.send_text(group_id, full_text)

    async def set_reaction(
        self, group_id: str, message_id: str, emoji: str | int, is_add: bool = True
    ) -> bool:
        """
        Thêm hoặc xoá reaction cho tin nhắn.

        Args:
            group_id: ID nhóm/kênh.
            message_id: ID tin nhắn.
            emoji: Mã hoặc ký tự emoji.
            is_add: True để thêm, False để xoá.

        Returns:
            True nếu nền tảng hỗ trợ và thực thi thành công.
        """
        return False

    async def send_text_report(self, group_id: str, content: str) -> bool:
        """
        Gửi báo cáo văn bản dài theo cách phù hợp nhất với nền tảng.

        Mặc định chia thành node rồi gọi ``send_forward_msg``; adapter quyết định
        hình thức cuối như chuyển tiếp gộp hoặc gửi theo đoạn.
        """
        import re

        try:
            # 1. Chuẩn bị thông tin node cơ bản.
            self_id = self.bot_self_ids[0] if self.bot_self_ids else "bot"
            self_name = "Báo cáo phân tích"
            # 2. Chia thành đoạn logic theo tiêu đề và dòng trống.
            raw_content = str(content)
            sections = re.split(r"\n+(?=[🎯📊💬🏆])|\n{2,}", raw_content.strip())
            nodes = []

            for sec in sections:
                if not sec.strip():
                    continue
                nodes.append(
                    {
                        "type": "node",
                        "data": {
                            "name": self_name,
                            "uin": self_id,
                            "content": sec.strip(),
                        },
                    }
                )

            if not nodes:
                return await self.send_text(group_id, raw_content)

            # 3. Thử gửi chuyển tiếp hoặc chuỗi tin nhắn dài.
            return await self.send_forward_msg(group_id, nodes)
        except Exception:
            # Fallback: gửi trực tiếp.
            return await self.send_text(group_id, str(content))

    async def is_group_muted(self, group_id: str) -> bool:
        """
        Kiểm tra nhóm có tắt chat toàn bộ hoặc tắt quyền bot hay không.

        Mặc định trả về False; adapter có thể ghi đè khi cần.
        """
        return False
