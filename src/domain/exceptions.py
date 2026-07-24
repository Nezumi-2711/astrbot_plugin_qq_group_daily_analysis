"""
Ngoại lệ domain - các ngoại lệ tuỳ chỉnh của tầng domain.

Module này chứa các ngoại lệ đặc thù nghiệp vụ được plugin sử dụng.
Các ngoại lệ độc lập với nền tảng và biểu diễn lỗi logic nghiệp vụ.
"""


class DomainException(Exception):
    """Ngoại lệ cơ sở cho mọi lỗi domain."""

    def __init__(self, message: str, code: str = "DOMAIN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


# ============================================================================
# Ngoại lệ phân tích
# ============================================================================


class AnalysisException(DomainException):
    """Ngoại lệ cơ sở cho các lỗi liên quan đến phân tích."""

    def __init__(self, message: str, code: str = "ANALYSIS_ERROR"):
        super().__init__(message, code)


class InsufficientDataException(AnalysisException):
    """Được phát sinh khi dữ liệu không đủ để phân tích."""

    def __init__(self, message: str = "Không đủ dữ liệu để phân tích"):
        super().__init__(message, "INSUFFICIENT_DATA")


class AnalysisTimeoutException(AnalysisException):
    """Được phát sinh khi phân tích quá thời gian cho phép."""

    def __init__(self, message: str = "Phân tích bị timeout"):
        super().__init__(message, "ANALYSIS_TIMEOUT")


class LLMException(AnalysisException):
    """Được phát sinh khi gọi API LLM thất bại."""

    def __init__(self, message: str = "Gọi API LLM thất bại", provider: str = ""):
        self.provider = provider
        super().__init__(
            f"{message} (nhà cung cấp: {provider})" if provider else message,
            "LLM_ERROR",
        )


class LLMRateLimitException(LLMException):
    """Được phát sinh khi vượt quá giới hạn tốc độ của API LLM."""

    def __init__(
        self, message: str = "Vượt quá giới hạn tốc độ LLM", provider: str = ""
    ):
        super().__init__(message, provider)
        self.code = "LLM_RATE_LIMIT"


class LLMQuotaExceededException(LLMException):
    """Được phát sinh khi vượt quá hạn ngạch của API LLM."""

    def __init__(self, message: str = "Vượt quá hạn ngạch LLM", provider: str = ""):
        super().__init__(message, provider)
        self.code = "LLM_QUOTA_EXCEEDED"


# ============================================================================
# Ngoại lệ nền tảng
# ============================================================================


class PlatformException(DomainException):
    """Ngoại lệ cơ sở cho các lỗi liên quan đến nền tảng."""

    def __init__(self, message: str, platform: str = "", code: str = "PLATFORM_ERROR"):
        self.platform = platform
        super().__init__(f"[{platform}] {message}" if platform else message, code)


class PlatformNotSupportedException(PlatformException):
    """Được phát sinh khi nền tảng không được hỗ trợ."""

    def __init__(self, platform: str):
        super().__init__(
            f"Nền tảng '{platform}' không được hỗ trợ",
            platform,
            "PLATFORM_NOT_SUPPORTED",
        )


class PlatformConnectionException(PlatformException):
    """Được phát sinh khi kết nối đến nền tảng thất bại."""

    def __init__(self, message: str = "Kết nối nền tảng thất bại", platform: str = ""):
        super().__init__(message, platform, "PLATFORM_CONNECTION_ERROR")


class PlatformAPIException(PlatformException):
    """Được phát sinh khi gọi API nền tảng thất bại."""

    def __init__(self, message: str = "Gọi API nền tảng thất bại", platform: str = ""):
        super().__init__(message, platform, "PLATFORM_API_ERROR")


class MessageFetchException(PlatformException):
    """Được phát sinh khi lấy tin nhắn thất bại."""

    def __init__(
        self,
        message: str = "Lấy tin nhắn thất bại",
        platform: str = "",
        group_id: str = "",
    ):
        self.group_id = group_id
        super().__init__(
            f"{message} (nhóm: {group_id})" if group_id else message,
            platform,
            "MESSAGE_FETCH_ERROR",
        )


class MessageSendException(PlatformException):
    """Được phát sinh khi gửi tin nhắn thất bại."""

    def __init__(
        self,
        message: str = "Gửi tin nhắn thất bại",
        platform: str = "",
        group_id: str = "",
    ):
        self.group_id = group_id
        super().__init__(
            f"{message} (nhóm: {group_id})" if group_id else message,
            platform,
            "MESSAGE_SEND_ERROR",
        )


# ============================================================================
# Ngoại lệ cấu hình
# ============================================================================


class ConfigurationException(DomainException):
    """Ngoại lệ cơ sở cho các lỗi liên quan đến cấu hình."""

    def __init__(self, message: str, code: str = "CONFIG_ERROR"):
        super().__init__(message, code)


class InvalidConfigurationException(ConfigurationException):
    """Được phát sinh khi cấu hình không hợp lệ."""

    def __init__(self, message: str = "Cấu hình không hợp lệ", key: str = ""):
        self.key = key
        super().__init__(f"{message}: {key}" if key else message, "INVALID_CONFIG")


class MissingConfigurationException(ConfigurationException):
    """Được phát sinh khi thiếu cấu hình bắt buộc."""

    def __init__(self, key: str):
        self.key = key
        super().__init__(f"Thiếu cấu hình bắt buộc: {key}", "MISSING_CONFIG")


# ============================================================================
# Ngoại lệ repository
# ============================================================================


class RepositoryException(DomainException):
    """Ngoại lệ cơ sở cho các lỗi liên quan đến repository."""

    def __init__(self, message: str, code: str = "REPOSITORY_ERROR"):
        super().__init__(message, code)


class DataNotFoundException(RepositoryException):
    """Được phát sinh khi không tìm thấy dữ liệu được yêu cầu."""

    def __init__(
        self,
        message: str = "Không tìm thấy dữ liệu",
        entity_type: str = "",
        entity_id: str = "",
    ):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(
            f"Không tìm thấy {entity_type}: {entity_id}" if entity_type else message,
            "DATA_NOT_FOUND",
        )


class DataPersistenceException(RepositoryException):
    """Được phát sinh khi lưu trữ dữ liệu thất bại."""

    def __init__(self, message: str = "Lưu trữ dữ liệu thất bại"):
        super().__init__(message, "DATA_PERSISTENCE_ERROR")


# ============================================================================
# Ngoại lệ lập lịch
# ============================================================================


class SchedulingException(DomainException):
    """Ngoại lệ cơ sở cho các lỗi liên quan đến lập lịch."""

    def __init__(self, message: str, code: str = "SCHEDULING_ERROR"):
        super().__init__(message, code)


class TaskAlreadyScheduledException(SchedulingException):
    """Được phát sinh khi cố lập lịch cho tác vụ đã được lập lịch."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(
            f"Tác vụ đã được lên lịch: {task_id}", "TASK_ALREADY_SCHEDULED"
        )


class TaskNotFoundException(SchedulingException):
    """Được phát sinh khi không tìm thấy tác vụ đã lập lịch."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(
            f"Không tìm thấy tác vụ đã lên lịch: {task_id}", "TASK_NOT_FOUND"
        )


# ============================================================================
# Ngoại lệ xác thực
# ============================================================================


class ValidationException(DomainException):
    """Ngoại lệ cơ sở cho các lỗi xác thực."""

    def __init__(self, message: str, field: str = "", code: str = "VALIDATION_ERROR"):
        self.field = field
        super().__init__(f"{field}: {message}" if field else message, code)


class InvalidGroupIdException(ValidationException):
    """Được phát sinh khi ID nhóm không hợp lệ."""

    def __init__(self, group_id: str):
        super().__init__(
            f"ID nhóm không hợp lệ: {group_id}", "group_id", "INVALID_GROUP_ID"
        )


class InvalidUserIdException(ValidationException):
    """Được phát sinh khi ID thành viên không hợp lệ."""

    def __init__(self, user_id: str):
        super().__init__(
            f"ID người dùng không hợp lệ: {user_id}", "user_id", "INVALID_USER_ID"
        )


class InvalidMessageException(ValidationException):
    """Được phát sinh khi định dạng tin nhắn không hợp lệ."""

    def __init__(self, message: str = "Định dạng tin nhắn không hợp lệ"):
        super().__init__(message, "message", "INVALID_MESSAGE")
