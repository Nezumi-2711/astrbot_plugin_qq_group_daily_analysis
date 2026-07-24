"""Router nền tảng cho chức năng xem trước template."""

from __future__ import annotations

from typing import Any


class TemplatePreviewRouter:
    """Điều phối handler xem trước template cho các nền tảng."""

    def __init__(self, handlers: list[Any] | None = None):
        self._handlers: list[Any] = handlers or []

    def add_handler(self, handler: Any) -> None:
        """Đăng ký một handler nền tảng."""
        self._handlers.append(handler)

    async def ensure_handlers_registered(self, context: Any) -> None:
        """Cho phép handler khởi tạo, chẳng hạn đăng ký callback."""
        for handler in self._handlers:
            register_func = getattr(
                handler, "ensure_callback_handlers_registered", None
            )
            if callable(register_func):
                await register_func(context)

    async def unregister_handlers(self) -> None:
        """Huỷ đăng ký tài nguyên của các handler."""
        for handler in self._handlers:
            unregister_func = getattr(handler, "unregister_callback_handlers", None)
            if callable(unregister_func):
                await unregister_func()

    async def handle_view_templates(
        self,
        event: Any,
        platform_id: str,
        available_templates: list[str],
    ) -> tuple[bool, list[Any]]:
        """
        Xử lý tương tác xem template.

        Returns:
            Tuple gồm trạng thái đã được handler tiếp nhận và danh sách kết quả
            tin nhắn cần trả về framework.
        """
        for handler in self._handlers:
            supports_func = getattr(handler, "supports", None)
            if not callable(supports_func) or not supports_func(event):
                continue

            handle_func = getattr(handler, "handle_view_templates", None)
            if not callable(handle_func):
                continue

            handled, results = await handle_func(
                event=event,
                platform_id=platform_id,
                available_templates=available_templates,
            )
            return handled, results

        return False, []
