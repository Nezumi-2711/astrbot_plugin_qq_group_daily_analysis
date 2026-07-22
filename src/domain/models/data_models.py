"""
Định nghĩa các mô hình dữ liệu.

Chứa tất cả cấu trúc dữ liệu liên quan đến quá trình phân tích.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SummaryTopic:
    """Cấu trúc dữ liệu tóm tắt chủ đề."""

    topic: str
    contributors: list[str]
    detail: str
    contributor_ids: list[str] = field(
        default_factory=list
    )  # Danh sách ID người đóng góp (dùng để hiển thị ảnh đại diện)


@dataclass
class UserTitle:
    """Cấu trúc dữ liệu danh hiệu người dùng."""

    name: str
    user_id: str  # Trường qq trước đây
    title: str
    mbti: str
    reason: str


@dataclass
class GoldenQuote:
    """Cấu trúc dữ liệu câu nói nổi bật trong nhóm chat."""

    content: str
    sender: str
    reason: str
    user_id: str = ""  # Trường qq trước đây


@dataclass
class QualityDimension:
    """Cấu trúc dữ liệu tiêu chí chất lượng trò chuyện."""

    name: str  # Tên tiêu chí
    percentage: float  # Tỷ lệ phần trăm
    comment: str  # Nhận xét nổi bật
    color: str = "#607d8b"  # Màu sắc


@dataclass
class QualityReview:
    """Cấu trúc dữ liệu đánh giá chất lượng trò chuyện."""

    title: str
    subtitle: str
    dimensions: list[QualityDimension]
    summary: str


@dataclass
class TokenUsage:
    """Thống kê mức sử dụng token."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class EmojiStatistics:
    """Cấu trúc dữ liệu thống kê biểu cảm."""

    face_count: int = 0  # Số biểu cảm QQ cơ bản
    mface_count: int = 0  # Số biểu cảm động
    bface_count: int = 0  # Số siêu biểu cảm
    sface_count: int = 0  # Số biểu cảm nhỏ
    other_emoji_count: int = 0  # Số biểu cảm khác
    face_details: dict = field(default_factory=dict)  # Thống kê theo ID biểu cảm

    @property
    def total_emoji_count(self) -> int:
        """Trả về tổng số biểu cảm."""
        return (
            self.face_count
            + self.mface_count
            + self.bface_count
            + self.sface_count
            + self.other_emoji_count
        )


@dataclass
class ActivityVisualization:
    """Cấu trúc dữ liệu trực quan hóa mức độ hoạt động."""

    hourly_activity: dict = field(default_factory=dict)  # {hour: count}
    daily_activity: dict = field(default_factory=dict)  # {date: count}
    user_activity_ranking: list = field(default_factory=list)  # Xếp hạng hoạt động
    peak_hours: list = field(default_factory=list)  # Khung giờ cao điểm
    activity_heatmap_data: dict = field(default_factory=dict)  # Dữ liệu bản đồ nhiệt


@dataclass
class GroupStatistics:
    """Cấu trúc dữ liệu thống kê nhóm chat."""

    message_count: int
    total_characters: int
    participant_count: int
    most_active_period: str
    golden_quotes: list[GoldenQuote]
    emoji_count: int  # Duy trì khả năng tương thích ngược
    emoji_statistics: EmojiStatistics = field(default_factory=EmojiStatistics)
    activity_visualization: ActivityVisualization = field(
        default_factory=ActivityVisualization
    )
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    chat_quality_review: Optional["QualityReview"] = None
