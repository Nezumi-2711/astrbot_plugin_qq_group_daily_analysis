"""
Entity phân tích gia tăng theo kiến trúc lưu batch trong cửa sổ trượt.

Khái niệm cốt lõi:
- IncrementalBatch: dữ liệu độc lập sinh ra từ một lần phân tích gia tăng
- IncrementalState: view tổng hợp từ nhiều batch khi tạo báo cáo, không lưu bền vững

Thiết kế cửa sổ trượt:
- Mỗi lần phân tích gia tăng tạo một IncrementalBatch và lưu riêng vào KV
- Khi tạo báo cáo cuối, truy vấn và gộp batch theo cửa sổ analysis_days × 24 giờ
- Có thể gửi nhiều báo cáo trong ngày, mỗi báo cáo dựa trên mọi batch trong cửa sổ hiện tại
"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class IncrementalBatch:
    """
    Dữ liệu của một batch phân tích gia tăng.

    Mỗi lần phân tích gia tăng hoàn tất sẽ tạo một ``IncrementalBatch`` chứa
    toàn bộ số liệu thống kê và kết quả LLM của batch, được lưu riêng vào KV.

    Attributes:
        group_id: ID nhóm.
        batch_id: UUID duy nhất của batch.
        timestamp: Epoch timestamp khi tạo batch.
        messages_count: Số tin nhắn được phân tích trong batch.
        characters_count: Tổng số ký tự trong batch.
        hourly_msg_counts: Số tin nhắn theo giờ.
        hourly_char_counts: Số ký tự theo giờ.
        user_stats: Thống kê thành viên.
        emoji_stats: Thống kê biểu cảm.
        topics: Danh sách chủ đề trích xuất từ batch.
        golden_quotes: Danh sách trích dẫn nổi bật từ batch.
        token_usage: Mức sử dụng token của batch.
        chat_quality_review: Đánh giá chất lượng trò chuyện của batch.
        last_message_timestamp: Timestamp tin nhắn cuối trong batch.
        participant_ids: Danh sách ID người tham gia trong batch.
    """

    group_id: str = ""
    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    # Dữ liệu thống kê
    messages_count: int = 0
    characters_count: int = 0
    hourly_msg_counts: dict[str, int] = field(default_factory=dict)
    hourly_char_counts: dict[str, int] = field(default_factory=dict)

    # Dữ liệu hoạt động của thành viên
    user_stats: dict[str, dict] = field(default_factory=dict)

    # Thống kê biểu cảm
    emoji_stats: dict[str, Any] = field(default_factory=dict)

    # Kết quả phân tích bằng LLM
    topics: list[dict] = field(default_factory=list)
    golden_quotes: list[dict] = field(default_factory=list)

    # Mức sử dụng token
    token_usage: dict = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )

    # Theo dõi phân tích gia tăng
    chat_quality_review: dict[str, Any] | None = None
    last_message_timestamp: int = 0
    participant_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Tuần tự hoá thành dict để lưu trong KV."""
        return {
            "group_id": self.group_id,
            "batch_id": self.batch_id,
            "timestamp": self.timestamp,
            "messages_count": self.messages_count,
            "characters_count": self.characters_count,
            "hourly_msg_counts": self.hourly_msg_counts,
            "hourly_char_counts": self.hourly_char_counts,
            "user_stats": self.user_stats,
            "emoji_stats": self.emoji_stats,
            "topics": self.topics,
            "golden_quotes": self.golden_quotes,
            "token_usage": self.token_usage,
            "chat_quality_review": self.chat_quality_review,
            "last_message_timestamp": self.last_message_timestamp,
            "participant_ids": self.participant_ids,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IncrementalBatch":
        """Khôi phục đối tượng từ dict."""
        return cls(
            group_id=data.get("group_id", ""),
            batch_id=data.get("batch_id", ""),
            timestamp=data.get("timestamp", 0.0),
            messages_count=data.get("messages_count", 0),
            characters_count=data.get("characters_count", 0),
            hourly_msg_counts=data.get("hourly_msg_counts", {}),
            hourly_char_counts=data.get("hourly_char_counts", {}),
            user_stats=data.get("user_stats", {}),
            emoji_stats=data.get("emoji_stats", {}),
            topics=data.get("topics", []),
            golden_quotes=data.get("golden_quotes", []),
            token_usage=data.get(
                "token_usage",
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            ),
            chat_quality_review=data.get("chat_quality_review"),
            last_message_timestamp=data.get("last_message_timestamp", 0),
            participant_ids=data.get("participant_ids", []),
        )

    def get_summary(self) -> dict:
        """Lấy thông tin tóm tắt của batch."""
        return {
            "batch_id": self.batch_id[:8],
            "timestamp": datetime.fromtimestamp(self.timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "messages_count": self.messages_count,
            "topics_count": len(self.topics),
            "quotes_count": len(self.golden_quotes),
            "participants": len(self.participant_ids),
        }


@dataclass
class IncrementalState:
    """
    View tổng hợp phân tích gia tăng dùng khi tạo báo cáo.

    Được gộp từ nhiều ``IncrementalBatch`` và không lưu trực tiếp.
    ``IncrementalMergeService.merge_batches()`` xây dựng đối tượng từ danh sách batch.

    Attributes:
        group_id: ID nhóm.
        window_start: Timestamp bắt đầu cửa sổ trượt.
        window_end: Timestamp kết thúc cửa sổ trượt.
        topics: Danh sách chủ đề đã gộp và loại trùng.
        golden_quotes: Danh sách trích dẫn đã gộp và loại trùng.
        hourly_message_counts: Số tin nhắn theo giờ sau khi gộp.
        hourly_character_counts: Số ký tự theo giờ sau khi gộp.
        user_activities: Dữ liệu hoạt động thành viên sau khi gộp.
        emoji_counts: Thống kê biểu cảm sau khi gộp.
        total_message_count: Tổng số tin nhắn trong cửa sổ.
        total_character_count: Tổng số ký tự trong cửa sổ.
        total_analysis_count: Số batch trong cửa sổ.
        total_token_usage: Tổng mức sử dụng token.
        last_analyzed_message_timestamp: Timestamp tin nhắn được phân tích cuối cùng.
        all_participant_ids: Tập hợp ID của tất cả người tham gia.
    """

    # Thông tin định danh
    group_id: str = ""
    window_start: float = 0.0
    window_end: float = 0.0

    # Kết quả phân tích LLM sau khi gộp
    topics: list[dict] = field(default_factory=list)
    golden_quotes: list[dict] = field(default_factory=list)
    chat_quality_review: dict[str, Any] | None = None
    all_quality_reviews: list[dict] = field(
        default_factory=list
    )  # Lưu đánh giá của mọi batch để tổng hợp khi tạo báo cáo cuối

    # Dữ liệu thống kê theo giờ sau khi gộp
    hourly_message_counts: dict[str, int] = field(default_factory=dict)
    hourly_character_counts: dict[str, int] = field(default_factory=dict)

    # Dữ liệu hoạt động của thành viên
    user_activities: dict[str, dict] = field(default_factory=dict)

    # Thống kê biểu cảm
    emoji_counts: dict[str, Any] = field(default_factory=dict)

    # Thống kê tổng hợp
    total_message_count: int = 0
    total_character_count: int = 0
    total_analysis_count: int = 0
    total_token_usage: dict = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )

    # Theo dõi phân tích gia tăng
    last_analyzed_message_timestamp: int = 0
    all_participant_ids: set[str] = field(default_factory=set)

    # Metadata
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def get_peak_hours(self, top_n: int = 3) -> list[int]:
        """
        Lấy các giờ có nhiều tin nhắn nhất.

        Args:
            top_n: Số giờ hoạt động tích cực nhất cần trả về.

        Returns:
            Danh sách giờ, sắp xếp giảm dần theo số tin nhắn.
        """
        if not self.hourly_message_counts:
            return []
        sorted_hours = sorted(
            self.hourly_message_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        return [int(h) for h, _ in sorted_hours[:top_n]]

    def get_most_active_period(self) -> str:
        """
        Lấy chuỗi mô tả khung giờ hoạt động tích cực nhất.

        Returns:
            Chuỗi dạng ``20:00-21:00``.
        """
        peak = self.get_peak_hours(1)
        if not peak:
            return "Không xác định"
        hour = peak[0]
        return f"{hour:02d}:00-{hour + 1:02d}:00"

    def get_user_activity_ranking(self, top_n: int = 10) -> list[dict]:
        """
        Lấy bảng xếp hạng mức độ hoạt động của thành viên.

        Args:
            top_n: Số thành viên đứng đầu cần trả về.

        Returns:
            Danh sách thành viên sắp xếp giảm dần theo số tin nhắn.
        """
        users = []
        for user_id, data in self.user_activities.items():
            users.append(
                {
                    "user_id": user_id,
                    "name": data.get("nickname", data.get("name", user_id)),
                    "message_count": data.get("message_count", 0),
                    "char_count": data.get("char_count", 0),
                }
            )
        users.sort(key=lambda x: x["message_count"], reverse=True)
        return users[:top_n]

    def get_window_date_str(self) -> str:
        """
        Lấy chuỗi phạm vi ngày của cửa sổ để hiển thị trong báo cáo.

        Returns:
            Chuỗi như ``2024-01-15`` hoặc ``2024-01-14 ~ 2024-01-15``.
        """
        if self.window_start <= 0 or self.window_end <= 0:
            return datetime.now().strftime("%Y-%m-%d")

        start_date = datetime.fromtimestamp(self.window_start).strftime("%Y-%m-%d")
        end_date = datetime.fromtimestamp(self.window_end).strftime("%Y-%m-%d")

        if start_date == end_date:
            return end_date
        return f"{start_date} ~ {end_date}"

    def get_summary(self) -> dict:
        """
        Lấy tóm tắt trạng thái gia tăng cho lệnh truy vấn trạng thái.

        Returns:
            Bản tóm tắt chứa các số liệu thống kê chính.
        """
        return {
            "group_id": self.group_id,
            "window": self.get_window_date_str(),
            "total_messages": self.total_message_count,
            "total_characters": self.total_character_count,
            "total_analyses": self.total_analysis_count,
            "topics_count": len(self.topics),
            "quotes_count": len(self.golden_quotes),
            "participants": len(self.all_participant_ids),
            "total_tokens": self.total_token_usage.get("total_tokens", 0),
            "last_analysis_time": (
                datetime.fromtimestamp(self.updated_at).strftime("%H:%M:%S")
                if self.updated_at
                else "Không có"
            ),
            "peak_hours": self.get_peak_hours(3),
        }

    @staticmethod
    def is_duplicate_topic(
        new_topic: dict, existing_topics: list[dict], threshold: float = 0.6
    ) -> bool:
        """
        Kiểm tra chủ đề có trùng với chủ đề hiện có hay không.

        Dùng độ tương đồng giao nhau ký tự đơn giản. Nếu độ tương đồng giữa
        tên chủ đề mới và tên hiện có vượt ngưỡng thì coi là trùng lặp.

        Args:
            new_topic: Chủ đề mới cần kiểm tra.
            existing_topics: Danh sách chủ đề hiện có.
            threshold: Ngưỡng tương đồng từ 0 đến 1, mặc định là 0.6.

        Returns:
            Có trùng lặp hay không.
        """
        new_name = new_topic.get("topic", "")
        if not new_name:
            return False

        for existing in existing_topics:
            existing_name = existing.get("topic", "")
            if not existing_name:
                continue
            similarity = IncrementalState.char_overlap_similarity(
                new_name, existing_name
            )
            if similarity >= threshold:
                return True
        return False

    @staticmethod
    def is_duplicate_quote(
        new_quote: dict, existing_quotes: list[dict], threshold: float = 0.7
    ) -> bool:
        """
        Kiểm tra trích dẫn có trùng với trích dẫn hiện có hay không.

        Args:
            new_quote: Trích dẫn mới cần kiểm tra.
            existing_quotes: Danh sách trích dẫn hiện có.
            threshold: Ngưỡng tương đồng từ 0 đến 1, mặc định là 0.7.

        Returns:
            Có trùng lặp hay không.
        """
        new_content = new_quote.get("content", "")
        if not new_content:
            return False

        for existing in existing_quotes:
            existing_content = existing.get("content", "")
            if not existing_content:
                continue
            similarity = IncrementalState.char_overlap_similarity(
                new_content, existing_content
            )
            if similarity >= threshold:
                return True
        return False

    @staticmethod
    def char_overlap_similarity(s1: str, s2: str) -> float:
        """
        Tính độ tương đồng giao nhau ký tự của hai chuỗi theo hệ số Jaccard.

        Args:
            s1: Chuỗi thứ nhất.
            s2: Chuỗi thứ hai.

        Returns:
            Độ tương đồng từ 0 đến 1.
        """
        if not s1 or not s2:
            return 0.0
        set1 = set(s1)
        set2 = set(s2)
        intersection = set1 & set2
        union = set1 | set2
        if not union:
            return 0.0
        return len(intersection) / len(union)
