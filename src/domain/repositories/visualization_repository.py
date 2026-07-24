"""
Giao diện repository trực quan hoá thuộc tầng domain.

Định nghĩa hợp đồng trừu tượng cho chức năng trực quan hoá hoạt động.
"""

from abc import ABC, abstractmethod

from ..models.data_models import ActivityVisualization


class IActivityVisualizer(ABC):
    """Giao diện trực quan hoá hoạt động của tầng domain."""

    @abstractmethod
    def generate_activity_visualization(
        self, messages: list[dict]
    ) -> ActivityVisualization:
        """Tạo dữ liệu trực quan hoá hoạt động từ danh sách tin nhắn."""
        pass
