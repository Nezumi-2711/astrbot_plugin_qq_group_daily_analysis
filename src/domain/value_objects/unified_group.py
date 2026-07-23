"""
Giá trị đối tượng nhóm thống nhất - Trừu tượng hóa nhóm đa nền tảng.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class UnifiedMember:
    """
    Giá trị đối tượng: thông tin thành viên thống nhất.

    Attributes:
        user_id (str): ID duy nhất của người dùng.
        nickname (str): Biệt danh của người dùng.
        card (str, optional): Tên hiển thị trong nhóm.
        role (str): Vai trò (owner/admin/member).
        join_time (int, optional): Thời điểm tham gia nhóm (dấu thời gian tính bằng giây).
        avatar_url (str, optional): URL ảnh đại diện.
        avatar_data (str, optional): Dữ liệu ảnh đại diện được mã hóa Base64.
    """

    user_id: str
    nickname: str
    card: str | None = None
    role: str = "member"
    join_time: int | None = None
    avatar_url: str | None = None
    avatar_data: str | None = None


@dataclass(frozen=True)
class UnifiedGroup:
    """
    Giá trị đối tượng: thông tin nhóm thống nhất.

    Attributes:
        group_id (str): ID duy nhất của nhóm.
        group_name (str): Tên nhóm.
        member_count (int): Số lượng thành viên.
        owner_id (str, optional): ID chủ nhóm.
        create_time (int, optional): Thời điểm tạo nhóm.
        description (str, optional): Phần giới thiệu hoặc thông báo của nhóm.
        platform (str): Nền tảng nguồn.
    """

    group_id: str
    group_name: str
    member_count: int = 0
    owner_id: str | None = None
    create_time: int | None = None
    description: str | None = None
    platform: str = "unknown"
