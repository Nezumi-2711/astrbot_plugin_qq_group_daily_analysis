"""Quản lý tập trung các bot instance ở tầng infrastructure."""

from __future__ import annotations

from collections.abc import Mapping

from ...utils.logger import logger
from . import PlatformAdapter, PlatformAdapterFactory


class BotManager:
    """Quản lý bot và tạo PlatformAdapter tương ứng để hỗ trợ đa nền tảng."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self._bot_instances: dict[str, object] = {}  # {platform_id: bot_instance}
        self._adapters: dict[
            str, PlatformAdapter
        ] = {}  # {platform_id: PlatformAdapter} - tích hợp DDD.
        self._platforms: dict[str, object] = {}  # Lưu platform để truy cập cấu hình.
        self._bot_self_ids: list[
            str
        ] = []  # Hỗ trợ nhiều ID bot, trước đây là _bot_qq_ids.
        self._context: object | None = None
        self._is_initialized = False
        self._default_platform = "default"  # Nền tảng mặc định.
        self._plugin_instance: object | None = None  # Dùng cho callback adapter.

    def set_context(self, context):
        """Thiết lập context AstrBot và truyền tới adapter có hỗ trợ."""
        self._context = context

        # Truyền context tới mọi adapter hỗ trợ set_context.
        for adapter in self._adapters.values():
            if hasattr(adapter, "set_context"):
                adapter.set_context(context)

    def set_plugin_instance(self, plugin_instance: object):
        """Thiết lập tham chiếu plugin instance."""
        self._plugin_instance = plugin_instance

    def set_bot_instance(self, bot_instance, platform_id=None, platform_name=None):
        """
        Thiết lập bot instance với platform ID tùy chọn.

        Đồng thời tạo PlatformAdapter nếu nền tảng được hỗ trợ.
        """
        if not platform_id:
            platform_id = self._get_platform_id_from_instance(bot_instance)

        if bot_instance and platform_id:
            # Không tạo lại adapter nếu instance không đổi để giữ trạng thái nội bộ.
            old_instance = self._bot_instances.get(platform_id)
            if bot_instance is old_instance and platform_id in self._adapters:
                bot_self_id = self._extract_bot_self_id(bot_instance)
                if bot_self_id and bot_self_id not in self._bot_self_ids:
                    self._bot_self_ids.append(str(bot_self_id))
                return

            self._bot_instances[platform_id] = bot_instance

            # Tạo PlatformAdapter để tích hợp DDD.
            if platform_name is None:
                platform_name = self._detect_platform_name(bot_instance)

            if platform_name and PlatformAdapterFactory.is_supported(platform_name):
                adapter_config = {
                    "bot_self_ids": self._bot_self_ids.copy(),
                    "platform_id": str(platform_id),
                    "filter_bot_messages": self.config_manager.get_filter_bot_messages(),
                    "plugin_instance": self._plugin_instance,
                }
                platform_instance = self._platforms.get(str(platform_id))
                platform_config = getattr(platform_instance, "config", None)
                if isinstance(platform_config, Mapping):
                    adapter_config["appid"] = platform_config.get("appid", "")
                adapter = PlatformAdapterFactory.create(
                    platform_name, bot_instance, adapter_config
                )
                if adapter:
                    # Truyền context tới adapter nếu có.
                    if self._context is not None:
                        adapter.set_context(self._context)
                    self._adapters[platform_id] = adapter
                    logger.debug(
                        f"Đã tạo PlatformAdapter cho {platform_id} ({platform_name})"
                    )

            # Tự trích xuất ID bot.
            bot_self_id = self._extract_bot_self_id(bot_instance)
            if bot_self_id and bot_self_id not in self._bot_self_ids:
                self._bot_self_ids.append(str(bot_self_id))

    def set_bot_self_ids(self, bot_self_ids):
        """Thiết lập một ID bot hoặc danh sách ID bot."""
        if isinstance(bot_self_ids, list):
            self._bot_self_ids = [str(uid) for uid in bot_self_ids if uid]
        elif bot_self_ids:
            self._bot_self_ids = [str(bot_self_ids)]

        # Đồng bộ danh sách ID tới mọi adapter hiện có.
        for adapter in self._adapters.values():
            if hasattr(adapter, "bot_self_ids"):
                adapter.bot_self_ids = self._bot_self_ids.copy()

    def get_bot_instance(self, platform_id=None):
        """Lấy bot instance theo platform hoặc instance khả dụng duy nhất."""
        if platform_id:
            # Thử lấy theo platform ID được chỉ định.
            instance = self._bot_instances.get(platform_id)
            if not instance and platform_id in self._platforms:
                self._refresh_from_stored_platforms()
                instance = self._bot_instances.get(platform_id)
            return instance

        # Không có platform ID.
        if not self._bot_instances and self._platforms:
            self._refresh_from_stored_platforms()

        if self._bot_instances:
            # Trả trực tiếp nếu chỉ có một instance.
            if len(self._bot_instances) == 1:
                return list(self._bot_instances.values())[0]

            # Bắt buộc chỉ định platform_id khi có nhiều instance.
            logger.error(
                f"Có nhiều bot instance {list(self._bot_instances.keys())} nhưng chưa chỉ định platform_id; "
                "không thể xác định instance cần dùng"
            )
            return None

        # Không có nền tảng khả dụng.
        logger.error("Không có bot instance khả dụng")
        return None

    def _refresh_from_stored_platforms(self):
        """Thử làm mới bot instance từ platform đã lưu theo lazy load."""
        for platform_id, platform in self._platforms.items():
            bot_client = None
            # Lark ưu tiên API client thay vì ws client chỉ hỗ trợ kết nối dài.
            bot_client = getattr(platform, "lark_api", None)
            # Ưu tiên get_client().
            get_client = getattr(platform, "get_client", None)
            if not bot_client and callable(get_client):
                bot_client = get_client()

            # Nếu get_client() trả None, thử truy cập thuộc tính trực tiếp.
            if not bot_client:
                bot_client = getattr(platform, "bot", None)
            if not bot_client:
                # DiscordPlatformAdapter AstrBot v4.14.4 dùng thuộc tính client.
                bot_client = getattr(platform, "client", None)

            if bot_client:
                # Kiểm tra client có thay đổi để tránh tạo adapter lặp.
                old_client = self._bot_instances.get(platform_id)

                # Bỏ qua nếu client không đổi và adapter đã tồn tại.
                if bot_client is old_client and platform_id in self._adapters:
                    continue

                platform_name = None
                metadata_obj = getattr(platform, "metadata", None)
                if metadata_obj is not None:
                    # Ưu tiên type.
                    type_val = getattr(metadata_obj, "type", None)
                    if isinstance(type_val, str):
                        platform_name = type_val
                    else:
                        name_val = getattr(metadata_obj, "name", None)
                        if isinstance(name_val, str):
                            platform_name = name_val

                # Tương thích cách lấy metadata ở các phiên bản.
                if not platform_name:
                    meta = getattr(platform, "meta", None)
                    if callable(meta):
                        try:
                            metadata = meta()
                            platform_name = getattr(metadata, "name", None)
                        except Exception:
                            pass

                # Phát hiện fallback nếu tên không được hỗ trợ.
                if not platform_name or not PlatformAdapterFactory.is_supported(
                    str(platform_name)
                ):
                    detected = self._detect_platform_name(bot_client)
                    if detected:
                        platform_name = detected

                self.set_bot_instance(bot_client, platform_id, platform_name)
                logger.info(
                    f"Đã làm mới/phát hiện bot instance của platform {platform_id}"
                )

    def get_all_bot_instances(self) -> dict:
        """Lấy mọi bot instance đã tải theo platform_id."""
        return self._bot_instances.copy()

    def get_platform_count(self) -> int:
        """Lấy số nền tảng hiện đã tải."""
        return len(self._bot_instances)

    def get_platform_ids(self) -> list[str]:
        """Lấy danh sách platform ID đã tải."""
        return list(self._bot_instances.keys())

    def has_bot_instance(self) -> bool:
        """Kiểm tra có bot instance khả dụng hay không."""
        return bool(self._bot_instances)

    def has_bot_self_id(self) -> bool:
        """Kiểm tra có ID bot đã cấu hình hay không."""
        return bool(self._bot_self_ids)

    def is_ready_for_auto_analysis(self) -> bool:
        """Kiểm tra đã sẵn sàng phân tích tự động hay chưa."""
        if not self.has_bot_instance():
            logger.debug(
                "[BotManager] Chưa sẵn sàng phân tích tự động: không có bot instance"
            )
            return False

        if not self.has_bot_self_id():
            # Vẫn cho phép thử khi chưa cấu hình/trích xuất được ID và ghi debug.
            logger.debug(
                "[BotManager] Cảnh báo sẵn sàng tự động: bot_self_ids rỗng, có thể ảnh hưởng lọc tin nhắn"
            )
            # Nới lỏng kiểm tra theo #128: có bot instance là có thể thử chạy.
            return True

        return True

    def _get_platform_id_from_instance(self, bot_instance):
        """Lấy platform ID từ bot instance."""
        if hasattr(bot_instance, "platform") and isinstance(bot_instance.platform, str):
            return bot_instance.platform
        return self._default_platform

    def _detect_platform_name(self, bot_instance) -> str | None:
        """
        Phát hiện tên nền tảng từ bot instance để tạo adapter.

        Trả về tên như ``aiocqhttp`` hoặc ``discord``.
        """
        # Ưu tiên thuộc tính platform.
        if hasattr(bot_instance, "platform"):
            platform = bot_instance.platform
            if isinstance(platform, str):
                return platform

        # Kiểm tra đặc trưng API đã biết; OneBot/aiocqhttp có call_action.
        if hasattr(bot_instance, "call_action"):
            return "aiocqhttp"

        # Khớp tên class với nền tảng đã đăng ký trong factory.
        class_name = type(bot_instance).__name__.lower()
        for platform_name in PlatformAdapterFactory.get_supported_platforms():
            if platform_name in class_name:
                return platform_name

        # Khớp mẫu tên class chung cho nền tảng chưa đăng ký.
        known_patterns = {
            "cqhttp": "aiocqhttp",
            "onebot": "aiocqhttp",
        }
        for pattern, platform in known_patterns.items():
            if pattern in class_name:
                return platform

        return None

    # ==================== Phương thức tích hợp DDD ====================

    def get_adapter(self, platform_id: str | None = None) -> PlatformAdapter | None:
        """
        Lấy PlatformAdapter của nền tảng được chỉ định.

        Đây là phương thức chính cho thao tác kiến trúc DDD.
        """
        if platform_id:
            # Luôn kiểm tra client có đổi hay không, ví dụ sau khi restart phiên.
            if platform_id in self._platforms:
                self._refresh_from_stored_platforms()

            return self._adapters.get(platform_id)

        if self._adapters:
            if len(self._adapters) == 1:
                return list(self._adapters.values())[0]

            logger.warning(
                f"Có nhiều adapter {list(self._adapters.keys())} nhưng chưa chỉ định platform_id"
            )
            return None

        # Nếu không có adapter, thử làm mới toàn cục một lần.
        self._refresh_from_stored_platforms()
        if self._adapters:
            if platform_id:
                return self._adapters.get(platform_id)
            if len(self._adapters) == 1:
                return list(self._adapters.values())[0]

        return None

    def get_all_adapters(self) -> dict:
        """Lấy mọi PlatformAdapter theo platform_id."""
        return self._adapters.copy()

    def has_adapter(self, platform_id: str | None = None) -> bool:
        """Kiểm tra nền tảng chỉ định có adapter hay không."""
        if platform_id:
            return platform_id in self._adapters
        return bool(self._adapters)

    def can_analyze(self, platform_id: str | None = None) -> bool:
        """Dùng capability DDD để kiểm tra nền tảng có hỗ trợ phân tích hay không."""
        adapter = self.get_adapter(platform_id)
        if adapter:
            return adapter.get_capabilities().can_analyze()
        return False

    async def auto_discover_bot_instances(self):
        """
        Tự phát hiện mọi bot instance khả dụng.

        Đồng thời tạo PlatformAdapter tương ứng cho mỗi bot.
        """
        platform_manager = getattr(self._context, "platform_manager", None)
        get_insts = getattr(platform_manager, "get_insts", None)
        if self._context is None or not callable(get_insts):
            return {}

        # Dùng API mới để lấy mọi platform instance.
        raw_platforms = get_insts()
        if isinstance(raw_platforms, list):
            platforms: list[object] = raw_platforms
        elif isinstance(raw_platforms, tuple):
            platforms = list(raw_platforms)
        else:
            logger.warning(
                "auto_discover_bot_instances: get_insts() returned non-iterable value."
            )
            return {}
        discovered = {}

        logger.info(
            f"auto_discover_bot_instances: phát hiện {len(platforms)} nền tảng trong manager"
        )

        for platform in platforms:
            # Lấy bot instance.
            bot_client = None
            bot_client = getattr(platform, "lark_api", None)
            platform_get_client = getattr(platform, "get_client", None)
            if not bot_client and callable(platform_get_client):
                bot_client = platform_get_client()

            if not bot_client:
                bot_client = getattr(platform, "bot", None)
            if not bot_client:
                bot_client = getattr(platform, "client", None)

            # Lấy metadata an toàn.
            metadata = getattr(platform, "metadata", None)
            platform_meta_method = getattr(platform, "meta", None)
            if not metadata and callable(platform_meta_method):
                try:
                    metadata = platform_meta_method()
                except Exception:
                    pass

            # Kiểm tra metadata và ID hợp lệ.
            platform_id = None
            if metadata:
                metadata_id = getattr(metadata, "id", None)
                if metadata_id is not None:
                    platform_id = metadata_id
                elif isinstance(metadata, Mapping):
                    platform_id = metadata.get("id")

            if platform_id:
                # Chuẩn hoá platform ID thành str.
                platform_id = str(platform_id)

                # Ghi metadata để debug ID tuỳ chỉnh.
                logger.info(
                    f"[Plugin phân tích nhóm BotManager] Metadata debug ID tuỳ chỉnh, platform: {platform_id}, type: {getattr(metadata, 'type', 'N/A') if not isinstance(metadata, Mapping) else metadata.get('type', 'N/A')}, name: {getattr(metadata, 'name', 'N/A') if not isinstance(metadata, Mapping) else metadata.get('name', 'N/A')}"
                )

                # Phát hiện tên nền tảng từ metadata.
                platform_name = None
                # Ưu tiên type.
                type_val = getattr(metadata, "type", None)
                if isinstance(type_val, str):
                    platform_name = type_val
                elif isinstance(metadata, Mapping):
                    dict_type = metadata.get("type")
                    if isinstance(dict_type, str):
                        platform_name = dict_type
                if not platform_name:
                    name_val = getattr(metadata, "name", None)
                    if isinstance(name_val, str):
                        platform_name = name_val
                    elif isinstance(metadata, Mapping):
                        dict_name = metadata.get("name")
                        if isinstance(dict_name, str):
                            platform_name = dict_name

                # Nếu tên chưa được hỗ trợ, thử phát hiện từ bot instance.
                if (
                    not platform_name
                    or not PlatformAdapterFactory.is_supported(str(platform_name))
                ) and bot_client:
                    detected = self._detect_platform_name(bot_client)
                    if detected:
                        platform_name = detected

                logger.debug(
                    f"Phát hiện platform: {platform_id} ({platform_name}), client sẵn sàng: {bool(bot_client)}"
                )

                # Luôn lưu platform instance dù client đã sẵn sàng hay chưa.
                self._platforms[platform_id] = platform

                if bot_client:
                    logger.info(
                        f"[BotManager] Đăng ký bot instance cho {platform_id} ({platform_name})"
                    )
                    self.set_bot_instance(bot_client, platform_id, platform_name)
                    discovered[platform_id] = bot_client
                else:
                    logger.info(
                        f"Phát hiện platform {platform_id} nhưng client chưa sẵn sàng; sẽ lazy load"
                    )
                    discovered[platform_id] = platform

        if self._adapters:
            logger.info(
                f"Đã tạo {len(self._adapters)} PlatformAdapter: "
                f"{list(self._adapters.keys())}"
            )

        return discovered

    async def initialize_from_config(self):
        """Khởi tạo bot manager từ cấu hình."""
        # Thiết lập danh sách ID bot trong cấu hình.
        bot_self_ids = self.config_manager.get_bot_self_ids()
        if bot_self_ids:
            self.set_bot_self_ids(bot_self_ids)

        # Tự phát hiện mọi bot instance.
        discovered = await self.auto_discover_bot_instances()
        self._is_initialized = True

        return discovered

    def get_status_info(self) -> dict[str, object]:
        """Lấy thông tin trạng thái bot manager."""
        adapter_info = {}
        for pid, adapter in self._adapters.items():
            caps = adapter.get_capabilities()
            adapter_info[pid] = {
                "platform_name": caps.platform_name,
                "can_analyze": caps.can_analyze(),
                "supports_image": caps.supports_image_message,
            }

        return {
            "has_bot_instance": self.has_bot_instance(),
            "bot_self_ids": self._bot_self_ids,
            "platform_count": len(self._bot_instances),
            "platforms": list(self._bot_instances.keys()),
            "adapters": adapter_info,  # Thông tin tích hợp DDD.
            "ready_for_auto_analysis": self.is_ready_for_auto_analysis(),
        }

    def update_from_event(self, event):
        """Cập nhật bot instance từ event cho command thủ công."""
        # Tương thích tên thuộc tính giữa các nền tảng.
        bot_instance = getattr(event, "bot", None) or getattr(event, "client", None)

        if bot_instance:
            # Lấy platform ID từ event.
            platform_id = None
            if hasattr(event, "get_platform_id"):
                platform_id = event.get_platform_id()
            elif hasattr(event, "platform_meta") and hasattr(event.platform_meta, "id"):
                platform_id = event.platform_meta.id
            elif hasattr(event, "platform") and isinstance(event.platform, str):
                platform_id = event.platform

            self.set_bot_instance(bot_instance, platform_id)

            # Ưu tiên lấy ID bot từ event, tránh object bất thường như functools.partial.
            bot_self_id = None
            if hasattr(event, "get_self_id"):
                val = event.get_self_id()
                if (
                    val
                    and isinstance(val, (str, int))
                    and not callable(val)
                    and "partial" not in str(val)
                ):
                    bot_self_id = str(val)

            if not bot_self_id:
                bot_self_id = self._extract_bot_self_id(bot_instance)

            if bot_self_id:
                # Chuyển một ID thành list để xử lý thống nhất.
                self.set_bot_self_ids([bot_self_id])
            else:
                # Nếu instance không có ID, thử danh sách trong cấu hình.
                config_self_ids = self.config_manager.get_bot_self_ids()
                if config_self_ids:
                    self.set_bot_self_ids(config_self_ids)
            return True
        return False

    def _extract_bot_self_id(self, bot_instance):
        """Trích xuất một self ID từ bot instance."""
        return self._extract_bot_self_id_impl(bot_instance)

    def _extract_bot_self_id_impl(self, bot_instance):
        """Trích xuất ID từ bot instance bằng triển khai chung."""
        # Chỉ nhận str/int không callable để tránh dynamic proxy trả functools.partial.
        if hasattr(bot_instance, "self_id") and bot_instance.self_id:
            val = bot_instance.self_id
            if isinstance(val, (str, int)) and not callable(val):
                return str(val)
        if hasattr(bot_instance, "user_id") and bot_instance.user_id:
            val = bot_instance.user_id
            if isinstance(val, (str, int)) and not callable(val):
                return str(val)
        # Discord.py style: client.user.id
        if hasattr(bot_instance, "user") and hasattr(bot_instance.user, "id"):
            val = bot_instance.user.id
            if isinstance(val, (str, int)) and not callable(val):
                return str(val)
        # python-telegram-bot style: bot.id
        if hasattr(bot_instance, "id") and bot_instance.id:
            val = bot_instance.id
            if isinstance(val, (str, int)) and not callable(val):
                return str(val)
        return None

    def validate_for_message_fetching(self, group_id: str) -> bool:
        """Xác thực có thể lấy tin nhắn hay không."""
        return self.has_bot_instance() and bool(group_id)

    def should_filter_bot_message(self, sender_id: str) -> bool:
        """Kiểm tra có nên lọc tin nhắn của bot, hỗ trợ nhiều ID."""
        if not self._bot_self_ids:
            return False

        sender_id_str = str(sender_id)
        # Kiểm tra sender có trong danh sách ID.
        return sender_id_str in self._bot_self_ids

    def is_plugin_enabled(self, platform_id: str, plugin_name: str) -> bool:
        """Kiểm tra plugin có được bật trên nền tảng chỉ định hay không."""
        if platform_id not in self._platforms:
            return True

        platform = self._platforms[platform_id]
        platform_config = getattr(platform, "config", None)
        if not isinstance(platform_config, dict):
            return True

        plugin_set = platform_config.get("plugin_set", ["*"])

        if plugin_set is None:
            return False

        if "*" in plugin_set:
            return True

        return plugin_name in plugin_set
