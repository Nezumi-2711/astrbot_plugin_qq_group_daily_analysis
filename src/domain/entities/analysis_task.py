"""Entity tác vụ phân tích - aggregate root."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    CHECKING_PLATFORM = "checking_platform"
    FETCHING_MESSAGES = "fetching_messages"
    ANALYZING = "analyzing"
    GENERATING_REPORT = "generating_report"
    SENDING = "sending"
    COMPLETED = "completed"
    FAILED = "failed"
    UNSUPPORTED_PLATFORM = "unsupported_platform"


@dataclass
class AnalysisTask:
    """Entity tác vụ phân tích - aggregate root."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    group_id: str = ""
    platform_name: str = ""
    trace_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    is_manual: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result_id: str | None = None
    error_message: str | None = None

    def start(self, can_analyze: bool) -> bool:
        """Khởi động tác vụ và kiểm tra năng lực nền tảng."""
        if not can_analyze:
            self.status = TaskStatus.UNSUPPORTED_PLATFORM
            self.error_message = f"Nền tảng {self.platform_name} không hỗ trợ phân tích"
            return False
        self.status = TaskStatus.FETCHING_MESSAGES
        self.started_at = time.time()
        return True

    def advance_to(self, status: TaskStatus):
        """Chuyển tác vụ sang trạng thái tiếp theo."""
        self.status = status

    def complete(self, result_id: str):
        """Đánh dấu tác vụ đã hoàn tất."""
        self.status = TaskStatus.COMPLETED
        self.result_id = result_id
        self.completed_at = time.time()

    def fail(self, error: str):
        """Đánh dấu tác vụ thất bại."""
        self.status = TaskStatus.FAILED
        self.error_message = error
        self.completed_at = time.time()

    @property
    def duration(self) -> float | None:
        """Lấy thời lượng thực thi tác vụ tính bằng giây."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None
