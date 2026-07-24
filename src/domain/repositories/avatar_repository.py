"""Giao diện repository ảnh đại diện - lớp trừu tượng đa nền tảng."""

from abc import ABC, abstractmethod


class IAvatarRepository(ABC):
    """
    Giao diện repository ảnh đại diện.

    Cách lấy ảnh đại diện khác nhau giữa các nền tảng:
    - QQ/OneBot: mẫu URL (q1.qlogo.cn)
    - Telegram: gọi API (getUserProfilePhotos + getFile)
    - Discord: mẫu URL CDN (cdn.discordapp.com)
    - Slack: trường profile.image_* của API users.info
    """

    @abstractmethod
    async def get_user_avatar_url(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """
        Lấy URL ảnh đại diện thành viên.

        Args:
            user_id: ID thành viên.
            size: Kích thước mong muốn; chọn kích thước khả dụng gần nhất.

        Returns:
            URL ảnh đại diện hoặc ``None`` nếu không khả dụng.
        """
        pass

    @abstractmethod
    async def get_user_avatar_data(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """
        Lấy dữ liệu Base64 của ảnh đại diện thành viên.

        Dùng trong các trường hợp cần nhúng ảnh, chẳng hạn khi render template HTML.

        Returns:
            Dữ liệu ảnh mã hoá Base64 hoặc ``None`` nếu không khả dụng.
        """
        pass

    @abstractmethod
    async def get_group_avatar_url(
        self,
        group_id: str,
        size: int = 100,
    ) -> str | None:
        """Lấy URL ảnh đại diện nhóm."""
        pass

    @abstractmethod
    async def batch_get_avatar_urls(
        self,
        user_ids: list[str],
        size: int = 100,
    ) -> dict[str, str | None]:
        """
        Lấy URL ảnh đại diện của nhiều thành viên.

        Dùng khi tạo báo cáo cần lấy nhiều ảnh đại diện trong một lần.
        """
        pass

    def get_default_avatar_url(self) -> str:
        """Lấy URL ảnh đại diện mặc định khi ảnh của thành viên không khả dụng."""
        return "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTEyIDEyYzIuMjEgMCA0LTEuNzkgNC00cy0xLjc5LTQtNC00LTQgMS43OS00IDQgMS43OSA0IDQgNHptMCAyYy0yLjY3IDAtOCAxLjM0LTggNHYyaDE2di0yYzAtMi42Ni01LjMzLTQtOC00eiIvPjwvc3ZnPg=="
