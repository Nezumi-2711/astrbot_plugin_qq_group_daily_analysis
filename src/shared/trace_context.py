"""
Ngữ cảnh truy vết - Theo dõi và liên kết yêu cầu.

Cung cấp ngữ cảnh dùng để theo dõi yêu cầu trong plugin.
"""

import functools
import logging
import re
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# Độ dài tối đa của tên nhóm trong Trace ID (cân bằng khả năng đọc và độ rộng nhật ký)
_MAX_GROUP_NAME_LEN = 10

# Mẫu biểu thức chính quy dùng để khớp token khử trùng lặp trong Caption báo cáo
# Định dạng: "| MM-DD HH:MM:SS"
REPORT_CAPTION_PATTERN = re.compile(r"\| (\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# Biến ngữ cảnh đang được truy vết
_current_trace: ContextVar[Optional["TraceContext"]] = ContextVar(
    "current_trace", default=None
)


@dataclass
class TraceContext:
    """
    Thành phần cốt lõi: ngữ cảnh truy vết toàn bộ chuỗi (Tracing Context).

    Thành phần này liên kết nhật ký, thống kê thời gian thực thi và siêu dữ liệu
    trong các quy trình phân tích bất đồng bộ phức tạp.
    Ngoài khả năng tạo và truyền Trace ID, thành phần còn tích hợp chức năng
    ghi nhận hiệu năng ở độ chính xác mili giây (Checkpoint).

    Attributes:
        trace_id (str): Mã định danh duy nhất của chuỗi, mặc định là 8 ký tự đầu của UUID.
        group_id (str): ID của nhóm đang được liên kết.
        platform (str): Nền tảng của tin nhắn hiện tại.
        operation (str): Tên thao tác đang thực hiện, ví dụ: 'DAILY_ANALYSIS'.
        start_time (datetime): Thời điểm cụ thể bắt đầu truy vết.
        metadata (dict[str, Any]): Dữ liệu ngữ cảnh bổ sung được truyền theo chuỗi.
    """

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    group_id: str = ""
    platform: str = ""
    operation: str = ""
    start_time: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Bộ hẹn giờ nội bộ, dùng để phân tích thời gian thực thi qua nhiều giai đoạn
    _checkpoints: dict[str, datetime] = field(default_factory=dict, init=False)

    def checkpoint(self, name: str) -> None:
        """
        Đặt một mốc có tên trên trục thời gian hiện tại.

        Args:
            name (str): Mã định danh của mốc, ví dụ: 'LLM_REPLY_RECEIVED'.
        """
        self._checkpoints[name] = datetime.now()

    def elapsed_ms(self, from_checkpoint: str | None = None) -> float:
        """
        Tính số mili giây đã trôi qua từ lúc bắt đầu hoặc từ một mốc chỉ định đến hiện tại.

        Args:
            from_checkpoint (str, optional): Tên mốc bắt đầu. Nếu là None,
                tính từ thời điểm chuỗi được khởi tạo.

        Returns:
            float: Số mili giây đã trôi qua.
        """
        start = self.start_time
        if from_checkpoint and from_checkpoint in self._checkpoints:
            start = self._checkpoints[from_checkpoint]

        delta = datetime.now() - start
        return delta.total_seconds() * 1000

    def to_dict(self) -> dict[str, Any]:
        """
        Tuần tự hóa ảnh chụp trạng thái của chuỗi thành dạng từ điển,
        thuận tiện cho việc lưu trữ hoặc xuất nhật ký JSON.

        Returns:
            dict[str, Any]: Trạng thái truy vết sau khi tuần tự hóa.
        """
        return {
            "trace_id": self.trace_id,
            "group_id": self.group_id,
            "platform": self.platform,
            "operation": self.operation,
            "start_time": self.start_time.isoformat(),
            "elapsed_ms": self.elapsed_ms(),
            "metadata": self.metadata,
            "checkpoints": {k: v.isoformat() for k, v in self._checkpoints.items()},
        }

    _token: Token | None = field(default=None, init=False, repr=False)

    def __enter__(self) -> "TraceContext":
        """Bước vào trình quản lý ngữ cảnh và liên kết instance hiện tại với ngữ cảnh coroutine."""
        self._token = _current_trace.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Thoát khỏi trình quản lý ngữ cảnh và dọn dẹp trạng thái liên kết."""
        if self._token:
            _current_trace.reset(self._token)
            self._token = None

    @classmethod
    def current(cls) -> Optional["TraceContext"]:
        """
        Lấy ngữ cảnh truy vết đang hoạt động trong coroutine hiện tại.

        Returns:
            Optional[TraceContext]: Instance nếu hiện tại đang ở trong chuỗi truy vết,
                ngược lại trả về None.
        """
        return _current_trace.get()

    @classmethod
    def get_or_create(
        cls,
        group_id: str = "",
        platform: str = "",
        operation: str = "",
        auto_bind: bool = False,
    ) -> "TraceContext":
        """
        Thử lấy chuỗi hiện có; nếu không tồn tại thì tạo một chuỗi mới khi cần.

        Args:
            group_id (str): ID nhóm.
            platform (str): Tên nền tảng.
            operation (str): Mô tả thao tác.
            auto_bind (bool): Nếu tạo mới, có tự động liên kết với ngữ cảnh hiện tại
                hay không (chỉ hữu ích trong trường hợp không dùng with, hãy thận trọng).

        Returns:
            TraceContext: Instance đang hoạt động hoặc vừa được tạo.
        """
        current = cls.current()
        if current:
            return current

        new_ctx = cls(
            group_id=group_id,
            platform=platform,
            operation=operation,
        )
        if auto_bind:
            new_ctx._token = _current_trace.set(new_ctx)
        return new_ctx

    @staticmethod
    def generate(prefix: str = "", group_name: str = "") -> str:
        """
        Tạo Trace ID có ngữ nghĩa và dễ đọc.

        Định dạng: {nguồn}_{tên_nhóm}_{thời_điểm}
        Ví dụ: manual_Nhóm_hệ_thống_1733

        Do plugin có khóa tác vụ (DuplicateGroupTaskError), bảo đảm mỗi nhóm
        chỉ có một tác vụ phân tích tại một thời điểm. Vì vậy, thời điểm (HHmm)
        đã đủ để tạo tính duy nhất và không cần thêm UUID.

        Args:
            prefix (str): Mã nguồn, ví dụ: 'manual', 'group', 'incr', 'report'.
            group_name (str): Tên nhóm tùy chọn, dùng để nhận diện nhanh trong nhật ký.

        Returns:
            str: Chuỗi Trace ID có ngữ nghĩa.
        """
        timestamp = datetime.now().strftime("%H%M")

        parts: list[str] = []
        if prefix:
            parts.append(prefix)
        if group_name:
            # Làm sạch: loại bỏ khoảng trắng và các ký tự không an toàn cho hệ thống tệp
            safe_name = re.sub(r'[\s\n\r\t/\\:*?"<>|\[\]{}]', "", group_name)
            safe_name = safe_name[:_MAX_GROUP_NAME_LEN]
            if safe_name:
                parts.append(safe_name)
        parts.append(timestamp)

        return "_".join(parts)

    @staticmethod
    def make_report_caption() -> str:
        """
        Tạo Caption báo cáo gọn gàng hướng đến người dùng,
        có chứa dấu thời gian ẩn dùng để khử trùng lặp.

        Dấu thời gian này được dùng làm token kiểm tra trùng lặp hình ảnh.
        Định dạng gồm biểu tượng báo cáo, nội dung Caption hiện tại và dấu thời gian
        theo mẫu ``MM-DD HH:MM:SS``.

        Returns:
            str: Chuỗi Caption của báo cáo.
        """
        ts = datetime.now().strftime("%m-%d %H:%M:%S")
        return f"📊 Báo cáo phân tích nhóm hằng ngày đã được tạo | {ts}"

    @classmethod
    def set(cls, trace_id: str) -> None:
        """
        [Giao diện tương thích] Đặt trực tiếp Trace ID của ngữ cảnh hiện tại.
        Thao tác này tạo một instance TraceContext mới và đưa vào ContextVar.

        Args:
            trace_id (str): Chuỗi Trace ID cần đặt.
        """
        ctx = cls(trace_id=trace_id)
        # Lưu ý: không lưu Token thủ công ở đây; dựa vào việc ContextVar tự dọn dẹp
        # khi tác vụ bất đồng bộ kết thúc.
        _current_trace.set(ctx)

    @classmethod
    def get(cls) -> str:
        """
        [Giao diện tương thích] Lấy chuỗi Trace ID đang hoạt động hiện tại.
        """
        return get_trace_id()


class TraceLogFilter(logging.Filter):
    """
    Bộ lọc nhật ký: tự động chèn Trace ID hiện tại vào mỗi bản ghi nhật ký.

    Được sử dụng cùng chuỗi định dạng nhật ký `[%(trace_id)s]`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


def get_trace_id() -> str:
    """
    Giao diện tiện ích: nhanh chóng lấy Trace ID đang hoạt động,
    hoặc tạm thời tạo một ID mới nếu chưa có.

    Returns:
        str: Trace ID hệ thập lục phân gồm 8 ký tự.
    """
    trace = TraceContext.current()
    if trace:
        return trace.trace_id
    return str(uuid.uuid4())[:8]


def with_trace(
    group_id: str = "",
    platform: str = "",
    operation: str = "",
):
    """
    Decorator: tự động bao bọc hàm bất đồng bộ bằng ngữ cảnh truy vết.

    Args:
        group_id (str): Nhóm cần truy vết.
        platform (str): Nền tảng cần truy vết.
        operation (str): Tên thao tác, mặc định là tên hàm.

    Returns:
        Callable: Hàm sau khi được trang trí.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Ưu tiên operation được khai báo trong decorator; nếu không có,
            # sử dụng tên gốc của hàm.
            op_name = operation or func.__name__
            with TraceContext(
                group_id=group_id,
                platform=platform,
                operation=op_name,
            ):
                return await func(*args, **kwargs)

        return wrapper

    return decorator
