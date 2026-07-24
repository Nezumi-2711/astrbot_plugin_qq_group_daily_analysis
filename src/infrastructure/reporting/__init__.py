"""Module tạo báo cáo HTML, ảnh và văn bản."""

from .generators import ReportGenerator
from .templates import HTMLTemplates

__all__ = ["ReportGenerator", "HTMLTemplates"]
