"""
Giá trị đối tượng năng lực nền tảng - Hỗ trợ quyết định lúc chạy.

Mỗi adapter nền tảng khai báo năng lực của mình;
tầng ứng dụng quyết định thao tác dựa trên các năng lực đó.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformCapabilities:
    """
    Giá trị đối tượng: mô tả năng lực nền tảng.

    Xác định lúc chạy nền tảng hiện tại hỗ trợ thao tác nào,
    giúp triển khai lập trình phòng thủ và tương thích đa nền tảng.

    Attributes:
        platform_name (str): Mã nền tảng (ví dụ: discord, onebot).
        platform_version (str): Số phiên bản.
        supports_message_history (bool): Có hỗ trợ lấy tin nhắn lịch sử hay không.
        max_message_history_days (int): Số ngày tối đa có thể truy xuất lịch sử.
        max_message_count (int): Số tin nhắn tối đa có thể lấy trong một lần.
        supports_message_search (bool): Có hỗ trợ tìm kiếm tin nhắn hay không (dành cho mở rộng).
        supports_group_list (bool): Có hỗ trợ liệt kê tất cả nhóm hay không.
        supports_group_info (bool): Có hỗ trợ lấy metadata nhóm hay không.
        supports_member_list (bool): Có hỗ trợ lấy danh sách thành viên hay không.
        supports_member_info (bool): Có hỗ trợ lấy thông tin chi tiết một thành viên hay không.
        supports_text_message (bool): Có thể gửi tin nhắn văn bản hay không.
        supports_image_message (bool): Có thể gửi hình ảnh hay không.
        supports_file_message (bool): Có thể gửi tệp/PDF hay không.
        supports_forward_message (bool): Có hỗ trợ chuỗi chuyển tiếp (chuyển tiếp hợp nhất) hay không.
        supports_reply_message (bool): Có hỗ trợ trích dẫn để trả lời hay không.
        max_text_length (int): Độ dài văn bản tối đa của một tin nhắn trả lời.
        max_image_size_mb (float): Giới hạn kích thước tải ảnh lên (MB).
        supports_at_all (bool): Có thể @tất cả thành viên hay không.
        supports_recall (bool): Có hỗ trợ thu hồi tin nhắn hay không.
        supports_edit (bool): Có hỗ trợ chỉnh sửa tin nhắn đã gửi hay không.
        supports_user_avatar (bool): Có API lấy ảnh đại diện người dùng hay không.
        supports_group_avatar (bool): Có API lấy ảnh đại diện nhóm hay không.
        avatar_needs_api_call (bool): Việc lấy ảnh đại diện có cần gọi API bất đồng bộ hay không.
        avatar_sizes (tuple[int, ...]): Các kích thước ảnh đại diện tính bằng pixel mà nền tảng hỗ trợ.
    """

    # 平台标识
    platform_name: str
    platform_version: str = "unknown"

    # 消息获取能力
    supports_message_history: bool = False
    max_message_history_days: int = 0
    max_message_count: int = 0
    supports_message_search: bool = False

    # 群组信息能力
    supports_group_list: bool = False
    supports_group_info: bool = False
    supports_member_list: bool = False
    supports_member_info: bool = False

    # 消息发送能力
    supports_text_message: bool = True
    supports_image_message: bool = False
    supports_file_message: bool = False
    supports_forward_message: bool = False
    supports_reply_message: bool = False
    max_text_length: int = 4096
    max_image_size_mb: float = 10.0

    # 特殊能力
    supports_at_all: bool = False
    supports_recall: bool = False
    supports_edit: bool = False

    # 头像能力
    supports_user_avatar: bool = True
    supports_group_avatar: bool = False
    avatar_needs_api_call: bool = False
    avatar_sizes: tuple[int, ...] = (100,)

    # Phương thức kiểm tra
    def can_analyze(self) -> bool:
        """
        Kiểm tra nền tảng có đủ khả năng cốt lõi để phân tích chat nhóm hay không.

        Returns:
            bool: True nếu có đủ khả năng cốt lõi.
        """
        return (
            self.supports_message_history
            and self.max_message_history_days > 0
            and self.max_message_count > 0
        )

    def can_send_report(self, format: str = "image") -> bool:
        """
        Kiểm tra có thể gửi báo cáo ở định dạng được chỉ định hay không.

        Args:
            format (str): Định dạng báo cáo ('text', 'image', 'pdf').

        Returns:
            bool: True nếu hỗ trợ định dạng đó.
        """
        if format == "text":
            return self.supports_text_message
        elif format == "image":
            return self.supports_image_message
        elif format == "pdf":
            return self.supports_file_message
        return False

    def get_effective_days(self, requested_days: int) -> int:
        """
        Lấy số ngày truy xuất lịch sử sau khi áp dụng giới hạn nền tảng.

        Args:
            requested_days (int): Số ngày được yêu cầu.

        Returns:
            int: Số ngày thực tế sau khi áp dụng giới hạn của nền tảng.
        """
        return min(requested_days, self.max_message_history_days)

    def get_effective_count(self, requested_count: int) -> int:
        """
        Lấy số lượng tin nhắn sau khi áp dụng giới hạn nền tảng.

        Args:
            requested_count (int): Số lượng tin nhắn được yêu cầu.

        Returns:
            int: Số lượng thực tế sau khi áp dụng giới hạn của nền tảng.
        """
        return min(requested_count, self.max_message_count)


# Năng lực nền tảng được định nghĩa sẵn
# OneBot v11 (ví dụ: NapCat, LLOneBot, ...)
ONEBOT_V11_CAPABILITIES = PlatformCapabilities(
    platform_name="onebot",
    platform_version="v11",
    supports_message_history=True,
    max_message_history_days=7,
    max_message_count=10000,
    supports_group_list=True,
    supports_group_info=True,
    supports_member_list=True,
    supports_member_info=True,
    supports_text_message=True,
    supports_image_message=True,
    supports_file_message=True,
    supports_forward_message=True,
    supports_reply_message=True,
    max_text_length=4500,
    supports_at_all=True,
    supports_recall=True,
    supports_user_avatar=True,
    supports_group_avatar=True,
    avatar_needs_api_call=False,
    avatar_sizes=(40, 100, 140, 160, 640),
)

# Telegram Bot API
TELEGRAM_CAPABILITIES = PlatformCapabilities(
    platform_name="telegram",
    platform_version="bot_api_7.x",
    # Hỗ trợ đọc lịch sử thông qua PlatformMessageHistoryManager + message interceptor
    supports_message_history=True,
    max_message_history_days=7,
    max_message_count=1000,
    supports_group_list=False,
    supports_group_info=True,
    supports_member_list=True,
    supports_member_info=True,
    supports_text_message=True,
    supports_image_message=True,
    supports_file_message=True,
    supports_reply_message=True,
    max_text_length=4096,
    max_image_size_mb=50.0,
    supports_edit=True,
    supports_user_avatar=True,
    supports_group_avatar=True,
    avatar_needs_api_call=True,
    avatar_sizes=(160, 320, 640),
)

# Discord API
DISCORD_CAPABILITIES = PlatformCapabilities(
    platform_name="discord",
    platform_version="api_v10",
    supports_message_history=True,
    max_message_history_days=30,
    max_message_count=10000,
    supports_group_list=True,
    supports_group_info=True,
    supports_member_list=True,
    supports_text_message=True,
    supports_image_message=True,
    supports_file_message=True,
    supports_reply_message=True,
    max_text_length=2000,
    max_image_size_mb=8.0,
    supports_edit=True,
    supports_user_avatar=True,
    supports_group_avatar=True,
    avatar_needs_api_call=False,
    avatar_sizes=(16, 32, 64, 128, 256, 512, 1024, 2048, 4096),
)

# Slack Web API
SLACK_CAPABILITIES = PlatformCapabilities(
    platform_name="slack",
    platform_version="web_api",
    supports_message_history=True,
    max_message_history_days=90,
    max_message_count=1000,
    supports_group_list=True,
    supports_group_info=True,
    supports_member_list=True,
    supports_text_message=True,
    supports_image_message=True,
    supports_file_message=True,
    supports_reply_message=True,
    max_text_length=40000,
    supports_edit=True,
    supports_user_avatar=True,
    supports_group_avatar=False,
    avatar_needs_api_call=True,
    avatar_sizes=(24, 32, 48, 72, 192, 512, 1024),
)

# Feishu/Lark Open Platform API
LARK_CAPABILITIES = PlatformCapabilities(
    platform_name="lark",
    platform_version="open_api_v1",
    supports_message_history=True,
    max_message_history_days=7,
    max_message_count=1000,
    supports_group_list=False,
    supports_group_info=True,
    supports_member_list=True,
    supports_member_info=True,
    supports_text_message=True,
    supports_image_message=True,
    supports_file_message=True,
    supports_reply_message=True,
    max_text_length=30000,
    max_image_size_mb=10.0,
    supports_user_avatar=True,
    supports_group_avatar=True,
    avatar_needs_api_call=True,
    avatar_sizes=(72, 240, 640),
)

# QQ Official Bot API. Lịch sử tin nhắn được cung cấp bởi kho lưu trữ sự kiện
# cục bộ của plugin vì API công khai không cung cấp truy vấn lịch sử nhóm.
QQ_OFFICIAL_CAPABILITIES = PlatformCapabilities(
    platform_name="qq_official",
    platform_version="api_v2_local_history",
    supports_message_history=True,
    max_message_history_days=7,
    max_message_count=10000,
    supports_group_list=False,
    supports_group_info=False,
    supports_member_list=False,
    supports_member_info=False,
    supports_text_message=True,
    supports_image_message=True,
    supports_file_message=True,
    supports_forward_message=False,
    supports_reply_message=False,
    max_text_length=4000,
    max_image_size_mb=20.0,
    supports_user_avatar=True,
    supports_group_avatar=False,
    avatar_needs_api_call=False,
    avatar_sizes=(640,),
)

# Bảng tra cứu năng lực (ánh xạ mã nền tảng đến đối tượng năng lực)
PLATFORM_CAPABILITIES: dict[str, PlatformCapabilities] = {
    "aiocqhttp": ONEBOT_V11_CAPABILITIES,
    "onebot": ONEBOT_V11_CAPABILITIES,
    "telegram": TELEGRAM_CAPABILITIES,
    "discord": DISCORD_CAPABILITIES,
    "slack": SLACK_CAPABILITIES,
    "lark": LARK_CAPABILITIES,
    "qq_official": QQ_OFFICIAL_CAPABILITIES,
    "qq_official_webhook": QQ_OFFICIAL_CAPABILITIES,
}


def get_capabilities(platform_name: str) -> PlatformCapabilities | None:
    """
    Tra cứu các năng lực được hỗ trợ theo tên nền tảng.

    Args:
        platform_name (str): Tên nền tảng.

    Returns:
        Optional[PlatformCapabilities]: Đối tượng năng lực tương ứng hoặc None.
    """
    return PLATFORM_CAPABILITIES.get(platform_name.lower())
