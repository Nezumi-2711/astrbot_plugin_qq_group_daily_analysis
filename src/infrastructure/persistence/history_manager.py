"""
Module quản lý lịch sử thuộc tầng persistence infrastructure.

Phụ trách lưu và truy vấn thông tin tóm tắt của báo cáo phân tích nhóm,
sử dụng put_kv_data/get_kv_data của AstrBot.
"""

import datetime
from typing import Any

from ...utils.logger import logger


class HistoryManager:
    """
    Thành phần cốt lõi: trình quản lý lưu trữ lịch sử phân tích.

    Lưu bản tóm tắt báo cáo phân tích nhóm hằng ngày và cung cấp giao diện truy vấn.
    Dựa trên KV của AstrBot để có thể truy xuất dữ liệu sau khi bot khởi động lại.
    """

    def __init__(self, star_instance: Any):
        """
        Khởi tạo trình quản lý lịch sử.

        Args:
            star_instance: Instance Star dùng để truy cập persistence engine.
        """
        self.plugin = star_instance

    async def save_analysis(
        self,
        group_id: str,
        analysis_result: dict[str, Any],
        date_str: str | None = None,
        time_str: str | None = None,
    ) -> bool:
        """
        Tuần tự hoá và lưu một bản tóm tắt báo cáo phân tích.

        Bản tóm tắt gồm tổng tin nhắn, số thành viên, chủ đề và thời gian tạo;
        không chứa toàn bộ luồng tin nhắn gốc.

        Args:
            group_id: ID nhóm.
            analysis_result: Đối tượng phân tích gồm statistics, topics và user_titles.
            date_str: Ngày lưu trữ (YYYY-MM-DD), mặc định là hôm nay.
            time_str: Thời điểm lưu (HH-MM), mặc định là hiện tại.

        Returns:
            True nếu lưu thành công.
        """
        try:
            now = datetime.datetime.now()
            if not date_str:
                date_str = now.strftime("%Y-%m-%d")
            if not time_str:
                time_str = now.strftime("%H-%M")

            # Loại ký tự không hợp lệ để key tương thích.
            time_str = time_str.replace(":", "-")

            # Loại trường không cần lưu và lấy metadata thống kê cốt lõi.
            stats = analysis_result.get("statistics")
            topics = analysis_result.get("topics", [])
            user_titles = analysis_result.get("user_titles", [])

            summary = {
                "message_count": getattr(stats, "message_count", 0) if stats else 0,
                "participant_count": getattr(stats, "participant_count", 0)
                if stats
                else 0,
                "topics": [{"topic": t.topic, "detail": t.detail} for t in topics],
                "user_titles_count": len(user_titles),
                "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }

            key = f"analysis_{group_id}_{date_str}_{time_str}"
            await self.plugin.put_kv_data(key, summary)

            logger.info(
                f"Đã lưu tóm tắt phân tích của nhóm {group_id} lúc {date_str} {time_str} (Key: {key})"
            )
            return True
        except Exception as e:
            logger.error(f"Lưu bản ghi phân tích lịch sử thất bại: {e}", exc_info=True)
            return False

    async def get_history(
        self, group_id: str, date_str: str, time_str: str
    ) -> dict[str, Any] | None:
        """
        Truy vấn một bản tóm tắt lịch sử theo nhóm, ngày và thời điểm.
        """
        time_str = time_str.replace(":", "-")
        key = f"analysis_{group_id}_{date_str}_{time_str}"
        return await self.plugin.get_kv_data(key, None)

    async def has_history(self, group_id: str, date_str: str, time_str: str) -> bool:
        """
        Kiểm tra nhanh bản ghi lịch sử tại thời điểm chỉ định có tồn tại hay không.
        """
        history = await self.get_history(group_id, date_str, time_str)
        return history is not None
