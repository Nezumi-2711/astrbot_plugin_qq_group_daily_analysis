import base64
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ...shared.constants import PLUGIN_NAME
from ...shared.trace_context import TraceContext
from ...utils.logger import logger


class ReportDispatcher:
    """
    Bộ phân phối báo cáo, điều phối tạo báo cáo, chọn định dạng, gửi và fallback.
    """

    def __init__(
        self,
        config_manager,
        report_generator,
        message_sender,
    ):
        self.config_manager = config_manager
        self.report_generator = report_generator
        self.message_sender = message_sender
        self._html_render_func: Callable | None = None

    def set_html_render(self, render_func: Callable):
        """Thiết lập hàm render HTML được inject lúc runtime."""
        self._html_render_func = render_func

    def _is_qq_official(self, platform_id: str | None) -> bool:
        adapter = self.message_sender.bot_manager.get_adapter(platform_id)
        return bool(adapter and adapter.get_platform_name() == "qq_official")

    async def dispatch(
        self,
        group_id: str,
        analysis_result: dict[str, Any],
        platform_id: str | None = None,
    ):
        """
        Phân phối báo cáo phân tích.
        """
        trace_id = TraceContext.get()
        output_formats = self.config_manager.get_output_format()

        logger.info(
            f"[{trace_id}] Đang phân phối báo cáo cho nhóm {group_id} (định dạng: {', '.join(output_formats)})"
        )

        dispatch_map = {
            "image": self._dispatch_image,
            "html": self._dispatch_html,
            "text": self._dispatch_text,
        }
        for fmt in output_formats:
            handler = dispatch_map.get(fmt)
            if handler:
                await handler(group_id, analysis_result, platform_id)

        logger.info(f"[{trace_id}] Hoàn tất phân phối báo cáo cho nhóm {group_id}")

    async def _dispatch_image(
        self, group_id: str, analysis_result: dict[str, Any], platform_id: str | None
    ) -> bool:
        trace_id = TraceContext.get()
        # 1. Kiểm tra hàm render.
        if not self._html_render_func:
            logger.warning(
                f"[{trace_id}] Chưa thiết lập hàm render HTML, chuyển sang văn bản"
            )
            return await self._dispatch_text(group_id, analysis_result, platform_id)

        # 2. Tạo ảnh.
        image_url = None
        html_content = None
        try:
            # Callback lấy avatar kích thước nhỏ để tối ưu hiệu năng.
            async def avatar_url_getter(user_id: str):
                if not platform_id:
                    return None
                adapter = self.message_sender.bot_manager.get_adapter(platform_id)
                if adapter and hasattr(adapter, "get_user_avatar_url"):
                    return await adapter.get_user_avatar_url(user_id, size=40)
                return None

            image_url, html_content = await self.report_generator.generate_image_report(
                analysis_result,
                group_id,
                self._html_render_func,
                avatar_url_getter=avatar_url_getter,
                avatar_cache_namespace=platform_id,
                allow_alphanumeric_user_ids=self._is_qq_official(platform_id),
            )
        except Exception as e:
            logger.error(f"[{trace_id}] Tạo báo cáo ảnh thất bại: {e}")
            # image_url and html_content remain None

        # 4. Gửi ảnh.
        sent = False
        if image_url:
            caption = (
                TraceContext.make_report_caption()
                if self.config_manager.get_show_report_caption()
                else ""
            )
            sent = await self.message_sender.send_image_smart(
                group_id, image_url, caption, platform_id
            )

            # 5. Thử upload vào tệp/album nhóm và chỉ ghi log khi lỗi.
            # Nếu ảnh đã tạo thì luôn thử sao lưu dù gửi tin nhắn có thành công hay không.
            await self._try_upload_image(group_id, image_url, platform_id)

        if sent:
            return True

        # 6. Fallback cuối: gửi báo cáo văn bản nếu ảnh thất bại.
        logger.warning(
            f"[{trace_id}] Phân phối ảnh thất bại, chuyển sang báo cáo văn bản"
        )
        return await self._dispatch_text(group_id, analysis_result, platform_id)

    async def _dispatch_html(
        self, group_id: str, analysis_result: dict[str, Any], platform_id: str | None
    ) -> bool:
        trace_id = TraceContext.get()

        html_path = None
        try:

            async def avatar_url_getter(user_id: str):
                if not platform_id:
                    return None
                adapter = self.message_sender.bot_manager.get_adapter(platform_id)
                if adapter and hasattr(adapter, "get_user_avatar_url"):
                    return await adapter.get_user_avatar_url(user_id, size=40)
                return None

            html_path, json_path = await self.report_generator.generate_html_report(
                analysis_result,
                group_id,
                avatar_url_getter=avatar_url_getter,
                avatar_cache_namespace=platform_id,
                allow_alphanumeric_user_ids=self._is_qq_official(platform_id),
            )
        except Exception as e:
            logger.error(f"[{trace_id}] Tạo báo cáo HTML thất bại: {e}")

        if html_path:
            is_only_url = self.config_manager.get_html_only_url()
            base_url = self.config_manager.get_html_base_url()

            if is_only_url:
                if base_url and base_url.strip():
                    # Lấy thư mục đã cấu hình.
                    html_output_dir = self.config_manager.get_html_output_dir()

                    # Dùng thư mục mặc định nếu cấu hình rỗng.
                    if not html_output_dir:
                        from astrbot.api.star import StarTools

                        html_output_dir = os.path.join(
                            StarTools.get_data_dir(PLUGIN_NAME),
                            "self_hosted_html_reports",
                        )

                    # Tính đường dẫn tương đối và chuyển thành URL.
                    rel_path = os.path.relpath(html_path, html_output_dir)
                    url_path = rel_path.replace(os.sep, "/")
                    report_url = f"{base_url.rstrip('/')}/{url_path.lstrip('/')}"

                    sent = await self.message_sender.send_text(
                        group_id,
                        f"📊 Báo cáo phân tích nhóm hôm nay đã sẵn sàng:\n{report_url}",
                        platform_id,
                    )

                    if sent:
                        return True
                else:
                    logger.warning(
                        f"[{trace_id}] Nhóm {group_id} chỉ bật gửi liên kết nhưng chưa cấu hình html_base_url; chuyển sang gửi tệp HTML"
                    )

            caption = (
                self.report_generator.build_html_caption(html_path)
                if self.config_manager.get_show_report_caption()
                else ""
            )

            sent = await self.message_sender.send_file(
                group_id,
                html_path,
                caption=caption,
                platform_id=platform_id,
            )
            if sent:
                return True

        logger.warning(
            f"[{trace_id}] Phân phối HTML thất bại, chuyển sang báo cáo văn bản"
        )
        return await self._dispatch_text(group_id, analysis_result, platform_id)

    async def _dispatch_text(
        self, group_id: str, analysis_result: dict[str, Any], platform_id: str | None
    ) -> bool:
        """Phân phối báo cáo văn bản."""
        logger.info(f"[Bộ phân phối] Đang gửi báo cáo văn bản tới nhóm {group_id}")
        is_qq_official = self._is_qq_official(platform_id)
        fallback_report = None
        if is_qq_official:
            (
                text_report,
                fallback_report,
            ) = await self.report_generator.generate_qq_official_markdown_report(
                analysis_result, self._html_render_func
            )
        else:
            text_report = self.report_generator.generate_text_report(analysis_result)
        adapter = self.message_sender.bot_manager.get_adapter(platform_id)
        # Thử gửi báo cáo văn bản qua adapter.
        logger.info(
            f"[Bộ phân phối] Đang thử gửi báo cáo văn bản qua adapter, nhóm: {group_id}"
        )
        try:
            if adapter:
                if is_qq_official:
                    if await adapter.send_text_report(
                        group_id,
                        text_report,
                        fallback_content=fallback_report,
                    ):
                        return True
                elif await adapter.send_text_report(group_id, text_report):
                    return True
            return await self.message_sender.send_text(
                group_id,
                f"📊 Báo cáo phân tích nhóm hằng ngày:\n\n{text_report}",
                platform_id,
            )
        except Exception as e:
            logger.error(
                f"[Bộ phân phối] Gửi báo cáo văn bản thất bại, nhóm: {group_id}, lỗi: {e}"
            )
            return False

    # ================================================================
    # Upload báo cáo ảnh vào tệp/album nhóm, chỉ cho định dạng ảnh trên QQ.
    # ================================================================

    async def _try_upload_image(
        self,
        group_id: str,
        image_url: str,
        platform_id: str | None,
    ):
        """
        Thử upload báo cáo ảnh vào tệp và/hoặc album nhóm.

        Chỉ thực hiện khi bật cấu hình và nền tảng là OneBot; lỗi chỉ ghi log.
        """
        enable_file = self.config_manager.get_enable_group_file_upload()
        enable_album = self.config_manager.get_enable_group_album_upload()
        if not enable_file and not enable_album:
            return

        # Chỉ OneBot hỗ trợ.
        adapter = self._get_onebot_adapter(platform_id)
        if not adapter:
            return

        # Lưu ảnh thành tệp tạm.
        image_file = self._save_image_to_temp(image_url, group_id)
        if not image_file:
            return

        try:
            # Upload vào tệp nhóm.
            if enable_file:
                await self._do_upload_group_file(adapter, group_id, image_file)

            # Upload vào album nhóm.
            if enable_album:
                await self._do_upload_group_album(adapter, group_id, image_file)
        finally:
            try:
                os.remove(image_file)
            except OSError:
                pass

    async def _do_upload_group_file(self, adapter, group_id: str, file_path: str):
        """Upload tệp vào thư mục nhóm; lỗi chỉ ghi log."""
        try:
            folder_name = self.config_manager.get_group_file_folder()
            folder_id = None
            if folder_name:
                folder_id = await adapter.find_or_create_folder(group_id, folder_name)
            await adapter.upload_group_file_to_folder(
                group_id=group_id,
                file_path=file_path,
                folder_id=folder_id,
            )
        except Exception as e:
            logger.warning(f"Upload tệp nhóm thất bại (nhóm {group_id}): {e}")

    async def _do_upload_group_album(self, adapter, group_id: str, file_path: str):
        """Upload ảnh vào album nhóm; lỗi chỉ ghi log."""
        try:
            album_name = self.config_manager.get_group_album_name()
            strict_mode = self.config_manager.get_group_album_strict_mode()
            album_id = None

            if hasattr(adapter, "find_album_id"):
                if album_name:
                    album_id = await adapter.find_album_id(group_id, album_name)
                    if not album_id and strict_mode:
                        logger.info(
                            f"Đã bật chế độ album nghiêm ngặt: không tìm thấy album '{album_name}' trong nhóm {group_id}, dừng upload"
                        )
                        return
                elif strict_mode:
                    logger.info(
                        f"Đã bật chế độ album nghiêm ngặt nhưng chưa đặt tên album đích; dừng để tránh thao tác album mặc định của nhóm {group_id}"
                    )
                    return

            await adapter.upload_group_album(
                group_id,
                file_path,
                album_id=album_id,
                album_name=album_name,
                strict_mode=strict_mode,
            )
        except Exception as e:
            logger.warning(f"Upload album nhóm thất bại (nhóm {group_id}): {e}")

    def _save_image_to_temp(self, image_url: str, group_id: str) -> str | None:
        """Lưu ảnh Base64 thành PNG tạm và trả về đường dẫn hoặc None."""
        try:
            image_data = None
            if image_url.startswith("base64://"):
                image_data = base64.b64decode(image_url[len("base64://") :])
            elif image_url.startswith("data:"):
                parts = image_url.split(",", 1)
                if len(parts) == 2:
                    image_data = base64.b64decode(parts[1])
            elif os.path.isfile(image_url):
                return os.path.abspath(image_url)
            elif image_url.startswith("file:///"):
                p = image_url[len("file:///") :]
                if os.path.isfile(p):
                    return os.path.abspath(p)

            if not image_data:
                return None

            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(
                tempfile.gettempdir(),
                f"bao_cao_phan_tich_nhom_{group_id}_{date_str}.png",
            )
            with open(path, "wb") as f:
                f.write(image_data)
            return path
        except Exception as e:
            logger.debug(f"Lưu ảnh vào tệp tạm thất bại: {e}")
            return None

    def _get_onebot_adapter(self, platform_id: str | None):
        """Lấy adapter OneBot hoặc None cho nền tảng khác."""
        if not platform_id:
            return None
        adapter = self.message_sender.bot_manager.get_adapter(platform_id)
        if adapter and hasattr(adapter, "upload_group_file_to_folder"):
            return adapter
        return None
