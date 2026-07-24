"""
Plugin phân tích hoạt động nhóm hằng ngày.

Tạo báo cáo từ lịch sử trò chuyện, gồm tóm tắt chủ đề, hồ sơ thành viên và
thống kê. Phiên bản module hoá hỗ trợ đa nền tảng.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api import logger as astrbot_logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.star import Context, Star, StarTools

# File is only available via astrbot.core (internal API — may change).
from astrbot.core.message.components import File

from .src.application.commands.template_command_service import (
    TemplateCommandService,
)
from .src.application.services.analysis_application_service import (
    AnalysisApplicationService,
    DuplicateGroupTaskError,
)
from .src.application.services.message_processing_service import (
    MessageProcessingService,
)
from .src.domain.services.analysis_domain_service import AnalysisDomainService
from .src.domain.services.incremental_merge_service import IncrementalMergeService
from .src.domain.services.statistics_service import StatisticsService
from .src.infrastructure.analysis.llm_analyzer import LLMAnalyzer
from .src.infrastructure.config.config_manager import ConfigManager
from .src.infrastructure.messaging.message_sender import MessageSender
from .src.infrastructure.persistence.history_manager import HistoryManager
from .src.infrastructure.persistence.incremental_store import IncrementalStore
from .src.infrastructure.persistence.platform_group_registry import (
    PlatformGroupRegistry,
)
from .src.infrastructure.platform.bot_manager import BotManager
from .src.infrastructure.platform.template_preview import (
    TelegramTemplatePreviewHandler,
    TemplatePreviewRouter,
)
from .src.infrastructure.reporting.generators import ReportGenerator
from .src.infrastructure.scheduler.auto_scheduler import AutoScheduler
from .src.infrastructure.visualization.activity_charts import ActivityVisualizer
from .src.shared.constants import PLUGIN_NAME
from .src.shared.trace_context import TraceContext, TraceLogFilter
from .src.utils.logger import logger
from .src.utils.resilience import GlobalRateLimiter


class GroupDailyAnalysis(Star):
    """Lớp plugin phân tích nhóm chính."""

    # ── Khai báo kiểu tường minh, được khởi tạo trong __init__ ──
    config: AstrBotConfig
    config_manager: ConfigManager
    bot_manager: BotManager
    history_manager: HistoryManager
    report_generator: ReportGenerator
    html_render: Callable
    platform_group_registry: PlatformGroupRegistry
    statistics_service: StatisticsService
    analysis_domain_service: AnalysisDomainService
    llm_analyzer: LLMAnalyzer
    incremental_store: IncrementalStore
    incremental_merge_service: IncrementalMergeService
    analysis_service: AnalysisApplicationService
    message_processing_service: MessageProcessingService
    template_command_service: TemplateCommandService
    telegram_template_preview_handler: TelegramTemplatePreviewHandler
    template_preview_router: TemplatePreviewRouter
    auto_scheduler: AutoScheduler
    message_sender: MessageSender

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 1. Tầng infrastructure.
        self.config_manager = ConfigManager(config)
        self.bot_manager = BotManager(self.config_manager)
        self.bot_manager.set_context(context)
        self.bot_manager.set_plugin_instance(self)
        self.history_manager = HistoryManager(self)

        plugin_data_dir = StarTools.get_data_dir(PLUGIN_NAME)

        self.report_generator = ReportGenerator(self.config_manager, plugin_data_dir)

        # Registry Telegram ở tầng persistence.
        self.platform_group_registry = PlatformGroupRegistry(self)

        # 2. Tầng domain.
        activity_visualizer = ActivityVisualizer()
        self.statistics_service = StatisticsService(activity_visualizer)
        self.analysis_domain_service = AnalysisDomainService()

        # 3. Lõi phân tích, cầu nối LLM.
        self.llm_analyzer = LLMAnalyzer(context, self.config_manager)

        # 4. Thành phần phân tích gia tăng.
        self.incremental_store = IncrementalStore(self)
        self.incremental_merge_service = IncrementalMergeService()

        # 5. Tầng application.
        self.analysis_service = AnalysisApplicationService(
            self.config_manager,
            self.bot_manager,
            self.history_manager,
            self.report_generator,
            self.llm_analyzer,
            self.statistics_service,
            self.analysis_domain_service,
            incremental_store=self.incremental_store,
            incremental_merge_service=self.incremental_merge_service,
        )

        # Dịch vụ xử lý tin nhắn.
        self.message_processing_service = MessageProcessingService(
            context, self.platform_group_registry
        )
        self.template_command_service = TemplateCommandService(
            plugin_root=os.path.dirname(__file__)
        )
        self.telegram_template_preview_handler = TelegramTemplatePreviewHandler(
            config_manager=self.config_manager,
            template_service=self.template_command_service,
        )
        self.template_preview_router = TemplatePreviewRouter(
            handlers=[self.telegram_template_preview_handler]
        )

        # Lập lịch và gửi.
        self.message_sender = MessageSender(self.bot_manager, self.config_manager)
        self.auto_scheduler = AutoScheduler(
            self.config_manager,
            self.analysis_service,
            self.bot_manager,
            self.report_generator,
            self.html_render,
            plugin_instance=self,
        )

        # Đồng bộ cấu hình bộ giới hạn toàn cục.
        GlobalRateLimiter.get_instance(self.config_manager.get_llm_max_concurrent())

        self._initialized = False
        self._terminating = False  # Cờ vòng đời.
        self._init_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

        # Đăng ký tác vụ bất đồng bộ để xử lý reload plugin.
        try:
            loop = asyncio.get_running_loop()
            self._init_task = loop.create_task(
                self._run_initialization("Plugin Reload/Init")
            )
            self._background_tasks.add(self._init_task)
            self._init_task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            self._init_task = None

    # Cache orchestrator đã chuyển vào application service hoặc tạm bỏ để đơn giản.
    # Nếu cần cache hiệu năng cao, AnalysisApplicationService có thể quản lý nội bộ.

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        """Khởi tạo sau khi nền tảng tải xong."""
        await self._run_initialization("Platform Loaded")

    async def _run_initialization(self, source: str):
        """Logic khởi tạo thống nhất."""
        async with self._init_lock:
            # Bỏ qua nếu đã phát hiện nền tảng và không phải trigger Platform Loaded.
            if (
                self._initialized
                and self.bot_manager
                and self.bot_manager.get_platform_count() > 0
                and source != "Platform Loaded"
            ):
                return

            # Chờ để context, môi trường và platform manager ổn định.
            await asyncio.sleep(5)

            # Thoát nếu plugin đã bị gỡ trong thời gian chờ.
            if not self.bot_manager:
                return

            try:
                # Đăng ký bộ lọc TraceID.
                trace_filter = TraceLogFilter()
                if not any(
                    isinstance(f, TraceLogFilter) for f in astrbot_logger.filters
                ):
                    astrbot_logger.addFilter(trace_filter)
                    astrbot_logger.info("[Trace] Đã bật theo dõi log bằng TraceID")

                logger.info(f"Đang khởi tạo plugin (nguồn: {source})...")

                # 0. Tự nâng cấp prompt cũ từ str.format sang string.Template.
                try:
                    self.config_manager.upgrade_prompt_templates()
                except Exception as e:
                    logger.warning(f"Tự nâng cấp template prompt thất bại: {e}")

                # 1. Thử phát hiện instance bot.
                await self.bot_manager.initialize_from_config()

                # 2. Đăng ký router preview.
                if self.template_preview_router:
                    await self.template_preview_router.ensure_handlers_registered(
                        self.context
                    )

                # 3. Đăng ký tác vụ phân tích định kỳ.
                if self.auto_scheduler:
                    self.auto_scheduler.schedule_jobs(self.context)

                self._initialized = True
                self._discovery_run = True
                logger.info(f"Hoàn tất đăng ký tác vụ plugin (nguồn: {source})")

            except Exception as e:
                logger.error(f"Khởi tạo plugin thất bại: {e}", exc_info=True)

    async def terminate(self):
        """Dọn tài nguyên khi plugin bị gỡ hoặc vô hiệu hoá."""
        if self._terminating:
            return
        self._terminating = True

        try:
            logger.info("Bắt đầu dọn tài nguyên plugin phân tích nhóm...")

            # 1. Dừng mọi tác vụ đang chạy.
            if self._background_tasks:
                logger.info(
                    f"Đang huỷ {len(self._background_tasks)} tác vụ đang chạy..."
                )
                for task in self._background_tasks:
                    if not task.done():
                        task.cancel()

                # Chờ tác vụ kết thúc với thời gian gia hạn 3 giây.
                try:
                    await asyncio.wait(list(self._background_tasks), timeout=3.0)
                except Exception:
                    pass
                self._background_tasks.clear()

            # 2. Dừng các thành phần: scheduler trước, dịch vụ tầng dưới sau.
            if self.auto_scheduler:
                logger.debug("Đang dừng bộ lập lịch tự động...")
                self.auto_scheduler.unschedule_jobs(self.context)

            if self.template_preview_router:
                await self.template_preview_router.unregister_handlers()

            if self.report_generator:
                await self.report_generator.close()

            # 3. Chỉ dọn tham chiếu sau khi mọi tác vụ đã kết thúc.
            # Giữ tham chiếu để GC thu hồi tự nhiên, tránh race với tác vụ bất đồng bộ (#125).
            logger.info("Hoàn tất dọn tài nguyên plugin phân tích nhóm")

        except Exception as e:
            logger.error(f"Dọn tài nguyên plugin thất bại: {e}")

    # ==================== Bộ chặn tin nhắn Telegram ====================

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.TELEGRAM)
    async def intercept_telegram_messages(self, event: AstrMessageEvent):
        """
        Chặn tin nhắn nhóm Telegram và lưu vào cơ sở dữ liệu.

        Uỷ quyền xử lý cho MessageProcessingService.
        """
        try:
            await self.message_processing_service.process_message(event)
        except (ValueError, RuntimeError) as e:
            logger.warning(f"[Telegram] Lưu tin nhắn thất bại: {e}")
        except Exception as e:
            logger.error(f"[Telegram] Lỗi lưu tin nhắn: {e}", exc_info=True)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(
        filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
    )
    async def intercept_qq_official_messages(self, event: AstrMessageEvent):
        """Cache tin nhắn nhóm QQ Official; không xử lý tin nhắn kênh."""
        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict):
            author = raw_message.get("author") or {}
            group_openid = str(raw_message.get("group_openid", "") or "").strip()
            member_openid = str(
                author.get("member_openid", "") if isinstance(author, dict) else ""
            ).strip()
        else:
            author = getattr(raw_message, "author", None)
            group_openid = str(getattr(raw_message, "group_openid", "") or "").strip()
            member_openid = str(getattr(author, "member_openid", "") or "").strip()
        if not group_openid or not member_openid:
            return

        try:
            await self.message_processing_service.process_message(event)
        except (ValueError, RuntimeError) as e:
            logger.warning(f"[QQOfficial] Lưu tin nhắn thất bại: {e}")
        except Exception as e:
            logger.error(f"[QQOfficial] Lỗi lưu tin nhắn: {e}", exc_info=True)

    async def get_telegram_seen_group_ids(
        self, platform_id: str | None = None
    ) -> list[str]:
        """Đọc nhóm/chủ đề Telegram đã thấy cho scheduler fallback."""
        return await self.platform_group_registry.get_all_group_ids(platform_id)

    async def get_seen_group_ids(self, platform_id: str | None = None) -> list[str]:
        """Đọc các nhóm đã thấy trên mọi nền tảng hướng sự kiện."""
        return await self.platform_group_registry.get_all_group_ids(platform_id)

    def _get_group_id_from_event(self, event: AstrMessageEvent) -> str | None:
        """Lấy an toàn ID nhóm từ sự kiện tin nhắn."""
        # Giữ helper này vì nhiều command khác vẫn dùng.
        try:
            group_id = event.get_group_id()
            return group_id if group_id else None
        except Exception:
            return None

    def _get_platform_id_from_event(self, event: AstrMessageEvent) -> str:
        """Lấy ID nền tảng duy nhất từ sự kiện tin nhắn."""
        # Giữ helper này vì nhiều command khác vẫn dùng.
        try:
            return event.get_platform_id()
        except Exception:
            # Fallback: lấy từ metadata.
            if (
                hasattr(event, "platform_meta")
                and event.platform_meta
                and hasattr(event.platform_meta, "id")
            ):
                return event.platform_meta.id
            return "default"

    # ================================================================
    # Upload báo cáo ảnh vào tệp/album nhóm, chỉ cho định dạng ảnh trên QQ.
    # ================================================================

    async def _try_upload_image(self, group_id: str, image_url: str, platform_id: str):
        """
        Thử upload báo cáo ảnh vào tệp và/hoặc album nhóm; lỗi chỉ ghi log.
        """
        import base64
        import re
        import tempfile
        from datetime import datetime

        enable_file = self.config_manager.get_enable_group_file_upload()
        enable_album = self.config_manager.get_enable_group_album_upload()
        if not enable_file and not enable_album:
            return

        adapter = self.bot_manager.get_adapter(platform_id)
        if not adapter or not hasattr(adapter, "upload_group_file_to_folder"):
            return

        # 1. Tạo tên tệp thân thiện hơn.
        now = datetime.now()
        timestamp = now.strftime("%H%M")
        date_str = now.strftime("%Y-%m-%d")

        # Tên cơ sở và phần mở rộng mặc định.
        ext = (
            ".jpg"
            if (".jpg" in image_url.lower() or ".jpeg" in image_url.lower())
            else ".png"
        )
        nice_filename = f"bao_cao_phan_tich_nhom_{group_id}_{date_str}_{timestamp}{ext}"

        try:
            # Thử lấy tên nhóm qua adapter để tên tệp dễ nhận diện hơn.
            group_info = await adapter.get_group_info(group_id)
            if group_info and group_info.group_name:
                # Lọc ký tự không hợp lệ trong tên tệp: \ / : * ? " < > |
                safe_name = re.sub(r'[\\/:*?"<>|]', "", group_info.group_name).strip()
                if safe_name:
                    nice_filename = f"bao_cao_phan_tich_nhom_{safe_name}_{date_str}_{timestamp}{ext}"
        except Exception:
            pass

        # 2. Chuẩn bị nội dung dưới dạng tệp hoặc dữ liệu.
        image_file = None
        created_temp = False
        MAX_PAYLOAD_SIZE = 20 * 1024 * 1024  # Giới hạn 20 MB.

        try:
            data = None
            if image_url.startswith("base64://"):
                base64_str = image_url[len("base64://") :]
                if len(base64_str) * 3 / 4 > MAX_PAYLOAD_SIZE:
                    logger.warning("Upload ảnh thất bại: payload Base64 quá lớn")
                    return
                data = base64.b64decode(base64_str)
            elif image_url.startswith("data:"):
                parts = image_url.split(",", 1)
                if len(parts) == 2:
                    if len(parts[1]) * 3 / 4 > MAX_PAYLOAD_SIZE:
                        logger.warning("Upload ảnh thất bại: payload Data URI quá lớn")
                        return
                    data = base64.b64decode(parts[1])
            elif os.path.isfile(image_url):
                image_file = os.path.abspath(image_url)

            if data and not image_file:
                # Dùng tempfile tạo hậu tố duy nhất để tránh xung đột đồng thời.
                fd, image_file = tempfile.mkstemp(suffix=ext, prefix="group_report_")
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    created_temp = True
                except Exception:
                    os.close(fd)
                    raise

            if not image_file:
                return

            # 3. Upload vào tệp nhóm.
            if enable_file:
                try:
                    folder_name = self.config_manager.get_group_file_folder()
                    folder_id = None
                    if folder_name:
                        folder_id = await adapter.find_or_create_folder(  # type: ignore[attr-defined]
                            group_id, folder_name
                        )
                    await adapter.upload_group_file_to_folder(  # type: ignore[attr-defined]
                        group_id=group_id,
                        file_path=image_file,
                        folder_id=folder_id,
                        filename=nice_filename,  # Truyền tường minh tên tệp thân thiện.
                    )
                except Exception as e:
                    logger.warning(f"Upload tệp nhóm thất bại (nhóm {group_id}): {e}")

            if enable_album and hasattr(adapter, "upload_group_album"):
                try:
                    album_name = self.config_manager.get_group_album_name()
                    strict_mode = self.config_manager.get_group_album_strict_mode()
                    album_id = None
                    if hasattr(adapter, "find_album_id"):
                        if album_name:
                            album_id = await adapter.find_album_id(group_id, album_name)  # type: ignore[attr-defined]
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
                    await adapter.upload_group_album(  # type: ignore[attr-defined]
                        group_id,
                        image_file,
                        album_id=album_id,
                        album_name=album_name,
                        strict_mode=strict_mode,
                    )
                except Exception as e:
                    logger.warning(f"Upload album nhóm thất bại (nhóm {group_id}): {e}")
        except Exception as e:
            logger.warning(f"Lỗi xử lý upload ảnh: {e}")
        finally:
            if created_temp and image_file and os.path.exists(image_file):
                try:
                    os.remove(image_file)
                except OSError:
                    pass

    @filter.command("phantichnhom", alias={"group_analysis"})
    @filter.permission_type(PermissionType.ADMIN)
    async def analyze_group_daily(
        self, event: AstrMessageEvent, days: int | None = None
    ):
        """
        Phân tích hoạt động nhóm hằng ngày trên nhiều nền tảng.
        Cách dùng: /phantichnhom [số ngày]
        """
        if self._terminating:
            return

        current_task = asyncio.current_task()
        if current_task:
            self._background_tasks.add(current_task)

        try:
            # Chặn cả LLM mặc định lẫn các handler tiếp theo xử lý lại slash command.
            event.should_call_llm(True)
            event.stop_event()
            group_id = self._get_group_id_from_event(event)
            platform_id = self._get_platform_id_from_event(event)

            if not group_id:
                yield event.plain_result("❌ Vui lòng sử dụng lệnh này trong nhóm chat")
                return

            # Cập nhật instance bot.
            self.bot_manager.update_from_event(event)

            # Ưu tiên UMO để kiểm tra quyền và tương thích whitelist UMO.
            check_target = getattr(event, "unified_msg_origin", None)
            if not check_target:
                check_target = f"{platform_id}:GroupMessage:{group_id}"

            if not self.config_manager.is_group_allowed(check_target):
                # Fallback checks (simple ID) are handled inside is_group_allowed logic if list item has no colon
                # But if list item HAS colon, we need precise match.
                # If prompt fails, try simple ID as fallback for permissive cases?
                # No, config_manager.is_group_allowed already handles simple ID matching if whitelist item is simple ID.
                yield event.plain_result(
                    "❌ Nhóm này chưa bật tính năng phân tích hàng ngày"
                )
                return

            # Lấy tên nhóm để tạo TraceID có ngữ nghĩa.
            group_name = ""
            try:
                adapter = self.bot_manager.get_adapter(platform_id)
                if adapter:
                    info = await adapter.get_group_info(group_id)
                    if info and info.group_name:
                        group_name = info.group_name
            except Exception:
                pass

            # Thiết lập TraceID theo dạng manual_tên_nhóm_HHmm.
            trace_id = TraceContext.generate(
                prefix="manual", group_name=group_name or group_id
            )
            TraceContext.set(trace_id)

            # Reaction hoặc thông báo văn bản, chọn theo cấu hình.
            adapter = self.bot_manager.get_adapter(platform_id)
            orig_msg_id = getattr(event.message_obj, "message_id", None)
            adapter_platform_name = (
                (adapter.get_platform_name() if adapter else "").strip().lower()
            )
            # API v2 của QQ Official không hỗ trợ reaction plugin đang dùng,
            # nên luôn dùng thông báo tiến độ dạng văn bản.
            use_text_reply = (
                adapter_platform_name in {"qq_official", "qq_official_webhook"}
                or self.config_manager.get_enable_analysis_reply()
            )

            if use_text_reply:
                yield event.plain_result(
                    "🔍 Đang khởi động phân tích và lấy các tin nhắn gần đây..."
                )
            elif adapter and orig_msg_id:
                await adapter.set_reaction(
                    event.get_group_id(), orig_msg_id, "analysis_started"
                )

            # Gọi application service theo DDD.
            result = await self.analysis_service.execute_daily_analysis(
                group_id=group_id, platform_id=platform_id, manual=True, days=days
            )

            if not result.get("success"):
                reason = result.get("reason")
                if reason == "no_messages":
                    yield event.plain_result(
                        "❌ Không tìm thấy đủ dữ liệu chat trong nhóm"
                    )
                elif reason == "muted":
                    logger.warning(
                        f"Nhóm {group_id} đã tắt chat toàn nhóm hoặc tắt quyền bot; bỏ qua phản hồi để tránh lỗi gửi"
                    )
                else:
                    yield event.plain_result(
                        "❌ Phân tích thất bại, không rõ nguyên nhân"
                    )
                return

            if not use_text_reply and adapter and orig_msg_id:
                await adapter.set_reaction(
                    event.get_group_id(), orig_msg_id, "analysis_done"
                )

            async for res in self._send_analysis_report(event, result):
                yield res

        except DuplicateGroupTaskError:
            yield event.plain_result(
                "📊 Phân tích cho nhóm này đang chạy, vui lòng thử lại sau nhé~"
            )
        except asyncio.CancelledError:
            logger.info("Tác vụ phân tích nhóm đã bị huỷ do plugin reload hoặc bị gỡ")
        except Exception as e:
            logger.error(f"Phân tích nhóm thất bại: {e}", exc_info=True)
            yield event.plain_result(
                "❌ Phân tích thất bại. Vui lòng kiểm tra kết nối mạng, "
                "cấu hình LLM hoặc liên hệ quản trị viên"
            )
        finally:
            if current_task:
                self._background_tasks.discard(current_task)

    async def _send_analysis_report(
        self, event: AstrMessageEvent, result: dict
    ) -> AsyncGenerator:
        """Render và gửi kết quả phân tích."""
        if self._terminating or not self.config_manager:
            logger.warning("Plugin đang đóng, dừng gửi báo cáo")
            return

        group_id = result["group_id"]
        platform_id = result["platform_id"]
        analysis_result = result["analysis_result"]
        adapter = result["adapter"]
        output_format = self.config_manager.get_output_format()[0]
        is_qq_official = adapter.get_platform_name() == "qq_official"

        # Định nghĩa callback truy xuất dữ liệu.
        async def avatar_url_getter(user_id: str) -> str | None:
            return await adapter.get_user_avatar_url(user_id)

        async def nickname_getter(user_id: str) -> str | None:
            try:
                member = await adapter.get_member_info(group_id, user_id)
                if member:
                    return member.card or member.nickname
            except Exception:
                pass
            return None

        if output_format == "image":
            image_url, html_content = await self.report_generator.generate_image_report(
                analysis_result,
                group_id,
                self.html_render,
                avatar_url_getter=avatar_url_getter,
                nickname_getter=nickname_getter,
                avatar_cache_namespace=platform_id,
                allow_alphanumeric_user_ids=is_qq_official,
            )

            if image_url:
                caption = (
                    TraceContext.make_report_caption()
                    if self.config_manager.get_show_report_caption()
                    else ""
                )
                sent = await adapter.send_image(group_id, image_url, caption=caption)
                if sent:
                    await self._try_upload_image(group_id, image_url, platform_id)
                    return  # Gửi thành công.

            # Chuyển thẳng sang văn bản nếu tạo hoặc gửi ảnh thất bại.
            logger.warning(
                f"Gửi báo cáo ảnh thất bại, đang gửi fallback văn bản cho nhóm {group_id}"
            )
            await self._send_text_reports(
                group_id, analysis_result, is_qq_official, adapter
            )
            return

        elif output_format == "html":
            html_path, json_path = await self.report_generator.generate_html_report(
                analysis_result,
                group_id,
                avatar_url_getter=avatar_url_getter,
                nickname_getter=nickname_getter,
                avatar_cache_namespace=platform_id,
                allow_alphanumeric_user_ids=is_qq_official,
            )
            if html_path:
                is_only_url = self.config_manager.get_html_only_url()
                base_url = self.config_manager.get_html_base_url()

                if is_only_url:
                    if base_url and base_url.strip():
                        # Lấy thư mục output trong cấu hình.
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

                        yield event.plain_result(
                            f"📊 Báo cáo phân tích nhóm hôm nay đã sẵn sàng:\n{report_url}"
                        )
                        return  # Đã gửi liên kết, không gửi tệp nữa.
                    else:
                        logger.warning(
                            f"Nhóm {group_id} được kích hoạt thủ công và chỉ bật gửi liên kết nhưng chưa cấu hình html_base_url; chuyển sang gửi tệp"
                        )

                caption = self.report_generator.build_html_caption(html_path)

                # Gửi tệp HTML.
                sender = getattr(self, "message_sender", None)
                if sender:
                    sent = await sender.send_file(
                        group_id,
                        html_path,
                        caption=caption,
                        platform_id=platform_id,
                    )
                else:
                    sent = await adapter.send_file(group_id, html_path)
                    if sent and caption:
                        await adapter.send_text(group_id, caption)

                if not sent:
                    yield event.chain_result(
                        [File(name=Path(html_path).name, file=html_path)]
                    )

                    if caption:
                        yield event.plain_result(caption)
            else:
                yield event.plain_result("⚠️ Tạo báo cáo HTML thất bại.")

        else:
            await self._send_text_reports(
                group_id, analysis_result, is_qq_official, adapter
            )

    async def _generate_text_reports(
        self, analysis_result: dict, use_qq_official_markdown: bool
    ) -> tuple[str, str | None]:
        """Generate text or QQ-official-markdown reports."""
        if use_qq_official_markdown:
            return await self.report_generator.generate_qq_official_markdown_report(
                analysis_result, self.html_render
            )
        return self.report_generator.generate_text_report(analysis_result), None

    async def _send_text_reports(
        self,
        group_id: str,
        analysis_result: dict,
        use_qq_official_markdown: bool,
        adapter,
    ) -> bool:
        """Send text reports via platform adapter."""
        tr, fr = await self._generate_text_reports(
            analysis_result, use_qq_official_markdown
        )
        if use_qq_official_markdown:
            return await adapter.send_text_report(group_id, tr, fallback_content=fr)
        return await adapter.send_text_report(group_id, tr)

    @filter.command("dinhdang", alias={"set_format"})
    @filter.permission_type(PermissionType.ADMIN)
    async def set_output_format(self, event: AstrMessageEvent, format_input: str = ""):
        """
        Thiết lập định dạng báo cáo trên nhiều nền tảng.
        Cách dùng: /dinhdang [tên hoặc số thứ tự], có thể dùng ``image,html``.
        """
        # Plugin xử lý command, tắt fallback LLM mặc định.
        event.should_call_llm(True)

        available_formats = ["image", "text", "html"]
        format_display_names = {
            "image": "Định dạng ảnh (mặc định)",
            "text": "Định dạng văn bản",
            "html": "Trang web HTML tương tác",
        }

        if not format_input:
            current = ", ".join(self.config_manager.get_output_format())
            format_list_str = "\n".join(
                [
                    f"【{i}】{f} - {format_display_names[f]}"
                    for i, f in enumerate(available_formats, start=1)
                ]
            )
            yield event.plain_result(f"""📊 Định dạng đầu ra hiện tại: {current}

