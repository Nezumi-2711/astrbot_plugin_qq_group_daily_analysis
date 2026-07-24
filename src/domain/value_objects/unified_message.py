"""
Value object tin nhắn thống nhất - lớp trừu tượng cốt lõi đa nền tảng.

Tin nhắn từ mọi nền tảng đều được chuyển sang định dạng này để phân tích.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageContentType(Enum):
    """
    Enum loại nội dung tin nhắn.

    Dùng để xác định loại cụ thể của ``MessageContent``.
    """

    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    EMOJI = "emoji"
    REPLY = "reply"
    FORWARD = "forward"
    AT = "at"
    VOICE = "voice"
    VIDEO = "video"
    LOCATION = "location"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MessageContent:
    """
    Value object biểu diễn một phân đoạn nội dung tin nhắn.

    Biểu diễn một thành phần trong chuỗi tin nhắn, chẳng hạn văn bản,
    hình ảnh hoặc biểu cảm. Đối tượng bất biến để đảm bảo luồng dữ liệu sạch.

    Attributes:
        type: Loại nội dung.
        text: Nội dung văn bản khi loại là TEXT hoặc có mô tả văn bản.
        url: Liên kết tài nguyên như hình ảnh, video hoặc tệp.
        emoji_id: ID biểu cảm.
        emoji_name: Tên biểu cảm.
        at_user_id: ID thành viên được nhắc đến.
        raw_data: Dữ liệu nền tảng gốc dùng cho mục đích mở rộng.
    """

    type: MessageContentType
    text: str = ""
    url: str = ""
    emoji_id: str = ""
    emoji_name: str = ""
    at_user_id: str = ""
    raw_data: Any = None

    def is_text(self) -> bool:
        """Kiểm tra đây có phải nội dung văn bản hay không."""
        return self.type == MessageContentType.TEXT

    def is_emoji(self) -> bool:
        """Kiểm tra đây có phải nội dung biểu cảm hay không."""
        return self.type == MessageContentType.EMOJI

    @property
    def target_id(self) -> str:
        """
        Lấy ID thành viên được nhắc đến để tương thích mã cũ.

        Alias for at_user_id.
        """
        return self.at_user_id


@dataclass(frozen=True)
class UnifiedMessage:
    """
    Value object cốt lõi biểu diễn định dạng tin nhắn thống nhất.

    Lớp trừu tượng đa nền tảng chuyển tin nhắn gốc sang một định dạng chung
    để phân tích. Thiết kế chỉ đọc đảm bảo logic phân tích nhất quán.

    Attributes:
        message_id: Mã định danh duy nhất của tin nhắn.
        sender_id: ID duy nhất của người gửi.
        sender_name: Biệt danh người gửi.
        group_id: ID duy nhất của nhóm hoặc cuộc trò chuyện.
        text_content: Nội dung văn bản thuần đã làm sạch, chủ yếu dùng cho LLM.
        contents: Chuỗi nội dung tin nhắn có cấu trúc.
        timestamp: Unix timestamp tính bằng giây.
        platform: Tên nền tảng nguồn, ví dụ OneBot hoặc Discord.
        reply_to_id: ID tin nhắn được trả lời.
        sender_card: Tên hiển thị hoặc ghi chú riêng của nền tảng.
    """

    # Thông tin định danh cơ bản
    message_id: str
    sender_id: str
    sender_name: str
    group_id: str

    # Nội dung tin nhắn
    text_content: str
    contents: tuple[MessageContent, ...] = field(default_factory=tuple)

    # Thông tin thời gian
    timestamp: int = 0

    # Thông tin nền tảng
    platform: str = "unknown"

    # Thông tin tuỳ chọn
    reply_to_id: str | None = None
    sender_card: str | None = None

    # Phương thức hỗ trợ phân tích
    def has_text(self) -> bool:
        """
        Kiểm tra tin nhắn có chứa văn bản không rỗng hay không.

        Returns:
            ``True`` nếu tin nhắn chứa văn bản hợp lệ.
        """
        return bool(self.text_content.strip())

    def get_display_name(self) -> str:
        """
        Lấy tên hiển thị của thành viên.

        Thứ tự ưu tiên: tên trong nhóm, biệt danh, ID thành viên.

        Returns:
            Tên hiển thị đã định dạng.
        """
        return self.sender_card or self.sender_name or self.sender_id

    def get_emoji_count(self) -> int:
        """
        Tính số biểu cảm có trong chuỗi tin nhắn.

        Returns:
            Tổng số biểu cảm.
        """
        return sum(1 for c in self.contents if c.is_emoji())

    def get_text_length(self) -> int:
        """
        Lấy độ dài ký tự của nội dung văn bản.

        Returns:
            Số ký tự.
        """
        return len(self.text_content)

    def get_datetime(self) -> datetime:
        """
        Chuyển Unix timestamp thành đối tượng ``datetime``.

        Returns:
            Đối tượng thời gian theo múi giờ cục bộ.
        """
        return datetime.fromtimestamp(self.timestamp)

    def to_analysis_format(self) -> str:
        """
        Chuyển sang định dạng phân tích dành cho LLM.

        Returns:
            Chuỗi có dạng ``[tên thành viên]: nội dung tin nhắn``.
        """
        name = self.get_display_name()
        return f"[{name}]: {self.text_content}"


# Bí danh kiểu dữ liệu
MessageList = list[UnifiedMessage]
