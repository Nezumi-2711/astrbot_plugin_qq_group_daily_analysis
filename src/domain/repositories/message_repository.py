"""Giao diện repository tin nhắn độc lập với nền tảng."""

from abc import ABC, abstractmethod

from ..value_objects.platform_capabilities import PlatformCapabilities
from ..value_objects.unified_group import UnifiedGroup, UnifiedMember
from ..value_objects.unified_message import UnifiedMessage


class IMessageRepository(ABC):
    """
    Giao diện repository tin nhắn.

    Mỗi adapter nền tảng phải triển khai giao diện này. Tất cả phương thức
    trả về định dạng thống nhất để che giấu khác biệt giữa các nền tảng.
    """

    @abstractmethod
    async def fetch_messages(
        self,
        group_id: str,
        days: int = 1,
        max_count: int = 1000,
        before_id: str | None = None,
        since_ts: int | None = None,
    ) -> list[UnifiedMessage]:
        """
        Lấy lịch sử tin nhắn của nhóm.

        Args:
            group_id: ID của nhóm.
            days: Số ngày gần nhất cần lấy tin nhắn.
            max_count: Số lượng tin nhắn tối đa.
            before_id: Chỉ lấy tin nhắn trước ID này, dùng để phân trang.
            since_ts: Lấy tin nhắn từ Unix timestamp này; ưu tiên hơn ``days``.

        Returns:
            Danh sách tin nhắn thống nhất, sắp xếp tăng dần theo thời gian.
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> PlatformCapabilities:
        """Lấy mô tả năng lực của nền tảng."""
        pass

    @abstractmethod
    def get_platform_name(self) -> str:
        """Lấy tên nền tảng."""
        pass


class IMessageSender(ABC):
    """Giao diện gửi tin nhắn."""

    @abstractmethod
    async def send_text(
        self,
        group_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> bool:
        """Gửi tin nhắn văn bản."""
        pass

    @abstractmethod
    async def send_image(
        self,
        group_id: str,
        image_path: str,
        caption: str = "",
    ) -> bool:
        """Gửi tin nhắn hình ảnh."""
        pass

    @abstractmethod
    async def send_forward_msg(
        self,
        group_id: str,
        nodes: list[dict],
    ) -> bool:
        """
        Gửi tin nhắn chuyển tiếp tổng hợp.

        Args:
            group_id: ID nhóm đích.
            nodes: Danh sách nút chuyển tiếp. Mỗi nút thường chứa ``name``,
                ``uin`` (hoặc ``user_id``) và ``content``. Hiện chủ yếu dùng
                để tương thích với OneBot.
        """
        pass

    @abstractmethod
    async def send_file(
        self,
        group_id: str,
        file_path: str,
        filename: str | None = None,
    ) -> bool:
        """Gửi tệp."""
        pass


class IGroupInfoRepository(ABC):
    """Giao diện repository thông tin nhóm."""

    @abstractmethod
    async def get_group_info(self, group_id: str) -> UnifiedGroup | None:
        """Lấy thông tin nhóm."""
        pass

    @abstractmethod
    async def get_group_list(self) -> list[str]:
        """Lấy ID của tất cả nhóm mà bot đang tham gia."""
        pass

    @abstractmethod
    async def get_member_list(self, group_id: str) -> list[UnifiedMember]:
        """Lấy danh sách thành viên nhóm."""
        pass

    @abstractmethod
    async def get_member_info(
        self,
        group_id: str,
        user_id: str,
    ) -> UnifiedMember | None:
        """Lấy thông tin của thành viên được chỉ định."""
        pass
