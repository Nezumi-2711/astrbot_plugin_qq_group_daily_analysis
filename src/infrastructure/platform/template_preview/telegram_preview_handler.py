"""Xử lý tương tác preview template trên Telegram."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....utils.logger import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ....application.commands.template_command_service import TemplateCommandService
    from ...config.config_manager import ConfigManager

try:
    from telegram import (
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        InputMediaDocument,
        InputMediaPhoto,
        Update,
    )
    from telegram.error import BadRequest
    from telegram.ext import CallbackQueryHandler, ContextTypes

    TELEGRAM_RUNTIME_AVAILABLE = True
except Exception:
    TELEGRAM_RUNTIME_AVAILABLE = False
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    InputMediaPhoto = None
    InputMediaDocument = None
    Update = None
    BadRequest = Exception
    CallbackQueryHandler = None
    ContextTypes = None


@dataclass
class _PreviewSession:
    token: str
    platform_id: str
    chat_id: int | str
    message_thread_id: int | None
    message_id: int
    requester_id: int
    templates: list[str]
    index: int
    created_at: float

    @property
    def current_template(self) -> str:
        return self.templates[self.index]


class TelegramTemplatePreviewHandler:
    """Trình xử lý nút preview Telegram (←/Xác nhận/→)."""

    _SESSION_TTL_SECONDS = 2 * 60 * 60
    _MAX_SESSIONS = 200
    _CONNECT_TIMEOUT = 20
    _READ_TIMEOUT = 120
    _WRITE_TIMEOUT = 120
    _POOL_TIMEOUT = 20

    def __init__(
        self,
        config_manager: ConfigManager,
        template_service: TemplateCommandService,
    ):
        self.config_manager = config_manager
        self.template_service = template_service
        self._sessions: dict[str, _PreviewSession] = {}
        self._registered_platform_ids: set[str] = set()
        self._handlers: dict[str, tuple[Any, Any]] = {}
        self._platform_clients: dict[str, Any] = {}
        self._callback_prefix = f"qda_tpl_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def supports(event: AstrMessageEvent) -> bool:
        """Kiểm tra có phải sự kiện Telegram hay không."""
        try:
            return (event.get_platform_name() or "").lower() == "telegram"
        except Exception:
            return False

    # Tương thích ngược với tên gọi cũ.
    is_telegram_event = supports

    async def ensure_callback_handlers_registered(self, context: Any) -> None:
        """Đăng ký callback nút cho mọi nền tảng Telegram."""
        if not TELEGRAM_RUNTIME_AVAILABLE:
            return
        if not context or not hasattr(context, "platform_manager"):
            return

        platforms = context.platform_manager.get_insts()
        seen_platform_ids: set[str] = set()
        for platform in platforms:
            platform_id, platform_name = self._extract_platform_meta(platform)
            if platform_name != "telegram":
                continue
            if not platform_id:
                continue

            seen_platform_ids.add(platform_id)
            client = self._extract_platform_client(platform)
            if client is not None:
                self._platform_clients[platform_id] = client

            application = getattr(platform, "application", None)
            if not application:
                continue

            existing = self._handlers.get(platform_id)
            if existing:
                old_application, old_handler = existing
                if old_application is application:
                    self._registered_platform_ids.add(platform_id)
                    continue

                # Hot-swap nền tảng: gỡ handler application cũ rồi gắn lại.
                try:
                    old_application.remove_handler(old_handler)
                    logger.info(
                        f"[TemplatePreview][Telegram] Phát hiện application thay đổi, đã gỡ callback cũ: platform_id={platform_id}"
                    )
                except Exception as e:
                    logger.debug(
                        f"[TemplatePreview][Telegram] Gỡ callback cũ thất bại: platform_id={platform_id}, err={e}"
                    )
                self._handlers.pop(platform_id, None)
                self._registered_platform_ids.discard(platform_id)

            try:
                handler = CallbackQueryHandler(
                    self._on_callback_query,
                    pattern=rf"^{re.escape(self._callback_prefix)}:",
                )
                application.add_handler(handler)
                self._registered_platform_ids.add(platform_id)
                self._handlers[platform_id] = (application, handler)
                logger.info(
                    f"[TemplatePreview][Telegram] Đã đăng ký callback: platform_id={platform_id}"
                )
            except Exception as e:
                logger.warning(
                    f"[TemplatePreview][Telegram] Đăng ký callback thất bại: platform_id={platform_id}, err={e}"
                )

        # Dọn handler sót lại khi nền tảng offline để tránh rò rỉ tài nguyên.
        stale_ids = [
            platform_id
            for platform_id in list(self._handlers.keys())
            if platform_id not in seen_platform_ids
        ]
        for stale_platform_id in stale_ids:
            old_application, old_handler = self._handlers.pop(stale_platform_id)
            try:
                old_application.remove_handler(old_handler)
                logger.info(
                    f"[TemplatePreview][Telegram] Đã dọn callback nền tảng offline: platform_id={stale_platform_id}"
                )
            except Exception as e:
                logger.debug(
                    f"[TemplatePreview][Telegram] Dọn callback nền tảng offline thất bại: platform_id={stale_platform_id}, err={e}"
                )
            self._registered_platform_ids.discard(stale_platform_id)
            self._platform_clients.pop(stale_platform_id, None)

    async def unregister_callback_handlers(self) -> None:
        """Gỡ callback đã đăng ký khi plugin kết thúc."""
        if not TELEGRAM_RUNTIME_AVAILABLE:
            return

        for platform_id, (application, handler) in list(self._handlers.items()):
            try:
                application.remove_handler(handler)
                logger.info(
                    f"[TemplatePreview][Telegram] Đã gỡ callback: platform_id={platform_id}"
                )
            except Exception as e:
                logger.debug(
                    f"[TemplatePreview][Telegram] Gỡ callback thất bại: platform_id={platform_id}, err={e}"
                )
        self._handlers.clear()
        self._registered_platform_ids.clear()
        self._platform_clients.clear()

    async def send_preview_message(
        self,
        event: AstrMessageEvent,
        platform_id: str,
        available_templates: list[str],
    ) -> bool:
        """
        Gửi tin nhắn preview template tương tác trên Telegram.

        Trả về True nếu handler đã gửi; False để caller dùng fallback mặc định.
        """
        if not TELEGRAM_RUNTIME_AVAILABLE:
            return False
        if not available_templates:
            return False

        client = self._get_event_client(event, platform_id)
        if client is None:
            logger.warning("[TemplatePreview][Telegram] Không thể lấy Telegram client")
            return False

        target = self._resolve_chat_target(event)
        if target is None:
            return False
        chat_id, message_thread_id = target

        try:
            requester_id = int(str(event.get_sender_id()))
        except Exception:
            logger.warning(
                "[TemplatePreview][Telegram] sender_id không hợp lệ, không thể tạo phiên tương tác"
            )
            return False

        current_template = self.config_manager.get_report_template()
        if current_template in available_templates:
            index = available_templates.index(current_template)
        else:
            index = 0

        token = uuid.uuid4().hex[:8]
        keyboard = self._build_keyboard(token)
        caption = self._build_caption(
            template_name=available_templates[index],
            index=index,
            total=len(available_templates),
        )

        image_path = self.template_service.resolve_template_preview_path(
            available_templates[index]
        )
        if not image_path:
            return False

        payload: dict[str, Any] = {"chat_id": chat_id, "reply_markup": keyboard}
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id

        try:
            with open(image_path, "rb") as image_file:
                sent_msg = await client.send_photo(
                    photo=image_file,
                    caption=caption,
                    connect_timeout=self._CONNECT_TIMEOUT,
                    read_timeout=self._READ_TIMEOUT,
                    write_timeout=self._WRITE_TIMEOUT,
                    pool_timeout=self._POOL_TIMEOUT,
                    **payload,
                )
        except BadRequest as e:
            if not self._is_photo_dimension_error(e):
                raise
            with open(image_path, "rb") as image_file:
                sent_msg = await client.send_document(
                    document=image_file,
                    caption=caption,
                    connect_timeout=self._CONNECT_TIMEOUT,
                    read_timeout=self._READ_TIMEOUT,
                    write_timeout=self._WRITE_TIMEOUT,
                    pool_timeout=self._POOL_TIMEOUT,
                    **payload,
                )

        self._sessions[token] = _PreviewSession(
            token=token,
            platform_id=platform_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            message_id=sent_msg.message_id,
            requester_id=requester_id,
            templates=available_templates.copy(),
            index=index,
            created_at=time.time(),
        )
        self._cleanup_expired_sessions()
        logger.info(
            "[TemplatePreview][Telegram] Đã gửi preview tương tác: "
            f"platform_id={platform_id} chat_id={chat_id} token={token} templates={len(available_templates)}"
        )
        return True

    async def send_preview_image_fallback(
        self,
        event: AstrMessageEvent,
        platform_id: str,
        template_name: str,
    ) -> bool:
        """Fallback Telegram: gửi trực tiếp một ảnh preview."""
        if not TELEGRAM_RUNTIME_AVAILABLE:
            return False

        image_path = self.template_service.resolve_template_preview_path(template_name)
        if not image_path:
            return False

        client = self._get_event_client(event, platform_id)
        if client is None:
            logger.warning(
                "[TemplatePreview][Telegram] Gửi ảnh fallback thất bại: không thể lấy client"
            )
            return False

        target = self._resolve_chat_target(event)
        if target is None:
            return False
        chat_id, message_thread_id = target

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": f"🖼 Preview template hiện tại: {template_name}",
            "connect_timeout": self._CONNECT_TIMEOUT,
            "read_timeout": self._READ_TIMEOUT,
            "write_timeout": self._WRITE_TIMEOUT,
            "pool_timeout": self._POOL_TIMEOUT,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id

        try:
            with open(image_path, "rb") as image_file:
                await client.send_photo(photo=image_file, **payload)
        except BadRequest as e:
            if not self._is_photo_dimension_error(e):
                raise
            with open(image_path, "rb") as image_file:
                await client.send_document(document=image_file, **payload)
        return True

    async def handle_view_templates(
        self,
        event: AstrMessageEvent,
        platform_id: str,
        available_templates: list[str],
    ) -> tuple[bool, list[Any]]:
        """Xử lý thống nhất quy trình /xemmau trên Telegram."""
        if not self.supports(event):
            return False, []

        results: list[Any] = []

        async def _append_fallback_results() -> None:
            current_template = self.config_manager.get_report_template()
            template_list_str = "\n".join(
                [f"【{i}】{t}" for i, t in enumerate(available_templates, start=1)]
            )
            results.append(
                event.plain_result(
                    f"""🎨 Danh sách template báo cáo khả dụng