Các định dạng khả dụng:
{format_list_str}

Cách dùng: /dinhdang [tên hoặc số thứ tự], ví dụ: /dinhdang image,html""")
            return

        target_format = None
        # Thử chọn theo số thứ tự.
        if format_input.isdigit():
            idx = int(format_input) - 1
            if 0 <= idx < len(available_formats):
                target_format = available_formats[idx]

        # Thử chọn theo tên.
        if not target_format:
            input_lower = format_input.lower()
            if input_lower in available_formats:
                target_format = input_lower

        # Hỗ trợ nhiều định dạng phân tách bằng dấu phẩy.
        if not target_format:
            parts = [f.strip() for f in format_input.replace("，", ",").split(",")]
            if all(p in available_formats for p in parts) and len(parts) > 1:
                try:
                    self.config_manager.set_output_format(parts)
                    yield event.plain_result(
                        f"✅ Định dạng đầu ra đã đặt thành: {', '.join(parts)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Cài đặt nhiều định dạng thất bại: {e}", exc_info=True
                    )
                    yield event.plain_result("❌ Cài đặt định dạng thất bại")
                return

        if not target_format:
            yield event.plain_result(
                f"❌ Định dạng '{format_input}' không hợp lệ. Có sẵn: "
                f"{', '.join(available_formats)} hoặc số thứ tự "
                f"1-{len(available_formats)}"
            )
            return

        try:
            self.config_manager.set_output_format(target_format)  # type: ignore[arg-type]
            yield event.plain_result(
                f"✅ Định dạng đầu ra đã đặt thành: {target_format}"
            )
        except Exception as e:
            logger.error(f"Cài đặt định dạng thất bại: {e}", exc_info=True)
            yield event.plain_result("❌ Cài đặt định dạng thất bại")

    @filter.command("maubc", alias={"set_template"})
    @filter.permission_type(PermissionType.ADMIN)
    async def set_report_template(
        self, event: AstrMessageEvent, template_input: str = ""
    ):
        """
        Thiết lập mẫu báo cáo trên nhiều nền tảng.
        Cách dùng: /maubc [tên mẫu hoặc số thứ tự].
        """
        # Plugin xử lý command, tắt fallback LLM mặc định.
        event.should_call_llm(True)

        available_templates = (
            await self.template_command_service.list_available_templates()
        )

        if not template_input:
            current_template = self.config_manager.get_report_template()
            template_list_str = "\n".join(
                [f"【{i}】{t}" for i, t in enumerate(available_templates, start=1)]
            )
            yield event.plain_result(f"""🎨 Mẫu báo cáo hiện tại: {current_template}

