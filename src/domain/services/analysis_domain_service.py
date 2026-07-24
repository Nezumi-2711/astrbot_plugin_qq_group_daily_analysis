"""
Dịch vụ phân tích thuộc tầng domain.

Phụ trách phân tích mức độ hoạt động, thói quen trò chuyện và nhận diện
mẫu hoạt động của từng thành viên.
"""

from datetime import datetime
from typing import TypedDict

from ..value_objects.unified_message import MessageContentType, UnifiedMessage


class UserActivityStats(TypedDict):
    message_count: int
    char_count: int
    emoji_count: int
    nickname: str
    hours: dict[int, int]
    reply_count: int


class AnalysisDomainService:
    """Xử lý phân tích chân dung và hành vi thành viên."""

    def analyze_user_activity(
        self,
        messages: list[UnifiedMessage],
        bot_self_ids: list[str] | None = None,
    ) -> dict[str, UserActivityStats]:
        """
        Phân tích mức độ hoạt động của thành viên.

        Dựa trên ``UnifiedMessage`` để tính số tin nhắn, số ký tự,
        số biểu cảm và các chỉ số khác của từng thành viên.
        """
        user_stats: dict[str, UserActivityStats] = {}

        bot_ids = set(bot_self_ids or [])

        for msg in messages:
            user_id = msg.sender_id

            # Bỏ qua tin nhắn của chính bot
            if user_id in bot_ids:
                continue

            stats = user_stats.setdefault(
                user_id,
                {
                    "message_count": 0,
                    "char_count": 0,
                    "emoji_count": 0,
                    "nickname": "",
                    "hours": {},
                    "reply_count": 0,
                },
            )
            stats["message_count"] += 1
            stats["nickname"] = msg.sender_card or msg.sender_name

            # Thống kê phân bố theo thời gian
            msg_time = datetime.fromtimestamp(msg.timestamp)
            hour = msg_time.hour
            stats["hours"][hour] = stats["hours"].get(hour, 0) + 1

            # Thống kê nội dung
            for content in msg.contents:
                if content.type == MessageContentType.TEXT:
                    stats["char_count"] += len(content.text or "")

                elif content.type == MessageContentType.EMOJI:
                    stats["emoji_count"] += 1

                elif content.type == MessageContentType.IMAGE:
                    # Giữ cách tính nhất quán với GroupStatistics
                    if self._is_emoji_like_image(content.raw_data):
                        stats["emoji_count"] += 1

                elif content.type == MessageContentType.REPLY:
                    stats["reply_count"] += 1

        return user_stats

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

    def get_top_users(
        self, user_activity: dict[str, UserActivityStats], limit: int = 10
    ) -> list[dict]:
        """Lấy danh sách thành viên hoạt động tích cực nhất."""
        users = []
        for user_id, stats in user_activity.items():
            users.append(
                {
                    "user_id": user_id,
                    "nickname": stats["nickname"],
                    "message_count": stats["message_count"],
                    "char_count": stats["char_count"],
                    "emoji_count": stats["emoji_count"],
                    "reply_count": stats["reply_count"],
                }
            )

        # Sắp xếp theo số lượng tin nhắn
        users.sort(key=lambda x: x["message_count"], reverse=True)
        return users[:limit]

    def get_user_activity_pattern(
        self, user_activity: dict[str, UserActivityStats], user_id: str
    ) -> dict:
        """Lấy và nhận diện mẫu hoạt động của thành viên được chỉ định."""
        if user_id not in user_activity:
            return {}

        stats = user_activity[user_id]
        hours = stats["hours"]

        # Xác định khung giờ hoạt động tích cực nhất
        most_active_hour = max(hours.items(), key=lambda x: x[1])[0] if hours else 0

        # Tính mức độ hoạt động ban đêm (0-6 giờ)
        night_messages = sum(hours[h] for h in range(0, 6))
        night_ratio = (
            night_messages / stats["message_count"] if stats["message_count"] > 0 else 0
        )

        return {
            "most_active_hour": most_active_hour,
            "night_ratio": night_ratio,
            "hourly_distribution": dict(hours),
        }