📌 Đang dùng: {current_template}

{template_list_str}

💡 Dùng /maubc [số thứ tự] để chuyển"""
                )
            )

            try:
                sent_preview = await self.send_preview_image_fallback(
                    event=event,
                    platform_id=platform_id,
                    template_name=current_template,
                )
                if not sent_preview:
                    results.append(
                        event.plain_result(
                            "⚠️ Gửi ảnh preview template hiện tại thất bại"
                        )
                    )
            except Exception as image_err:
                logger.warning(
                    f"[TemplatePreview][Telegram] Gửi ảnh fallback thất bại: {image_err}"
                )
                results.append(
                    event.plain_result("⚠️ Gửi ảnh preview template hiện tại thất bại")
                )

        try:
            sent = await self.send_preview_message(
                event=event,
                platform_id=platform_id,
                available_templates=available_templates,
            )
            if sent:
                return True, results
            await _append_fallback_results()
            return True, results
        except Exception as e:
            logger.warning(
                f"[TemplatePreview][Telegram] Gửi preview tương tác thất bại, chuyển sang chế độ thường: {e}"
            )
            await _append_fallback_results()
            return True, results

    async def _on_callback_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not TELEGRAM_RUNTIME_AVAILABLE:
            return
        if not update.callback_query or not update.callback_query.data:
            return

        self._cleanup_expired_sessions()

        query = update.callback_query
        data = query.data
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer("Thao tác không hợp lệ", show_alert=False)
            return

        _, token, action = parts
        session = self._sessions.get(token)
        if not session:
            await query.answer(
                "Phiên preview đã hết hạn, hãy gửi lại /xemmau", show_alert=True
            )
            return
        if time.time() - session.created_at > self._SESSION_TTL_SECONDS:
            self._sessions.pop(token, None)
            await query.answer(
                "Phiên preview đã hết hạn, hãy gửi lại /xemmau", show_alert=True
            )
            return

        if not query.from_user:
            await query.answer("Không thể nhận diện người thao tác", show_alert=False)
            return
        if int(query.from_user.id) != session.requester_id:
            await query.answer(
                "Chỉ người gọi lệnh mới có thể thao tác preview này", show_alert=True
            )
            return

        if not query.message:
            await query.answer("Tin nhắn không còn hiệu lực", show_alert=False)
            return

        if query.message.message_id != session.message_id or str(
            query.message.chat_id
        ) != str(session.chat_id):
            await query.answer(
                "Trạng thái preview không khớp, hãy gửi lại /xemmau", show_alert=True
            )
            return

        if action == "prev":
            session.index = (session.index - 1) % len(session.templates)
            await self._edit_preview_message(query, session)
            await query.answer()
            return

        if action == "next":
            session.index = (session.index + 1) % len(session.templates)
            await self._edit_preview_message(query, session)
            await query.answer()
            return

        if action == "apply":
            template_name = session.current_template
            self.config_manager.set_report_template(template_name)
            await self._edit_preview_message(query, session, applied=True)
            await query.answer(f"Đã đặt template: {template_name}", show_alert=False)
            logger.info(
                "[TemplatePreview][Telegram] Đã áp dụng template: "
                f"platform_id={session.platform_id} template={template_name} requester={session.requester_id}"
            )
            return

        await query.answer("Thao tác không xác định", show_alert=False)

    async def _edit_preview_message(
        self, query: Any, session: _PreviewSession, applied: bool = False
    ) -> None:
        template_name = session.current_template
        caption = self._build_caption(
            template_name=template_name,
            index=session.index,
            total=len(session.templates),
            applied=applied,
        )
        keyboard = self._build_keyboard(session.token)
        image_path = self.template_service.resolve_template_preview_path(template_name)
        if not image_path:
            await query.edit_message_caption(
                caption=caption,
                reply_markup=keyboard,
            )
            return

        try:
            with open(image_path, "rb") as image_file:
                media = InputMediaPhoto(media=image_file, caption=caption)
                await query.edit_message_media(
                    media=media,
                    reply_markup=keyboard,
                    connect_timeout=self._CONNECT_TIMEOUT,
                    read_timeout=self._READ_TIMEOUT,
                    write_timeout=self._WRITE_TIMEOUT,
                    pool_timeout=self._POOL_TIMEOUT,
                )
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            if self._is_photo_dimension_error(e):
                try:
                    with open(image_path, "rb") as image_file:
                        media = InputMediaDocument(media=image_file, caption=caption)
                        await query.edit_message_media(
                            media=media,
                            reply_markup=keyboard,
                            connect_timeout=self._CONNECT_TIMEOUT,
                            read_timeout=self._READ_TIMEOUT,
                            write_timeout=self._WRITE_TIMEOUT,
                            pool_timeout=self._POOL_TIMEOUT,
                        )
                except BadRequest as document_error:
                    if "message is not modified" in str(document_error).lower():
                        return
                    raise
                return
            raise

    def _build_keyboard(self, token: str) -> Any:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="←",
                        callback_data=f"{self._callback_prefix}:{token}:prev",
                    ),
                    InlineKeyboardButton(
                        text="Xác nhận",
                        callback_data=f"{self._callback_prefix}:{token}:apply",
                    ),
                    InlineKeyboardButton(
                        text="→",
                        callback_data=f"{self._callback_prefix}:{token}:next",
                    ),
                ]
            ]
        )

    def _build_caption(
        self,
        template_name: str,
        index: int,
        total: int,
        applied: bool = False,
    ) -> str:
        current_active = self.config_manager.get_report_template()
        active_mark = (
            "✅ Đang áp dụng" if template_name == current_active else "Chưa áp dụng"
        )
        apply_mark = "\n\n✅ Đã áp dụng template này" if applied else ""
        return (
            f"🎨 Preview template ({index + 1}/{total})\n"
            f"Mục hiện tại: {template_name}\n"
            f"Trạng thái: {active_mark}\n\n"
            "Thao tác: ← Trước / Xác nhận áp dụng / → Sau"
            f"{apply_mark}"
        )

    @staticmethod
    def _extract_platform_meta(platform: Any) -> tuple[str | None, str | None]:
        metadata = getattr(platform, "metadata", None)
        if not metadata and hasattr(platform, "meta"):
            try:
                metadata = platform.meta()
            except Exception:
                metadata = None

        platform_id = None
        platform_name = None
        if metadata:
            if isinstance(metadata, dict):
                platform_id = metadata.get("id")
                platform_name = metadata.get("type") or metadata.get("name")
            else:
                platform_id = getattr(metadata, "id", None)
                platform_name = getattr(metadata, "type", None) or getattr(
                    metadata, "name", None
                )
        if platform_name:
            platform_name = str(platform_name).lower()
        if platform_id:
            platform_id = str(platform_id)
        return platform_id, platform_name

    @staticmethod
    def _extract_platform_client(platform: Any) -> Any | None:
        client = None
        if hasattr(platform, "get_client"):
            try:
                client = platform.get_client()
            except Exception:
                client = None
        if client is None:
            client = getattr(platform, "client", None)
        if client is None:
            application = getattr(platform, "application", None)
            if application is not None:
                client = getattr(application, "bot", None)
        if client is None:
            return None
        if not hasattr(client, "send_photo"):
            return None
        return client

    @staticmethod
    def _get_raw_event_client(event: AstrMessageEvent) -> Any | None:
        client = getattr(event, "client", None)
        if client:
            return client
        return getattr(event, "bot", None)

    def _get_event_client(
        self, event: AstrMessageEvent, platform_id: str | None = None
    ) -> Any | None:
        client = self._get_raw_event_client(event)
        if client is not None and hasattr(client, "send_photo"):
            return client
        if platform_id:
            cached = self._platform_clients.get(platform_id)
            if cached is not None:
                return cached
        return None

    @staticmethod
    def _resolve_chat_target(
        event: AstrMessageEvent,
    ) -> tuple[int | str, int | None] | None:
        try:
            group_id = event.get_group_id()
        except Exception:
            group_id = ""

        if group_id:
            raw_target = str(group_id)
        else:
            try:
                raw_target = str(event.get_sender_id())
            except Exception:
                return None

        chat_part = raw_target
        thread_id: int | None = None
        if "#" in raw_target:
            chat_part, thread_part = raw_target.split("#", 1)
            try:
                thread_id = int(thread_part)
            except (TypeError, ValueError):
                thread_id = None

        try:
            chat_id: int | str = int(chat_part)
        except (TypeError, ValueError):
            chat_id = chat_part
        return chat_id, thread_id

    def _cleanup_expired_sessions(self) -> None:
        now = time.time()
        expired_tokens = [
            token
            for token, session in self._sessions.items()
            if now - session.created_at > self._SESSION_TTL_SECONDS
        ]
        for token in expired_tokens:
            self._sessions.pop(token, None)

        if len(self._sessions) <= self._MAX_SESSIONS:
            return

        ordered = sorted(self._sessions.items(), key=lambda item: item[1].created_at)
        overflow_count = len(self._sessions) - self._MAX_SESSIONS
        for token, _ in ordered[:overflow_count]:
            self._sessions.pop(token, None)

    @staticmethod
    def _is_photo_dimension_error(err: Exception) -> bool:
        message = str(err).lower()
        return "photo_invalid_dimensions" in message or "invalid dimensions" in message
