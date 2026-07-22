"""
Hằng số - Các hằng số dùng chung trong plugin.
"""

from enum import Enum


class Platform(str, Enum):
    """
    Liệt kê các nền tảng trò chuyện được hỗ trợ.

    Định nghĩa mã định danh của các nền tảng giao tiếp cơ bản mà plugin hỗ trợ.
    """

    ONEBOT = "onebot"
    AIOCQHTTP = "aiocqhttp"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLACK = "slack"
    LARK = "lark"


class TaskStatus(str, Enum):
    """
    Liệt kê các trạng thái thực thi của tác vụ phân tích.

    Dùng để đánh dấu giai đoạn trong vòng đời của tác vụ phân tích
    thuộc quy trình xử lý bất đồng bộ.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContentType(str, Enum):
    """
    Liệt kê các loại nội dung tin nhắn hợp nhất.

    Trừu tượng hóa các thành phần tin nhắn từ nhiều nền tảng
    (OneBot, Discord, v.v.) thành một hệ thống kiểu thống nhất.
    """

    TEXT = "text"
    IMAGE = "image"
    EMOJI = "emoji"
    STICKER = "sticker"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"
    REPLY = "reply"
    AT = "at"
    UNKNOWN = "unknown"


class ReportFormat(str, Enum):
    """
    Liệt kê các định dạng xuất báo cáo phân tích.

    Kiểm soát hình thức trình bày báo cáo cuối cùng cho người dùng.
    """

    TEXT = "text"
    MARKDOWN = "markdown"
    IMAGE = "image"
    HTML = "html"


# Siêu dữ liệu của plugin
PLUGIN_NAME = "astrbot_plugin_qq_group_daily_analysis"
PLUGIN_VERSION = "2.0.0"

# Mã định danh nền tảng
SUPPORTED_PLATFORMS = [
    Platform.ONEBOT.value,
    Platform.TELEGRAM.value,
    Platform.DISCORD.value,
]

# Giá trị phân tích mặc định
DEFAULT_MAX_TOPICS = 5
DEFAULT_MAX_USER_TITLES = 10
DEFAULT_MAX_GOLDEN_QUOTES = 5
DEFAULT_MIN_MESSAGES = 50
DEFAULT_MAX_TOKENS = 2000

# Các khoảng thời gian
HOUR_RANGES = {
    "morning": (6, 12),
    "afternoon": (12, 18),
    "evening": (18, 24),
    "night": (0, 6),
}

# Mã lỗi
ERROR_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
ERROR_LLM_FAILED = "LLM_FAILED"
ERROR_PLATFORM_ERROR = "PLATFORM_ERROR"
ERROR_CONFIG_ERROR = "CONFIG_ERROR"
ERROR_TIMEOUT = "TIMEOUT"

# Thời gian tồn tại của bộ nhớ đệm (giây)
CACHE_TTL_SHORT = 60  # 1 phút
CACHE_TTL_MEDIUM = 300  # 5 phút
CACHE_TTL_LONG = 3600  # 1 giờ
CACHE_TTL_DAY = 86400  # 24 giờ

# Giá trị giới hạn tốc độ mặc định
RATE_LIMIT_LLM_CALLS = 10  # Số lượt gọi mỗi phút
RATE_LIMIT_API_CALLS = 60  # Số lượt gọi mỗi phút
RATE_LIMIT_BURST = 5  # Quy mô gọi đột biến

# Giá trị thử lại mặc định
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0

# Đường dẫn tệp
HISTORY_DIR = "history"
CACHE_DIR = "cache"
TEMP_DIR = "temp"