Các mẫu khả dụng:
{template_list_str}

Cách dùng: /maubc [tên mẫu hoặc số thứ tự]
💡 Sử dụng /xemmau để xem ảnh xem trước""")
            return

        template_name, parse_error = self.template_command_service.parse_template_input(
            template_input, available_templates
        )
        if parse_error:
            yield event.plain_result(parse_error)
            return

        if not template_name:
            yield event.plain_result(
                f"❌ Không thể phân tích đầu vào mẫu: {template_input}"
            )
            return

        if not await self.template_command_service.template_exists(template_name):
            yield event.plain_result(f"❌ Mẫu '{template_name}' không tồn tại")
            return

        self.config_manager.set_report_template(template_name)
        yield event.plain_result(f"✅ Mẫu báo cáo đã đặt thành: {template_name}")

    @filter.command("xemmau", alias={"view_templates"})
    @filter.permission_type(PermissionType.ADMIN)
    async def view_templates(self, event: AstrMessageEvent):
        """
        Xem mọi mẫu báo cáo khả dụng và ảnh preview trên nhiều nền tảng.
        Cách dùng: /xemmau.
        """
        # Plugin xử lý command, tắt fallback LLM mặc định.
        event.should_call_llm(True)

        available_templates = (
            await self.template_command_service.list_available_templates()
        )

        if not available_templates:
            yield event.plain_result("❌ Không tìm thấy mẫu báo cáo nào khả dụng")
            return

        platform_id = self._get_platform_id_from_event(event)
        await self.template_preview_router.ensure_handlers_registered(self.context)
        (
            handled,
            handler_results,
        ) = await self.template_preview_router.handle_view_templates(
            event=event,
            platform_id=platform_id,
            available_templates=available_templates,
        )
        if handled:
            for result in handler_results:
                yield result
            return

        current_template = self.config_manager.get_report_template()
        bot_id = event.get_self_id()
        preview_nodes = self.template_command_service.build_template_preview_nodes(
            available_templates=available_templates,
            current_template=current_template,
            bot_id=bot_id,
        )
        yield event.chain_result([preview_nodes])

    @filter.command("caidat", alias={"analysis_settings"})
    @filter.permission_type(PermissionType.ADMIN)
    async def analysis_settings(self, event: AstrMessageEvent, action: str = "status"):
        """
        Quản lý cài đặt phân tích trên nhiều nền tảng.

        Cách dùng: /caidat [enable|disable|status|reload|test|filter_bot|incremental_debug].
        ``filter_bot`` chuyển chế độ lọc tin bot; ``incremental_debug`` chuyển
        chế độ gửi ngay báo cáo gia tăng để debug.
        """
        group_id = self._get_group_id_from_event(event)

        if not group_id:
            yield event.plain_result("❌ Vui lòng sử dụng lệnh này trong nhóm chat")
            return

        if action == "enable":
            async for result in self._handle_settings_enable(event, group_id):
                yield result
        elif action == "disable":
            async for result in self._handle_settings_disable(event, group_id):
                yield result

        elif action == "reload":
            self.auto_scheduler.schedule_jobs(self.context)
            yield event.plain_result(
                "✅ Đã tải lại cấu hình và khởi động lại tác vụ định kỳ"
            )

        elif action == "test":
            check_target = getattr(event, "unified_msg_origin", None)
            if not check_target:
                check_target = (
                    f"{self._get_platform_id_from_event(event)}:GroupMessage:{group_id}"
                )

            if not self.config_manager.is_group_allowed(check_target):
                yield event.plain_result(
                    "❌ Vui lòng bật tính năng phân tích cho nhóm này trước"
                )
                return

            yield event.plain_result("🧪 Đang kiểm tra tính năng phân tích tự động...")

            # Cập nhật instance bot để kiểm tra.
            self.bot_manager.update_from_event(event)

            try:
                await self.auto_scheduler._perform_auto_analysis_for_group(group_id)
                yield event.plain_result(
                    "✅ Kiểm tra phân tích tự động hoàn tất, vui lòng xem tin nhắn nhóm"
                )
            except DuplicateGroupTaskError:
                yield event.plain_result(
                    "📊 Phân tích cho nhóm này đang chạy, vui lòng thử lại sau nhé~"
                )
            except Exception as e:
                logger.error(f"Kiểm tra phân tích tự động thất bại: {e}", exc_info=True)
                yield event.plain_result(
                    "❌ Kiểm tra phân tích tự động thất bại. Vui lòng kiểm tra "
                    "cấu hình và nhật ký hệ thống"
                )

        elif action == "incremental_debug":
            current_state = self.config_manager.get_incremental_report_immediately()
            new_state = not current_state
            self.config_manager.set_incremental_report_immediately(new_state)
            status_text = "Đã bật" if new_state else "Đã tắt"
            yield event.plain_result(
                f"✅ Chế độ báo cáo ngay phân tích gia tăng: {status_text}"
            )

        elif action == "filter_bot":
            current = self.config_manager.get_filter_bot_messages()
            new_state = not current
            self.config_manager.set_filter_bot_messages(new_state)
            status_text = "Đã bật" if new_state else "Đã tắt"
            yield event.plain_result(f"✅ Lọc tin nhắn bot: {status_text}")

        else:  # status
            check_target = getattr(event, "unified_msg_origin", None)
            if not check_target:
                check_target = (
                    f"{self._get_platform_id_from_event(event)}:GroupMessage:{group_id}"
                )

            is_allowed = self.config_manager.is_group_allowed(check_target)
            status = "Đã bật" if is_allowed else "Chưa bật"
            mode = self.config_manager.get_group_list_mode()

            auto_status = (
                "Đã bật"
                if self.config_manager.is_auto_analysis_enabled()
                else "Chưa bật"
            )
            auto_time = self.config_manager.get_auto_analysis_time()

            output_format = self.config_manager.get_output_format()[0]
            min_threshold = self.config_manager.get_min_messages_threshold()

            # Trạng thái phân tích gia tăng.
            incremental_enabled = self.config_manager.get_incremental_enabled()
            incremental_status_text = "Chưa bật"
            if incremental_enabled:
                interval = self.config_manager.get_incremental_interval_minutes()
                max_daily = self.config_manager.get_incremental_max_daily_analyses()
                active_start = self.config_manager.get_incremental_active_start_hour()
                active_end = self.config_manager.get_incremental_active_end_hour()
                incremental_status_text = (
                    f"Đã bật (mỗi {interval} phút, tối đa {max_daily} lần/ngày, "
                    f"khung giờ hoạt động {active_start}:00-{active_end}:00)"
                )

        debug_report = self.config_manager.get_incremental_report_immediately()
        debug_status = "✅ Bật" if debug_report else "❌ Tắt"
        filter_bot = self.config_manager.get_filter_bot_messages()
        filter_bot_status = "✅ Bật" if filter_bot else "❌ Tắt"

        yield event.plain_result(f"""📊 Trạng thái phân tích của nhóm hiện tại:
    • Phân tích nhóm: {status} (chế độ: {mode})
    • Phân tích tự động: {auto_status} ({auto_time})
        • Phân tích gia tăng: {incremental_status_text}
        • Chế độ gỡ lỗi: {debug_status} (báo cáo gia tăng ngay lập tức)
        • Lọc bot: {filter_bot_status}
        • Định dạng đầu ra: {output_format}
    • Số tin nhắn tối thiểu: {min_threshold}

    💡 Lệnh khả dụng: enable, disable, status, reload, test, filter_bot, incremental_debug
    💡 Định dạng đầu ra được hỗ trợ: image, text (ảnh có biểu đồ hoạt động)
    💡 Lệnh khác: /dinhdang, /tangcuong""")

    @filter.command("tangcuong", alias={"incremental_status"})
    @filter.permission_type(PermissionType.ADMIN)
    async def incremental_status(self, event: AstrMessageEvent):
        """Xem trạng thái phân tích gia tăng trong cửa sổ trượt."""
        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("❌ Vui lòng sử dụng lệnh này trong nhóm chat")
            return

        if not self.config_manager.get_incremental_enabled():
            yield event.plain_result(
                "ℹ️ Chế độ phân tích gia tăng chưa bật, vui lòng bật trong cấu hình plugin"
            )
            return

        import time as time_mod

        # Tính phạm vi cửa sổ trượt.
        analysis_days = self.config_manager.get_analysis_days()
        window_end = time_mod.time()
        window_start = window_end - (analysis_days * 24 * 3600)

        # Truy vấn các batch trong cửa sổ.
        batches = await self.incremental_store.query_batches(
            group_id, window_start, window_end
        )

        if not batches:
            from datetime import datetime

            start_str = datetime.fromtimestamp(window_start).strftime("%m-%d %H:%M")
            end_str = datetime.fromtimestamp(window_end).strftime("%m-%d %H:%M")
            yield event.plain_result(
                f"📊 Chưa có dữ liệu phân tích gia tăng trong cửa sổ "
                f"({start_str} ~ {end_str})"
            )
            return

        # Gộp batch để tạo chế độ xem tổng hợp.
        state = self.incremental_merge_service.merge_batches(
            batches, window_start, window_end
        )
        summary = state.get_summary()

        yield event.plain_result(
            f"📊 Trạng thái phân tích gia tăng (cửa sổ: {summary['window']})\n"
            f"• Số lần phân tích: {summary['total_analyses']}\n"
            f"• Tổng số tin nhắn: {summary['total_messages']}\n"
            f"• Số chủ đề: {summary['topics_count']}\n"
            f"• Số trích dẫn nổi bật: {summary['quotes_count']}\n"
            f"• Người tham gia: {summary['participants']}\n"
            f"• Khung giờ cao điểm: {summary['peak_hours']}"
        )

    async def _handle_settings_enable(self, event: AstrMessageEvent, group_id: str):
        """Helper xử lý nhánh bật cài đặt."""
        mode = self.config_manager.get_group_list_mode()
        target_id = event.unified_msg_origin or group_id

        if mode == "whitelist":
            glist = self.config_manager.get_group_list()
            if not self.config_manager.is_group_allowed(target_id):
                glist.append(target_id)
                self.config_manager.set_group_list(glist)
                yield event.plain_result(
                    f"✅ Đã thêm nhóm hiện tại vào whitelist\nID: {target_id}"
                )
                self.auto_scheduler.schedule_jobs(self.context)
            else:
                yield event.plain_result("ℹ️ Nhóm hiện tại đã có trong whitelist")
        elif mode == "blacklist":
            glist = self.config_manager.get_group_list()
            removed = False
            if target_id in glist:
                glist.remove(target_id)
                removed = True
            if group_id in glist:
                glist.remove(group_id)
                removed = True

            if removed:
                self.config_manager.set_group_list(glist)
                yield event.plain_result("✅ Đã xóa nhóm hiện tại khỏi blacklist")
                self.auto_scheduler.schedule_jobs(self.context)
            else:
                yield event.plain_result("ℹ️ Nhóm hiện tại không có trong blacklist")
        else:
            yield event.plain_result(
                "ℹ️ Đang ở chế độ không giới hạn, tất cả nhóm mặc định được bật"
            )

    async def _handle_settings_disable(self, event: AstrMessageEvent, group_id: str):
        """Helper xử lý nhánh tắt cài đặt."""
        mode = self.config_manager.get_group_list_mode()
        target_id = event.unified_msg_origin or group_id

        if mode == "whitelist":
            glist = self.config_manager.get_group_list()
            removed = False
            if target_id in glist:
                glist.remove(target_id)
                removed = True
            if group_id in glist:
                glist.remove(group_id)
                removed = True

            if removed:
                self.config_manager.set_group_list(glist)
                yield event.plain_result("✅ Đã xóa nhóm hiện tại khỏi whitelist")
                self.auto_scheduler.schedule_jobs(self.context)
            else:
                yield event.plain_result("ℹ️ Nhóm hiện tại không có trong whitelist")
        elif mode == "blacklist":
            glist = self.config_manager.get_group_list()
            if self.config_manager.is_group_allowed(target_id):
                glist.append(target_id)
                self.config_manager.set_group_list(glist)
                yield event.plain_result(
                    f"✅ Đã thêm nhóm hiện tại vào blacklist\nID: {target_id}"
                )
                self.auto_scheduler.schedule_jobs(self.context)
            else:
                yield event.plain_result("ℹ️ Nhóm hiện tại đã có trong blacklist")
        else:
            yield event.plain_result(
                "ℹ️ Đang ở chế độ không giới hạn; để tắt, hãy chuyển sang chế độ blacklist"
            )
