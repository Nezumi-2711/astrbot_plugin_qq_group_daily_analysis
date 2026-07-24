"""Factory adapter nền tảng."""

from __future__ import annotations

from collections.abc import Mapping

from ...utils.logger import logger
from .base import PlatformAdapter


class PlatformAdapterFactory:
    """
    Factory adapter nền tảng.

    Tạo instance adapter theo tên nền tảng bằng registry dễ mở rộng.
    """

    _adapters: dict[str, type[PlatformAdapter]] = {}

    @classmethod
    def register(cls, platform_name: str, adapter_class: type[PlatformAdapter]):
        """Đăng ký adapter mới."""
        cls._adapters[platform_name.lower()] = adapter_class

    @classmethod
    def create(
        cls,
        platform_name: str,
        bot_instance: object,
        config: Mapping[str, object] | None = None,
    ) -> PlatformAdapter | None:
        """
        Tạo adapter nền tảng.

        Args:
            platform_name: Tên nền tảng như ``aiocqhttp`` hoặc ``telegram``.
            bot_instance: Instance bot AstrBot.
            config: Dict cấu hình.

        Returns:
            Instance adapter hoặc None nếu không hỗ trợ.
        """
        adapter_class = cls._adapters.get(platform_name.lower())

        if adapter_class is None:
            return None

        try:
            return adapter_class(bot_instance, config)
        except Exception:
            # Ghi lỗi nhưng không làm sập plugin.
            logger.error(f"Lỗi khi tạo adapter cho {platform_name}", exc_info=True)
            return None

    @classmethod
    def get_supported_platforms(cls) -> list[str]:
        """Lấy tên tất cả nền tảng được hỗ trợ."""
        return list(cls._adapters.keys())

    @classmethod
    def is_supported(cls, platform_name: str) -> bool:
        """Kiểm tra nền tảng có được hỗ trợ hay không."""
        return platform_name.lower() in cls._adapters


# Import các adapter để đăng ký.
def _register_adapters():
    try:
        from .adapters.onebot_adapter import OneBotAdapter

        PlatformAdapterFactory.register("aiocqhttp", OneBotAdapter)
        PlatformAdapterFactory.register("onebot", OneBotAdapter)
    except ImportError:
        pass

    try:
        from .adapters.discord_adapter import DiscordAdapter

        PlatformAdapterFactory.register("discord", DiscordAdapter)
        PlatformAdapterFactory.register("discord_bot", DiscordAdapter)  # Alias
    except ImportError:
        pass

    try:
        from .adapters.telegram_adapter import TelegramAdapter

        PlatformAdapterFactory.register("telegram", TelegramAdapter)
    except ImportError:
        pass

    try:
        from .adapters.lark_adapter import LarkAdapter

        PlatformAdapterFactory.register("lark", LarkAdapter)
        PlatformAdapterFactory.register("feishu", LarkAdapter)
    except ImportError:
        pass

    try:
        from .adapters.qq_official_adapter import QQOfficialAdapter

        PlatformAdapterFactory.register("qq_official", QQOfficialAdapter)
        PlatformAdapterFactory.register("qq_official_webhook", QQOfficialAdapter)
    except ImportError:
        pass


_register_adapters()
