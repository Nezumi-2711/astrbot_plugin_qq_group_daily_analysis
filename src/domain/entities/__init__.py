"""
Các entity thuộc tầng domain.

Module này export các entity domain, gồm:
- AnalysisTask: aggregate root của tác vụ phân tích
- IncrementalBatch: entity đại diện cho một batch phân tích gia tăng
- IncrementalState: view tổng hợp phân tích gia tăng dùng khi tạo báo cáo
"""

from .analysis_task import AnalysisTask, TaskStatus
from .incremental_state import IncrementalBatch, IncrementalState

__all__ = [
    "AnalysisTask",
    "TaskStatus",
    "IncrementalBatch",
    "IncrementalState",
]
