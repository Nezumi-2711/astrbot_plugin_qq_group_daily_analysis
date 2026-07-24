"""Adapter Telegram dùng Bot API và lịch sử tin nhắn AstrBot."""

import asyncio
import base64
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import TYPE_CHECKING, Any

from ....domain.value_objects.platform_capabilities import (
    TELEGRAM_CAPABILITIES,
    PlatformCapabilities,
)
from ....domain.value_objects.unified_group import UnifiedGroup, UnifiedMember
from ....domain.value_objects.unified_message import (
    MessageContent,
    MessageContentType,
    UnifiedMessage,
)
from ....utils.logger import logger
from ..base import PlatformAdapter

if TYPE_CHECKING:
    from astrbot.api.star import Context

# Dependency Telegram.
try:
    from telegram.ext import ExtBot

    TELEGRAM_AVAILABLE = True
except ImportError:
    ExtBot = None
    TELEGRAM_AVAILABLE = False


TELEGRAM_AVATAR_NEGATIVE_CACHE_TTL = 600
TELEGRAM_AVATAR_NEGATIVE_CACHE_MAX_SIZE = 1024


class TelegramAdapter(PlatformAdapter):
    """Adapter Telegram hỗ trợ gửi tin, avatar, nhóm và lịch sử AstrBot."""

    def __init__(self, bot_instance: Any, config: dict | None = None):
        super().__init__(bot_instance, config)
        self._cached_client: Any = None
        self._context: Context | None = None

        # ID của bot để lọc tin nhắn.
        self.bot_user_id = str(config.get("bot_user_id", "")) if config else ""

        # Thử lấy danh sách self ID của bot từ cấu hình.
        self.bot_self_ids: list[str] = []
        if config:
            ids = config.get("bot_self_ids", [])
            self.bot_self_ids = [str(i) for i in ids] if ids else []
            self._plugin_instance = config.get("plugin_instance")
        else:
            self._plugin_instance = None
        self._platform_id = str(config.get("platform_id", "")).strip() if config else ""
        # user_id -> (expires_at, reason)
        self._avatar_negative_cache: dict[str, tuple[float, str]] = {}

    def set_context(self, context: "Context") -> None:
        """
        Thiết lập context AstrBot.

        Dùng để truy cập các dịch vụ lõi như message_history_manager.
        """
        self._context = context

    def _init_capabilities(self) -> PlatformCapabilities:
        """Trả về capability của nền tảng Telegram."""
        return TELEGRAM_CAPABILITIES

    async def get_group_list(self) -> list[str]:
        """
        Lấy danh sách nhóm.

        Telegram Bot API không hỗ trợ lấy trực tiếp danh sách nhóm, do đó
        fallback sang nhóm đã biết trong KV của plugin.
        """
        groups = []

        # python-telegram-bot hiện chưa hỗ trợ liệt kê toàn bộ chat.

        # Fallback: dùng registry KV.
        if not groups and self._plugin_instance:
            try:
                # Kiểm tra plugin có phương thức lấy nhóm Telegram đã thấy.
                if hasattr(self._plugin_instance, "get_telegram_seen_group_ids"):
                    kv_groups = await self._plugin_instance.get_telegram_seen_group_ids(
                        self._platform_id
                    )
                    if kv_groups:
                        groups.extend(kv_groups)
                        logger.debug(
                            f"[Telegram] Fallback KV lấy được {len(kv_groups)} nhóm"
                        )
            except Exception as e:
                logger.warning(
                    f"[Telegram] Fallback KV lấy danh sách nhóm thất bại: {e}"
                )

        if not groups:
            logger.debug(
                "[Telegram] Không thể lấy danh sách nhóm: API không hỗ trợ và KV rỗng"
            )

        return list(set(groups))

    @property
    def _telegram_client(self) -> Any:
        """
        Lazy load Telegram client.

        Hỗ trợ nhiều đường dẫn để tương thích các phiên bản AstrBot.
        """
        if self._cached_client is not None:
            return self._cached_client

        if not TELEGRAM_AVAILABLE:
            logger.warning(
                "Chưa cài python-telegram-bot; adapter Telegram không khả dụng"
            )
            return None

        # Đường dẫn A: bot chính là ExtBot.
        if ExtBot is not None and isinstance(self.bot, ExtBot):
            self._cached_client = self.bot
            return self._cached_client

        # Đường dẫn B: bot.client.
        if hasattr(self.bot, "client"):
            client = self.bot.client
            if ExtBot is not None and isinstance(client, ExtBot):
                self._cached_client = client
                return self._cached_client

        # Đường dẫn C: bot có send_message, đặc trưng ExtBot.
        if hasattr(self.bot, "send_message") and hasattr(self.bot, "send_photo"):
            self._cached_client = self.bot
            return self._cached_client

        # Thử các thuộc tính khác của bot.
        for attr in ("_client", "telegram_client", "_telegram_client", "bot"):
            if hasattr(self.bot, attr):
                client = getattr(self.bot, attr)
                if hasattr(client, "send_message"):
                    self._cached_client = client
                    return self._cached_client

        logger.warning("Không thể lấy Telegram client từ bot_instance")
        return None

    # ==================== IMessageRepository ====================

    async def fetch_messages(
        self,
        group_id: str,
        days: int = 1,
        max_count: int = 100,
        before_id: str | None = None,
        since_ts: int | None = None,
    ) -> list[UnifiedMessage]:
        """
        Lấy lịch sử tin nhắn.

        Đọc tin nhắn đã lưu từ message_history_manager của AstrBot.
        """
        if not self._context:
            logger.warning("[Telegram] Chưa thiết lập context, không thể lấy lịch sử")
            return []

        try:
            history_mgr = self._context.message_history_manager

            platform_id = self._get_platform_id()
            logger.info(
                f"[Telegram] Đang lấy lịch sử nhóm {group_id}, platform_id: {platform_id}"
            )
            before_id_int: int | None = None
            if before_id:
                try:
                    before_id_int = int(before_id)
                except (TypeError, ValueError):
                    logger.warning(f"[Telegram] before_id invalid: {before_id}")

            if since_ts and since_ts > 0:
                # Dùng UTC để tương thích thời gian lưu trong database.
                cutoff_time = datetime.fromtimestamp(since_ts, timezone.utc)
            else:
                cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
            target_count = max(1, int(max_count))
            page_size = target_count
            current_page = 1

            messages: list[UnifiedMessage] = []
            sender_name_cache: dict[str, str] = {}
            total_records_loaded = 0

            while len(messages) < target_count:
                history_records = await history_mgr.get(
                    platform_id=platform_id,
                    user_id=group_id,
                    page=current_page,
                    page_size=page_size,
                )
                if not history_records:
                    if current_page == 1:
                        logger.info(
                            f"[Telegram] Nhóm {group_id} chưa có tin nhắn đã lưu. "
                            "Tin nhắn cần được interceptor lưu theo thời gian thực."
                        )
                    break

                total_records_loaded += len(history_records)

                # Làm nóng cache bằng nickname hợp lệ trên trang để giảm API call.
                for record in history_records:
                    sender_id = str(getattr(record, "sender_id", "") or "").strip()
                    sender_name = str(getattr(record, "sender_name", "") or "").strip()
                    if sender_id and not self._is_placeholder_sender_name(
                        sender_name, sender_id
                    ):
                        sender_name_cache[sender_id] = sender_name

                oldest_record_time: datetime | None = None
                for record in history_records:
                    if before_id_int is not None:
                        try:
                            rec_id = getattr(record, "id", None)
                            if rec_id is not None and int(rec_id) >= before_id_int:
                                continue
                        except (TypeError, ValueError):
                            pass

                    record_time = getattr(record, "created_at", None)
                    if not record_time:
                        continue
                    if record_time.tzinfo is None:
                        record_time = record_time.replace(tzinfo=timezone.utc)
                    if oldest_record_time is None or record_time < oldest_record_time:
                        oldest_record_time = record_time
                    if record_time < cutoff_time:
                        continue

                    msg = self._convert_history_record(record, group_id)
                    if not msg:
                        continue

                    # Lọc tin nhắn của bot.
                    if self.bot_user_id and msg.sender_id == self.bot_user_id:
                        continue
                    if msg.sender_id in self.bot_self_ids:
                        continue

                    msg = await self._fix_sender_name_if_needed(
                        group_id, msg, sender_name_cache
                    )
                    messages.append(msg)

                # Dừng nếu đã đủ sau khi xử lý hết trang hiện tại.
                if len(messages) >= target_count:
                    break

                # Dừng sớm nếu bản ghi cũ nhất đã vượt cửa sổ thời gian.
                if oldest_record_time and oldest_record_time < cutoff_time:
                    break
                if len(history_records) < page_size:
                    break
                current_page += 1

            messages.sort(key=lambda m: m.timestamp)
            if len(messages) > target_count:
                messages = messages[-target_count:]

            logger.info(
                f"[Telegram] Lấy tin nhắn nhóm {group_id} từ database: "
                f"{len(messages)}/{total_records_loaded} mục"
            )
            return messages

        except Exception as e:
            logger.error(f"[Telegram] Lấy lịch sử tin nhắn thất bại: {e}")
            return []

    def _get_platform_id(self) -> str:
        """Lấy platform ID."""
        if self._platform_id:
            return self._platform_id

        if isinstance(self.config, dict):
            config_platform_id = str(self.config.get("platform_id", "")).strip()
            if config_platform_id:
                return config_platform_id

        # Thử lấy từ bot instance.
        if hasattr(self.bot, "meta") and callable(self.bot.meta):
            try:
                meta = self.bot.meta()  # type: ignore
                if hasattr(meta, "id"):
                    return str(getattr(meta, "id", "telegram"))
            except Exception:
                pass
        return "telegram"

    @staticmethod
    def _is_placeholder_sender_name(name: str | None, sender_id: str | None) -> bool:
        """Kiểm tra sender_name có phải giá trị placeholder hay không."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized.lower() in {"unknown", "none", "null", "nil", "undefined"}:
            return True
        if sender_id and normalized == str(sender_id).strip():
            return True
        return False

    async def _fix_sender_name_if_needed(
        self,
        group_id: str,
        msg: UnifiedMessage,
        sender_name_cache: dict[str, str],
    ) -> UnifiedMessage:
        """
        Nếu sender_name là placeholder, thử sửa qua get_member_info.

        Tương thích dữ liệu lịch sử bẩn và cache theo sender_id để tránh gọi API lặp.
        """
        if not self._is_placeholder_sender_name(msg.sender_name, msg.sender_id):
            return msg

        sender_id = str(msg.sender_id)
        if sender_id in sender_name_cache:
            cached_name = sender_name_cache[sender_id]
            if cached_name == msg.sender_name:
                return msg
            return replace(msg, sender_name=cached_name)

        resolved_name = msg.sender_name
        try:
            member = await self.get_member_info(group_id, sender_id)
            if member:
                candidate = str(member.nickname or "").strip()
                if self._is_placeholder_sender_name(candidate, sender_id):
                    candidate = str(member.card or "").strip()
                if not self._is_placeholder_sender_name(candidate, sender_id):
                    resolved_name = candidate
        except Exception as e:
            logger.debug(f"[Telegram] Sửa sender_name thất bại (uid={sender_id}): {e}")

        sender_name_cache[sender_id] = resolved_name
        if resolved_name == msg.sender_name:
            return msg
        return replace(msg, sender_name=resolved_name)

    def _convert_history_record(
        self, record: Any, group_id: str
    ) -> UnifiedMessage | None:
        """
        Chuyển bản ghi database thành UnifiedMessage.
        """
        try:
            content = record.content
            if not content:
                return None

            # Trích xuất nội dung tin nhắn.
            message_parts = content.get("message", [])
            text_content = ""
            contents = []

            for part in message_parts:
                if isinstance(part, dict):
                    part_type = part.get("type", "")
                    if part_type == "plain" or part_type == "text":
                        text = part.get("text", "")
                        text_content += text
                        contents.append(
                            MessageContent(
                                type=MessageContentType.TEXT,
                                text=text,
                            )
                        )
                    elif part_type == "image":
                        contents.append(
                            MessageContent(
                                type=MessageContentType.IMAGE,
                                url=part.get("url", "")
                                or part.get("attachment_id", ""),
                            )
                        )
                    elif part_type == "at":
                        target_id = (
                            part.get("target_id", "")
                            or part.get("qq", "")
                            or part.get("at_user_id", "")
                        )
                        contents.append(
                            MessageContent(
                                type=MessageContentType.AT,
                                at_user_id=str(target_id),
                            )
                        )

            if not contents:
                contents.append(
                    MessageContent(
                        type=MessageContentType.TEXT,
                        text=text_content,
                    )
                )

            sender_id = str(record.sender_id or "")
            sender_name = str(record.sender_name or "").strip() or "Unknown"

            return UnifiedMessage(
                message_id=str(record.id),
                sender_id=sender_id,
                sender_name=sender_name,
                sender_card=None,
                group_id=group_id,
                text_content=text_content,
                contents=tuple(contents),
                timestamp=int(record.created_at.timestamp()),
                platform="telegram",
                reply_to_id=None,
            )

        except Exception as e:
            logger.debug(f"[Telegram] Chuyển bản ghi lịch sử thất bại: {e}")
            return None

    def convert_to_raw_format(self, messages: list[UnifiedMessage]) -> list[dict]:
        """
        Chuyển định dạng tin nhắn thống nhất sang định dạng tương thích OneBot.

        Dùng để tương thích ngược với logic phân tích hiện tại.
        """
        result = []
        for msg in messages:
            raw = {
                "message_id": msg.message_id,
                "group_id": msg.group_id,
                "time": msg.timestamp,
                "sender": {
                    "user_id": msg.sender_id,
                    "nickname": msg.sender_name,
                    "card": msg.sender_card or "",
                },
                "message": [],
                "user_id": msg.sender_id,
            }

            # Chuyển nội dung tin nhắn.
            for content in msg.contents:
                if content.type == MessageContentType.TEXT:
                    raw["message"].append(
                        {"type": "text", "data": {"text": content.text or ""}}
                    )
                elif content.type == MessageContentType.IMAGE:
                    raw["message"].append(
                        {"type": "image", "data": {"url": content.url or ""}}
                    )
                elif content.type == MessageContentType.AT:
                    raw["message"].append(
                        {"type": "at", "data": {"qq": content.at_user_id or ""}}
                    )

            result.append(raw)

        return result

    # ==================== IMessageSender ====================

    async def send_text(
        self,
        group_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> bool:
        """Gửi tin nhắn văn bản."""
        client = self._telegram_client
        if not client:
            logger.error("[Telegram] Client chưa khởi tạo, không thể gửi văn bản")
            return False

        try:
            # Xử lý ID topic nhóm.
            chat_id, message_thread_id = self._parse_group_id(group_id)

            kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
            if message_thread_id:
                kwargs["message_thread_id"] = int(message_thread_id)
            if reply_to:
                kwargs["reply_to_message_id"] = int(reply_to)

            await client.send_message(**kwargs)
            return True
        except Exception as e:
            logger.error(f"[Telegram] Gửi văn bản thất bại: {e}")
            return False

    async def send_image(
        self,
        group_id: str,
        image_path: str,
        caption: str = "",
    ) -> bool:
        """Gửi tin nhắn ảnh."""
        client = self._telegram_client
        if not client:
            logger.error("[Telegram] Client chưa khởi tạo, không thể gửi ảnh")
            return False

        try:
            chat_id, message_thread_id = self._parse_group_id(group_id)
            file_obj: Any = None
            is_temp_obj = False

            kwargs: dict[str, Any] = {"chat_id": chat_id}
            if message_thread_id:
                kwargs["message_thread_id"] = int(message_thread_id)
            if caption:
                kwargs["caption"] = caption

            # 1. Xử lý thống nhất nguồn Base64, URL hoặc tệp local.
            if image_path.startswith("base64://"):
                data = base64.b64decode(image_path[len("base64://") :])
                file_obj = BytesIO(data)
                is_temp_obj = True
            elif image_path.startswith("data:"):
                parts = image_path.split(",", 1)
                if len(parts) == 2:
                    data = base64.b64decode(parts[1])
                    file_obj = BytesIO(data)
                    is_temp_obj = True
            elif image_path.startswith(("http://", "https://")):
                try:
                    import aiohttp

                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            image_path, timeout=aiohttp.ClientTimeout(total=30)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                file_obj = BytesIO(data)
                                is_temp_obj = True
                            else:
                                file_obj = image_path  # Thử gửi URL trực tiếp.
                except Exception as e:
                    logger.warning(
                        f"[Telegram] Tải ảnh thất bại, thử gửi trực tiếp: {e}"
                    )
                    file_obj = image_path
            else:
                # Tệp local.
                if os.path.exists(image_path):
                    file_obj = open(image_path, "rb")
                    is_temp_obj = True
                else:
                    file_obj = image_path

            # 2. Gửi ảnh.
            kwargs["photo"] = file_obj
            try:
                await client.send_photo(**kwargs)
            finally:
                if is_temp_obj and hasattr(file_obj, "close"):
                    file_obj.close()

            return True

        except Exception as e:
            err_msg = str(e)
            # Photo_invalid_dimensions: tỷ lệ hoặc tổng kích thước ảnh không hợp lệ.
            if (
                "Photo_invalid_dimensions" in err_msg
                or "Photo invalid dimensions" in err_msg
            ):
                logger.warning(
                    "[Telegram] Kích thước ảnh vượt giới hạn, thử gửi dạng tệp..."
                )
                # Tạo tên tệp dễ hiểu hơn.
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                fn = f"analysis_report_{group_id}_{ts}.png"
                return await self.send_file(group_id, image_path, filename=fn)

            logger.error(f"[Telegram] Gửi ảnh thất bại: {e}")
            return False

    async def send_file(
        self,
        group_id: str,
        file_path: str,
        filename: str | None = None,
    ) -> bool:
        """Gửi tin nhắn tệp."""
        client = self._telegram_client
        if not client:
            logger.error("[Telegram] Client chưa khởi tạo, không thể gửi tệp")
            return False

        try:
            chat_id, message_thread_id = self._parse_group_id(group_id)
            file_obj: Any = None
            is_temp_obj = False

            kwargs: dict[str, Any] = {"chat_id": chat_id}
            if message_thread_id:
                kwargs["message_thread_id"] = int(message_thread_id)

            # 1. Xử lý thống nhất nguồn Base64 hoặc tệp local.
            if file_path.startswith("base64://"):
                data = base64.b64decode(file_path[len("base64://") :])
                file_obj = BytesIO(data)
                is_temp_obj = True
                if not filename:
                    filename = "file.png"
            elif file_path.startswith("data:"):
                parts = file_path.split(",", 1)
                if len(parts) == 2:
                    data = base64.b64decode(parts[1])
                    file_obj = BytesIO(data)
                    is_temp_obj = True
                    if not filename:
                        filename = "file.png"
            elif os.path.isfile(file_path):
                file_obj = open(file_path, "rb")
                is_temp_obj = True
                if not filename:
                    filename = os.path.basename(file_path)
            else:
                # Có thể là URL hoặc cache ID.
                file_obj = file_path
                if not filename:
                    filename = "file"

            kwargs["document"] = file_obj
            kwargs["filename"] = filename

            try:
                await client.send_document(**kwargs)
            finally:
                if is_temp_obj and hasattr(file_obj, "close"):
                    file_obj.close()

            return True
        except Exception as e:
            logger.error(f"[Telegram] Gửi tệp thất bại: {e}")
            return False

    async def send_forward_msg(self, group_id: str, nodes: list[dict]) -> bool:
        """
        Gửi tin nhắn chuyển tiếp đã gộp.

        Telegram không hỗ trợ chuỗi chuyển tiếp native nên gửi dạng văn bản định dạng.
        """
        if not nodes:
            return True

        lines = ["📊 **Báo cáo phân tích**\n"]
        for node in nodes:
            data = node.get("data", node)
            name = data.get("name", "AstrBot")
            content = data.get("content", "")
            if isinstance(content, list):
                # Chuỗi tin nhắn.
                text_parts = []
                for seg in content:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        text_parts.append(seg.get("data", {}).get("text", ""))
                content = "".join(text_parts)
            lines.append(f"**[{name}]**\n{content}\n")

        full_text = "\n".join(lines)

        # Chia đoạn vì Telegram giới hạn 4096 ký tự.
        max_len = 4000
        if len(full_text) > max_len:
            parts = [
                full_text[i : i + max_len] for i in range(0, len(full_text), max_len)
            ]
            for part in parts:
                if not await self.send_text(group_id, part):
                    return False
            return True
        else:
            return await self.send_text(group_id, full_text)

    # ==================== IGroupInfoRepository ====================

    async def get_group_info(self, group_id: str) -> UnifiedGroup | None:
        """Lấy thông tin nhóm."""
        client = self._telegram_client
        if not client:
            return None

        try:
            chat_id, _ = self._parse_group_id(group_id)
            chat = await client.get_chat(chat_id=chat_id)

            return UnifiedGroup(
                group_id=str(chat.id),
                group_name=chat.title or "Unknown",
                member_count=await client.get_chat_member_count(chat_id) or 0,
                description=chat.description,
                platform="telegram",
            )
        except Exception as e:
            logger.debug(f"[Telegram] Lấy thông tin nhóm thất bại: {e}")
            return None

    async def get_member_list(self, group_id: str) -> list[UnifiedMember]:
        """
        Lấy danh sách thành viên.

        Telegram Bot API giới hạn việc lấy danh sách thành viên.
        """
        client = self._telegram_client
        if not client:
            return []

        try:
            chat_id, _ = self._parse_group_id(group_id)
            # Telegram Bot API chỉ cho lấy danh sách quản trị viên.
            admins = await client.get_chat_administrators(chat_id=chat_id)

            members = []
            for admin in admins:
                user = admin.user
                members.append(
                    UnifiedMember(
                        user_id=str(user.id),
                        nickname=user.full_name
                        or user.first_name
                        or user.username
                        or "Unknown",
                        card=user.username,
                        role="admin" if admin.status == "administrator" else "owner",
                    )
                )
            return members
        except Exception as e:
            logger.debug(f"[Telegram] Lấy danh sách thành viên thất bại: {e}")
            return []

    async def get_member_info(
        self,
        group_id: str,
        user_id: str,
    ) -> UnifiedMember | None:
        """Lấy thông tin thành viên."""
        client = self._telegram_client
        if not client:
            return None

        try:
            chat_id, _ = self._parse_group_id(group_id)
            member = await client.get_chat_member(chat_id=chat_id, user_id=int(user_id))
            user = member.user

            role = "member"
            if member.status in ("creator", "owner"):
                role = "owner"
            elif member.status == "administrator":
                role = "admin"

            return UnifiedMember(
                user_id=str(user.id),
                nickname=user.full_name
                or user.first_name
                or user.username
                or "Unknown",
                card=user.username,
                role=role,
            )
        except Exception as e:
            logger.debug(f"[Telegram] Lấy thông tin thành viên thất bại: {e}")
            return None

    # ==================== IAvatarRepository ====================

    async def get_user_avatar_url(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """
        Lấy URL avatar người dùng.

        Telegram cần gọi API để lấy tệp avatar.
        """
        client = self._telegram_client
        if not client:
            logger.warning(
                f"[Telegram] Lấy avatar người dùng thất bại uid={user_id}: client chưa khởi tạo"
            )
            return None

        user_id_str = str(user_id).strip()
        cached_reason = self._get_avatar_negative_cache_reason(user_id_str)
        if cached_reason:
            logger.debug(
                f"[Telegram] Bỏ qua avatar uid={user_id_str}: trúng negative cache, "
                f"lý do thất bại trước: {cached_reason}"
            )
            return None

        try:
            tg_user_id = int(user_id_str)
        except (TypeError, ValueError):
            reason = f"ID người dùng không phải số nguyên hợp lệ: {user_id!r}"
            self._remember_avatar_negative(user_id_str, reason)
            logger.warning(
                f"[Telegram] Lấy avatar người dùng thất bại uid={user_id}: {reason}"
            )
            return None

        try:
            photos = await client.get_user_profile_photos(user_id=tg_user_id, limit=1)
            if photos.photos:
                # Lấy avatar kích thước lớn nhất.
                photo_sizes = photos.photos[0]
                if photo_sizes:
                    # Chọn kích thước gần yêu cầu nhất.
                    best = photo_sizes[-1]  # Phần tử cuối thường lớn nhất.
                    file = await client.get_file(best.file_id)
                    if file.file_path:
                        # Dựng URL đầy đủ; File.file_path thường chỉ trả phần path.

                        file_path = file.file_path
                        if file_path.startswith("http"):
                            return file_path

                        # Thử dựng URL đầy đủ.
                        if hasattr(client, "token"):
                            return f"https://api.telegram.org/file/bot{client.token}/{file_path}"

                        # Trả None nếu không lấy được token.
                        reason = "get_file trả file_path tương đối nhưng client không có token để dựng URL tải"
                        self._remember_avatar_negative(user_id_str, reason)
                        logger.warning(
                            f"[Telegram] Lấy avatar người dùng thất bại uid={user_id_str}: {reason}"
                        )
                        return None
                    reason = "get_file không trả file_path"
                    self._remember_avatar_negative(user_id_str, reason)
                    logger.warning(
                        f"[Telegram] Lấy avatar người dùng thất bại uid={user_id_str}: {reason}"
                    )
                    return None
                reason = "Avatar đầu tiên từ get_user_profile_photos không có kích thước khả dụng"
                self._remember_avatar_negative(user_id_str, reason)
                logger.info(
                    f"[Telegram] Lấy avatar người dùng thất bại uid={user_id_str}: {reason}"
                )
                return None
            reason = "get_user_profile_photos trả danh sách rỗng; avatar có thể không công khai"
            self._remember_avatar_negative(user_id_str, reason)
            logger.info(
                f"[Telegram] Lấy avatar người dùng thất bại uid={user_id_str}: {reason}"
            )
            return None
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            self._remember_avatar_negative(user_id_str, reason)
            logger.warning(
                f"[Telegram] Lấy avatar người dùng thất bại uid={user_id_str}: {reason}"
            )
            return None

    async def get_user_avatar_data(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """Lấy dữ liệu Base64 của avatar."""
        # Chưa triển khai, trả về None.
        logger.debug(
            f"[Telegram] Không thể lấy dữ liệu avatar uid={user_id}: get_user_avatar_data chưa triển khai"
        )
        return None

    async def get_group_avatar_url(
        self,
        group_id: str,
        size: int = 100,
    ) -> str | None:
        """Lấy URL avatar nhóm."""
        client = self._telegram_client
        if not client:
            logger.warning(
                f"[Telegram] Lấy avatar nhóm thất bại group_id={group_id}: client chưa khởi tạo"
            )
            return None

        try:
            chat_id, _ = self._parse_group_id(group_id)
            chat = await client.get_chat(chat_id=chat_id)

            if chat.photo:
                file = await client.get_file(chat.photo.big_file_id)
                if file.file_path:
                    file_path = file.file_path
                    if file_path.startswith("http"):
                        return file_path

                    if hasattr(client, "token"):
                        return f"https://api.telegram.org/file/bot{client.token}/{file_path}"

                    logger.warning(
                        f"[Telegram] Lấy avatar nhóm thất bại group_id={group_id}: "
                        "get_file trả file_path tương đối nhưng client không có token để dựng URL tải"
                    )
                    return None
                logger.warning(
                    f"[Telegram] Lấy avatar nhóm thất bại group_id={group_id}: get_file không trả file_path"
                )
                return None
            logger.info(
                f"[Telegram] Lấy avatar nhóm thất bại group_id={group_id}: nhóm chưa đặt avatar hoặc bot không thấy"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[Telegram] Lấy avatar nhóm thất bại group_id={group_id}: {type(e).__name__}: {e}"
            )
            return None

    def _prune_avatar_negative_cache(self) -> None:
        """Xoá mục hết hạn và giới hạn negative cache để tránh tăng vô hạn."""
        cache = self._avatar_negative_cache
        if not cache:
            return

        now = time.monotonic()
        expired_keys = [
            user_id
            for user_id, (expires_at, _reason) in cache.items()
            if expires_at <= now
        ]
        for user_id in expired_keys:
            cache.pop(user_id, None)

        overflow = len(cache) - TELEGRAM_AVATAR_NEGATIVE_CACHE_MAX_SIZE
        if overflow <= 0:
            return

        for user_id, _ in sorted(cache.items(), key=lambda item: item[1][0])[:overflow]:
            cache.pop(user_id, None)

    def _get_avatar_negative_cache_reason(self, user_id: str) -> str | None:
        self._prune_avatar_negative_cache()
        cached = self._avatar_negative_cache.get(user_id)
        if not cached:
            return None

        expires_at, reason = cached
        if time.monotonic() >= expires_at:
            self._avatar_negative_cache.pop(user_id, None)
            return None
        return reason

    def _remember_avatar_negative(self, user_id: str, reason: str) -> None:
        self._prune_avatar_negative_cache()
        self._avatar_negative_cache[user_id] = (
            time.monotonic() + TELEGRAM_AVATAR_NEGATIVE_CACHE_TTL,
            reason,
        )
        self._prune_avatar_negative_cache()

    async def batch_get_avatar_urls(
        self,
        user_ids: list[str],
        size: int = 100,
    ) -> dict[str, str | None]:
        """Lấy hàng loạt URL avatar."""
        if not user_ids:
            return {}

        # Giới hạn concurrency để tránh chờ tuần tự lâu hoặc quá tải Telegram API.
        semaphore = asyncio.Semaphore(8)

        async def _fetch_avatar(uid: str) -> tuple[str, str | None]:
            async with semaphore:
                try:
                    return uid, await self.get_user_avatar_url(uid, size)
                except Exception as e:
                    logger.debug(
                        f"[Telegram] Lấy avatar hàng loạt thất bại uid={uid}: {e}"
                    )
                    return uid, None

        pairs = await asyncio.gather(*(_fetch_avatar(uid) for uid in user_ids))
        return dict(pairs)

    async def set_reaction(
        self, group_id: str, message_id: str, emoji: str | int, is_add: bool = True
    ) -> bool:
        """
        Triển khai reaction tin nhắn Telegram.
        """
        client = self._telegram_client
        if not client:
            return False

        try:
            chat_id, _ = self._parse_group_id(group_id)

            # set_message_reaction cần Bot API 7.0 và PTB 20.8 trở lên.
            if not hasattr(client, "set_message_reaction"):
                return False

            if not is_add:
                try:
                    from telegram import ReactionTypeEmoji

                    await client.set_message_reaction(
                        chat_id=chat_id,
                        message_id=int(message_id),
                        reaction=[],
                    )
                    return True
                except ImportError:
                    await client.set_message_reaction(
                        chat_id=chat_id,
                        message_id=int(message_id),
                        reaction=None,
                    )
                    return True

            reaction_key = str(emoji)
            candidates = {
                "analysis_started": ("👀", "🤔", "👍"),
                "analysis_done": ("👌", "👍", "🎉"),
                "🔍": ("👀", "🤔", "👍"),
                "📊": ("👌", "👍", "🎉"),
                "289": ("👀", "🤔", "👍"),
                "124": ("👌", "👍", "🎉"),
                "424": ("👌", "👍", "🎉"),
                "✅": ("👌", "👍", "🎉"),
            }.get(reaction_key, (reaction_key,))

            try:
                from telegram import ReactionTypeEmoji

                for candidate in candidates:
                    try:
                        await client.set_message_reaction(
                            chat_id=chat_id,
                            message_id=int(message_id),
                            reaction=[ReactionTypeEmoji(emoji=candidate)],
                        )
                        return True
                    except Exception:
                        continue
            except ImportError:
                for candidate in candidates:
                    try:
                        await client.set_message_reaction(
                            chat_id=chat_id,
                            message_id=int(message_id),
                            reaction=candidate,
                        )
                        return True
                    except Exception:
                        continue

            logger.debug(
                f"[Telegram] set_reaction không khớp emoji khả dụng: emoji={emoji}, candidates={candidates}"
            )
            return False
        except Exception as e:
            logger.debug(f"[Telegram] set_reaction thất bại: {e}")
            return False

    # ==================== Phương thức hỗ trợ ====================

    def _parse_group_id(self, group_id: str) -> tuple[str, str | None]:
        """
        Parse ID nhóm.

        ID nhóm topic Telegram có dạng ``chat_id#thread_id``.

        Returns:
            tuple[str, str | None]: (chat_id, message_thread_id)
        """
        if "#" in group_id:
            parts = group_id.split("#", 1)
            return parts[0], parts[1]
        return group_id, None
