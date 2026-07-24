"""
Repository lịch sử - triển khai lưu trữ lịch sử phân tích.

Module cung cấp persistence cho kết quả phân tích và bản ghi lịch sử,
đóng gói chức năng history_manager hiện có.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ...utils.logger import logger


class HistoryRepository:
    """
    Infrastructure: repository lịch sử.

    Lưu và truy xuất lịch sử phân tích nhóm bằng file JSON cục bộ,
    duy trì tương thích định dạng dữ liệu với history_manager cũ.

    Attributes:
        data_dir: Thư mục gốc lưu dữ liệu plugin.
        history_dir: Thư mục con lưu lịch sử.
    """

    def __init__(self, data_dir: str):
        """
        Khởi tạo repository lịch sử.

        Args:
            data_dir: Đường dẫn thư mục cơ sở lưu dữ liệu lịch sử.
        """
        self.data_dir = Path(data_dir)
        self.history_dir = self.data_dir / "history"
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Đảm bảo cấu trúc thư mục cần thiết đã tồn tại."""
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _get_group_history_path(self, group_id: str) -> Path:
        """Lấy đường dẫn JSON lịch sử của nhóm."""
        return self.history_dir / f"group_{group_id}.json"

    def save_analysis_result(
        self,
        group_id: str,
        result: dict[str, Any],
        date_str: str | None = None,
    ) -> bool:
        """
        Lưu kết quả phân tích vào persistence.

        Args:
            group_id: Định danh nhóm.
            result: Dict kết quả gồm thống kê, trích dẫn và thông tin liên quan.
            date_str: Ngày liên quan (YYYY-MM-DD), mặc định là ngày thực thi.

        Returns:
            True nếu lưu thành công, False nếu xảy ra lỗi.
        """
        try:
            date_str = date_str or datetime.now().strftime("%Y-%m-%d")
            history = self.load_group_history(group_id)

            # Gắn timestamp thực thi.
            if "timestamp" not in result:
                result["timestamp"] = datetime.now().isoformat()

            # Lưu có cấu trúc: ánh xạ hai cấp {date -> result}.
            if "daily" not in history:
                history["daily"] = {}

            history["daily"][date_str] = result
            history["last_updated"] = datetime.now().isoformat()

            # Ghi nguyên tử (ghi đè).
            history_path = self._get_group_history_path(group_id)
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

            logger.debug(
                f"Đã lưu bản ghi phân tích lịch sử của nhóm {group_id} ngày {date_str}"
            )
            return True

        except Exception as e:
            logger.error(f"Lưu lịch sử của nhóm {group_id} thất bại: {e}")
            return False

    def load_group_history(self, group_id: str) -> dict[str, Any]:
        """
        Tải toàn bộ dict lịch sử của nhóm.

        Args:
            group_id: Định danh nhóm.

        Returns:
            Dict lịch sử; nếu file chưa tồn tại thì trả cấu trúc daily rỗng.
        """
        try:
            history_path = self._get_group_history_path(group_id)
            if history_path.exists():
                with open(history_path, encoding="utf-8") as f:
                    return json.load(f)
            return {"daily": {}, "group_id": group_id}
        except Exception as e:
            logger.error(f"Tải lịch sử của nhóm {group_id} thất bại: {e}")
            return {"daily": {}, "group_id": group_id}

    def get_analysis_result(
        self, group_id: str, date_str: str
    ) -> dict[str, Any] | None:
        """
        Lấy kết quả phân tích đã lưu của ngày chỉ định.

        Args:
            group_id: ID nhóm.
            date_str: Ngày đích (YYYY-MM-DD).

        Returns:
            Dict kết quả hoặc None nếu không tìm thấy.
        """
        history = self.load_group_history(group_id)
        return history.get("daily", {}).get(date_str)

    def get_recent_results(self, group_id: str, limit: int = 7) -> list[dict[str, Any]]:
        """
        Lấy N kết quả phân tích gần nhất của nhóm.

        Args:
            group_id: ID nhóm.
            limit: Số kết quả tối đa.

        Returns:
            Danh sách kết quả sắp xếp giảm dần theo ngày.
        """
        history = self.load_group_history(group_id)
        daily = history.get("daily", {})

        # Sắp xếp giảm dần theo chuỗi ngày (YYYY-MM-DD vốn có thứ tự).
        sorted_dates = sorted(daily.keys(), reverse=True)[:limit]
        return [daily[date] for date in sorted_dates]

    def has_analysis_for_date(self, group_id: str, date_str: str) -> bool:
        """
        Kiểm tra ngày chỉ định đã có phân tích hay chưa.

        Args:
            group_id: ID nhóm.
            date_str: Chuỗi ngày.

        Returns:
            True nếu bản ghi tồn tại.
        """
        return self.get_analysis_result(group_id, date_str) is not None

    def delete_old_history(self, group_id: str, keep_days: int = 30) -> int:
        """
        Tự động xoá bản ghi lịch sử cũ vượt quá số ngày giữ lại.

        Args:
            group_id: ID nhóm.
            keep_days: Số ngày tối đa cần giữ lại.

        Returns:
            Số bản ghi thực tế đã xoá.
        """
        try:
            history = self.load_group_history(group_id)
            daily = history.get("daily", {})

            # Tính mốc ngày giới hạn.
            from datetime import timedelta

            cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")

            # Lọc các ngày đã hết hạn.
            dates_to_delete = [date for date in daily.keys() if date < cutoff]

            for date in dates_to_delete:
                del daily[date]

            if dates_to_delete:
                history["daily"] = daily
                history_path = self._get_group_history_path(group_id)
                with open(history_path, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)

            return len(dates_to_delete)

        except Exception as e:
            logger.error(f"Xoá lịch sử cũ của nhóm {group_id} thất bại: {e}")
            return 0

    def list_groups_with_history(self) -> list[str]:
        """
        Quét filesystem và liệt kê ID của các nhóm có bản ghi lưu trữ.

        Returns:
            Danh sách chuỗi ID nhóm.
        """
        try:
            groups = []
            for file_path in self.history_dir.glob("group_*.json"):
                # Suy ra ID nhóm từ tên tệp (group_123.json -> 123).
                group_id = file_path.stem.replace("group_", "")
                groups.append(group_id)
            return groups
        except Exception as e:
            logger.error(f"Liệt kê nhóm có lịch sử thất bại: {e}")
            return []
