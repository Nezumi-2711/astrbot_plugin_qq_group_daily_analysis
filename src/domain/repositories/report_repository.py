"""
Giao diện tạo báo cáo thuộc tầng domain.

Định nghĩa hợp đồng trừu tượng cho chức năng tạo báo cáo phân tích.
"""

from abc import ABC, abstractmethod
from typing import Any


class IReportGenerator(ABC):
    """Giao diện trình tạo báo cáo."""

    @abstractmethod
    async def generate_image_report(
        self,
        analysis_result: dict,
        group_id: str,
        html_render_func: Any,
        avatar_url_getter: Any = None,
        nickname_getter: Any = None,
        avatar_cache_namespace: str | None = None,
        hide_user_names: bool = False,
        allow_alphanumeric_user_ids: bool = False,
    ) -> tuple[str | None, str | None]:
        """Tạo báo cáo hình ảnh."""
        pass

    @abstractmethod
    async def generate_html_report(
        self,
        analysis_result: dict,
        group_id: str,
        avatar_url_getter: Any = None,
        nickname_getter: Any = None,
        avatar_cache_namespace: str | None = None,
        hide_user_names: bool = False,
        allow_alphanumeric_user_ids: bool = False,
    ) -> tuple[str | None, str | None]:
        """Tạo báo cáo HTML."""
        pass

    @abstractmethod
    def generate_text_report(self, analysis_result: dict) -> str:
        """Tạo báo cáo văn bản."""
        pass

    @abstractmethod
    async def close(self):
        """Giải phóng tài nguyên."""
        pass
