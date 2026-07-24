"""
Dịch vụ domain - các dịch vụ xử lý logic phân tích nghiệp vụ.

Module này export các dịch vụ domain đóng gói logic nghiệp vụ cốt lõi
để phân tích dữ liệu trò chuyện nhóm. Các dịch vụ độc lập với nền tảng.
"""

from .incremental_merge_service import IncrementalMergeService

__all__ = [
    "IncrementalMergeService",
]
