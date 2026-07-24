"""
Dịch vụ thống kê thuộc tầng domain.

Phụ trách tính toán logic thống kê cốt lõi, không phụ thuộc nền tảng hoặc
cơ sở hạ tầng cụ thể.
"""

from collections import defaultdict
from datetime import datetime

from ...infrastructure.visualization.activity_charts import ActivityVisualizer
from ..models.data_models import EmojiStatistics, GroupStatistics, TokenUsage
from ..repositories.visualization_repository import IActivityVisualizer
from ..value_objects.unified_message import MessageContentType, UnifiedMessage


class StatisticsService:
    """Xử lý thống kê tổng hợp dữ liệu trò chuyện nhóm."""

    def __init__(self, activity_visualizer: IActivityVisualizer | None = None):
        if activity_visualizer is None:
            # Fallback: keep backward compatibility
            self.activity_visualizer: IActivityVisualizer = ActivityVisualizer()
        else:
            self.activity_visualizer = activity_visualizer

    def calculate_group_statistics(
        self, messages: list[UnifiedMessage]
    ) -> GroupStatistics:
        """
        Tính các số liệu thống kê cơ bản của nhóm.

        Tính toán dựa trên định dạng ``UnifiedMessage`` để đảm bảo kết quả
        nhất quán giữa các nền tảng.
        """
        total_chars = 0
        participants = set()
        hour_counts = defaultdict(int)
        emoji_statistics = EmojiStatistics()

        for msg in messages:
            participants.add(msg.sender_id)

            # Thống kê phân bố theo thời gian
            msg_time = datetime.fromtimestamp(msg.timestamp)
            hour_counts[msg_time.hour] += 1

            # Xử lý nội dung tin nhắn
            for content in msg.contents:
                if content.type == MessageContentType.TEXT:
                    total_chars += len(content.text or "")
                elif content.type == MessageContentType.EMOJI:
                    emoji_statistics.face_count += 1
                    # Giữ chi tiết biểu cảm gốc nếu adapter cung cấp
                    face_id = content.emoji_id or "unknown"
                    emoji_statistics.face_details[f"emoji_{face_id}"] = (
                        emoji_statistics.face_details.get(f"emoji_{face_id}", 0) + 1
                    )
                elif content.type == MessageContentType.IMAGE:
                    # Nhận diện tương thích biểu cảm ở dạng hình ảnh:
                    # 1) Ưu tiên tín hiệu sub_type=1 của OneBot
                    # 2) Nếu không có, đối chiếu văn bản summary kiểu cũ
                    if self._is_emoji_like_image(content.raw_data):
                        emoji_statistics.mface_count += 1
                elif content.type in (
                    MessageContentType.VOICE,
                    MessageContentType.VIDEO,
                ):
                    # Có thể bổ sung thống kê các loại phi văn bản khác
                    pass

        # Xác định khung giờ hoạt động tích cực nhất
        most_active_hour = (
            max(hour_counts.items(), key=lambda x: x[1])[0] if hour_counts else 0
        )
        most_active_period = (
            f"{most_active_hour:02d}:00-{(most_active_hour + 1) % 24:02d}:00"
        )

        # Tạo dữ liệu trực quan hoá hoạt động
        # ActivityVisualizer có thể cần được chuyển đổi để hỗ trợ UnifiedMessage.
        # Hiện tại chuyển ngược về dict để duy trì khả năng tương thích.
        raw_msgs = self._convert_to_legacy_dict(messages)
        activity_visualization = (
            self.activity_visualizer.generate_activity_visualization(raw_msgs)
        )

        return GroupStatistics(
            message_count=len(messages),
            total_characters=total_chars,
            participant_count=len(participants),
            most_active_period=most_active_period,
            golden_quotes=[],
            emoji_count=emoji_statistics.total_emoji_count,
            emoji_statistics=emoji_statistics,
            activity_visualization=activity_visualization,
            token_usage=TokenUsage(),
        )

    @staticmethod
    def _is_emoji_like_image(raw_data: object) -> bool:
        """Kiểm tra phân đoạn IMAGE có được tính là biểu cảm hay không."""
        if isinstance(raw_data, dict):
            sub_type = raw_data.get("sub_type")
            if sub_type is not None:
                return str(sub_type) == "1"
            summary = str(raw_data.get("summary", ""))
            return "动画表情" in summary or "表情" in summary

        if raw_data is None:
            return False

        text = str(raw_data)
        return "动画表情" in text or "表情" in text

    def _convert_to_legacy_dict(self, messages: list[UnifiedMessage]) -> list[dict]:
        """Chuyển ``UnifiedMessage`` sang dict cũ để tương thích trình trực quan."""
        legacy_list = []
        for msg in messages:
            legacy_list.append(
                {
                    "time": msg.timestamp,
                    "sender": {
                        "user_id": msg.sender_id,
                        "nickname": msg.sender_name,
                        "card": msg.sender_card or "",
                    },
                    "message": [
                        {"type": "text", "data": {"text": msg.text_content or ""}}
                    ],
                }
            )
        return legacy_list
