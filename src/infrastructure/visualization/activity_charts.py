"""Module trực quan hoá mức độ hoạt động của nhóm."""

from collections import defaultdict
from datetime import datetime

from ...domain.models.data_models import ActivityVisualization
from ...domain.repositories.visualization_repository import IActivityVisualizer


class ActivityVisualizer(IActivityVisualizer):
    """Trình trực quan hoá mức độ hoạt động."""

    def __init__(self):
        pass

    def generate_activity_visualization(
        self, messages: list[dict]
    ) -> ActivityVisualization:
        """Tạo dữ liệu trực quan hoạt động theo giờ."""
        hourly_activity = defaultdict(int)
        user_activity = defaultdict(int)
        emoji_activity = defaultdict(int)  # Thống kê biểu cảm theo giờ.

        # Phân tích dữ liệu tin nhắn.
        for msg in messages:
            # Chỉ phân tích theo giờ.
            msg_time = datetime.fromtimestamp(msg.get("time", 0))
            hour = msg_time.hour

            # # Phân tích thành viên.
            # sender = msg.get("sender", {})
            # user_id = str(sender.get("user_id", ""))
            # nickname = InfoUtils.get_user_nickname(self.config_manager, sender)

            # Đếm tin nhắn theo giờ.
            hourly_activity[hour] += 1

            # # Thống kê hoạt động thành viên.
            # user_activity[user_id] = {
            #     "nickname": nickname,
            #     "count": user_activity.get(user_id, {}).get("count", 0) + 1
            # }

            # Đếm biểu cảm theo giờ.
            for content in msg.get("message", []):
                if content.get("type") in ["face", "mface", "bface", "sface"]:
                    emoji_activity[hour] += 1
                elif content.get("type") == "image":
                    data = content.get("data", {})
                    summary = data.get("summary", "")
                    if "动画表情" in summary or "表情" in summary:
                        emoji_activity[hour] += 1

        # Tạo bảng xếp hạng hoạt động thành viên.
        user_ranking = []
        for user_id, data in user_activity.items():
            user_ranking.append(
                {
                    "user_id": user_id,
                    "nickname": data["nickname"],
                    "message_count": data["count"],
                }
            )
        user_ranking.sort(key=lambda x: x["message_count"], reverse=True)

        # Tìm ba khung giờ hoạt động cao nhất.
        peak_hours = sorted(hourly_activity.items(), key=lambda x: x[1], reverse=True)[
            :3
        ]
        peak_hours = [{"hour": hour, "count": count} for hour, count in peak_hours]

        return ActivityVisualization(
            hourly_activity=dict(hourly_activity),
            daily_activity={},  # Không phân tích theo ngày.
            user_activity_ranking=user_ranking[:10],  # Top 10.
            peak_hours=peak_hours,
            activity_heatmap_data=self._generate_hourly_heatmap_data(
                hourly_activity, emoji_activity
            ),
        )

    def _generate_hourly_heatmap_data(
        self, hourly_activity: dict, emoji_activity: dict
    ) -> dict:
        """Tạo dữ liệu heatmap theo giờ."""
        # Tính cấp độ hoạt động.
        max_hourly = max(hourly_activity.values()) if hourly_activity else 1
        max_emoji = max(emoji_activity.values()) if emoji_activity else 1

        return {
            "hourly_max": max_hourly,
            "emoji_max": max_emoji,
            "hourly_normalized": {
                hour: (count / max_hourly) * 100
                for hour, count in hourly_activity.items()
            },
            "emoji_normalized": {
                hour: (emoji_activity.get(hour, 0) / max_emoji) * 100
                for hour in range(24)
            },
            "activity_levels": self._calculate_activity_levels(hourly_activity),
        }

    def _calculate_activity_levels(self, hourly_activity: dict) -> dict:
        """Tính cấp độ hoạt động."""
        if not hourly_activity:
            return {}

        max_count = max(hourly_activity.values())
        levels = {}

        for hour in range(24):
            count = hourly_activity.get(hour, 0)
            if count == 0:
                level = "inactive"
            elif count <= max_count * 0.3:
                level = "low"
            elif count <= max_count * 0.7:
                level = "medium"
            else:
                level = "high"
            levels[hour] = level

        return levels

    def get_hourly_chart_data(self, hourly_activity: dict) -> list[dict]:
        """Tạo dữ liệu phân bố hoạt động theo giờ."""
        chart_data = []
        max_activity = max(hourly_activity.values()) if hourly_activity else 1

        for hour in range(24):
            count = hourly_activity.get(hour, 0)
            percentage = (count / max_activity) * 100 if max_activity > 0 else 0

            chart_data.append(
                {"hour": hour, "count": count, "percentage": round(percentage, 1)}
            )

        return chart_data
