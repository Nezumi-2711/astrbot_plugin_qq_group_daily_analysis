"""Lập lịch phân tích tự động ở chế độ truyền thống và tăng dần."""

import asyncio
import time as time_mod
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from ...application.services.analysis_application_service import DuplicateGroupTaskError
from ...shared.trace_context import TraceContext
from ...utils.logger import logger
from ..messaging.message_sender import MessageSender
from ..platform.factory import PlatformAdapterFactory
from ..reporting.dispatcher import ReportDispatcher


class AutoScheduler:
    """Bộ lập lịch tự động hỗ trợ chế độ truyền thống và tăng dần."""

    def __init__(
        self,
        config_manager,
        analysis_service,
        bot_manager,
        report_generator=None,
        html_render_func=None,
        plugin_instance: Any | None = None,
    ):
        self.config_manager = config_manager
        self.analysis_service = analysis_service
        self.bot_manager = bot_manager
        self.report_generator = report_generator
        self.html_render_func = html_render_func
        self.plugin_instance = plugin_instance

        # Khởi tạo các thành phần cốt lõi.
        self.message_sender = MessageSender(bot_manager, config_manager)
        self.report_dispatcher = ReportDispatcher(
            config_manager, report_generator, self.message_sender
        )
        if html_render_func:
            self.report_dispatcher.set_html_render(html_render_func)

        self.scheduler_job_ids = []  # Lưu ID của các tác vụ đã đăng ký.
        self.last_executed_target = None  # Ngăn chạy lặp cùng một mốc thời gian.

        # Cache: group_id -> group_name (populated lazily)
        self._group_name_cache: dict[str, str] = {}
        self._terminating = False  # Cờ đang dừng plugin.

    def set_bot_instance(self, bot_instance):
        """Đặt bot instance để giữ tương thích ngược."""
        self.bot_manager.set_bot_instance(bot_instance)

    def set_bot_self_ids(self, bot_self_ids):
        """Đặt một hoặc nhiều ID bot."""
        # Chuẩn hoá đầu vào thành danh sách.
        if isinstance(bot_self_ids, list):
            self.bot_manager.set_bot_self_ids(bot_self_ids)
        elif bot_self_ids:
            self.bot_manager.set_bot_self_ids([bot_self_ids])

    def set_bot_qq_ids(self, bot_qq_ids):
        """Đặt ID QQ bot; đã lỗi thời, dùng set_bot_self_ids."""
        self.set_bot_self_ids(bot_qq_ids)

    async def get_platform_id_for_group(self, group_id):
        """Lấy ID nền tảng tương ứng với ID nhóm."""
        try:
            # Kiểm tra các bot instance đã đăng ký trước.
            if (
                hasattr(self.bot_manager, "_bot_instances")
                and self.bot_manager._bot_instances
            ):
                # Trả ngay khi chỉ có một instance.
                if self.bot_manager.get_platform_count() == 1:
                    platform_id = self.bot_manager.get_platform_ids()[0]
                    logger.debug(f"Chỉ có một adapter, dùng nền tảng: {platform_id}")
                    return platform_id

                # Khi có nhiều instance, kiểm tra nhóm qua từng adapter.
                logger.info(
                    f"Phát hiện nhiều adapter, đang xác định nền tảng của nhóm {group_id}..."
                )
                for platform_id in self.bot_manager.get_platform_ids():
                    try:
                        adapter = self.bot_manager.get_adapter(platform_id)
                        if adapter:
                            # Nhóm thuộc nền tảng nếu adapter lấy được thông tin nhóm.
                            info = await adapter.get_group_info(str(group_id))
                            if info:
                                logger.info(
                                    f"✅ Nhóm {group_id} thuộc nền tảng {platform_id}"
                                )
                                return platform_id
                            else:
                                logger.debug(
                                    f"Nền tảng {platform_id} không lấy được thông tin nhóm {group_id}"
                                )
                    except Exception as e:
                        logger.debug(
                            f"Xác minh nhóm {group_id} trên nền tảng {platform_id} thất bại: {e}"
                        )
                        continue

                # Không adapter nào xác định được nền tảng.
                logger.error(
                    f"❌ Không thể xác định nền tảng của nhóm {group_id} "
                    f"(đã thử: {list(self.bot_manager._bot_instances.keys())})"
                )
                return None

            # Chưa có bot instance nào được đăng ký.
            logger.error("❌ Chưa đăng ký bot instance nào")
            return None
        except Exception as e:
            logger.error(f"❌ Lấy ID nền tảng thất bại: {e}")
            return None

    async def _get_group_name_safe(
        self, group_id: str, platform_id: str | None = None
    ) -> str:
        """
        Lấy tên nhóm dễ đọc để tạo TraceID.

        Dùng cache bộ nhớ để tránh gọi API lặp lại và fallback về group_id.
        """
        if group_id in self._group_name_cache:
            return self._group_name_cache[group_id]

        try:
            pid = platform_id or await self.get_platform_id_for_group(group_id)
            if pid:
                adapter = self.bot_manager.get_adapter(pid)
                if adapter:
                    info = await adapter.get_group_info(group_id)
                    if info and info.group_name:
                        self._group_name_cache[group_id] = info.group_name
                        return info.group_name
        except Exception:
            pass

        return group_id

    # ================================================================
    # Đăng ký và huỷ tác vụ
    # ================================================================

    def schedule_jobs(self, context):
        """Đăng ký tác vụ theo cấu hình danh sách phân tầng."""
        # Dọn các tác vụ cũ trước.
        self.unschedule_jobs(context)

        # unschedule_jobs đặt _terminating=True cho luồng shutdown;
        # schedule_jobs nghĩa là plugin vẫn chạy nên cần đặt lại cờ.
        self._terminating = False

        if not self.config_manager.is_auto_analysis_enabled():
            logger.info(
                "Danh sách phân tích định kỳ trống ở chế độ danh sách trắng; không đăng ký tác vụ."
            )
            return

        scheduler = context.cron_manager.scheduler

        # 1. Đăng ký tác vụ báo cáo cho cả phân tích đầy đủ và tăng dần.
        # Mỗi mốc thời gian cấu hình sẽ kích hoạt một lần phân giải.
        logger.info("Đang đăng ký tác vụ báo cáo phân tích định kỳ...")
        self._schedule_report_time_jobs(scheduler)

        # 2. Chỉ đăng ký tác vụ trích xuất tăng dần khi tính năng được bật.
        if self.config_manager.get_incremental_enabled():
            logger.info(
                "Phân tích tăng dần đã bật; đang đăng ký tác vụ trích xuất trong ngày..."
            )
            self._schedule_incremental_cron_jobs(scheduler)
        else:
            logger.info(
                "Phân tích tăng dần chưa bật; chỉ chạy phân tích đầy đủ định kỳ."
            )

    def _schedule_report_time_jobs(self, scheduler):
        """Đăng ký tác vụ tạo báo cáo tại các mốc đã cấu hình.

        Chế độ hiệu lực lúc chạy quyết định phân tích đầy đủ hay báo cáo tăng dần.
        """
        time_config = self.config_manager.get_auto_analysis_time()
        if isinstance(time_config, str):
            time_config = [time_config]

        for i, t_str in enumerate(time_config):
            try:
                t_str = str(t_str).replace("：", ":").strip()
                hour, minute = t_str.split(":")

                trigger = CronTrigger(hour=int(hour), minute=int(minute))
                job_id = f"astrbot_plugin_qq_group_daily_analysis_trigger_{i}"

                scheduler.add_job(
                    self._run_scheduled_report,
                    trigger=trigger,
                    id=job_id,
                    replace_existing=True,
                    misfire_grace_time=60,
                )
                self.scheduler_job_ids.append(job_id)
                logger.info(
                    f"Đã đăng ký tác vụ báo cáo định kỳ: {t_str} (Job ID: {job_id})"
                )

            except Exception as e:
                logger.error(f"Đăng ký tác vụ định kỳ thất bại ({t_str}): {e}")

    def _schedule_incremental_cron_jobs(self, scheduler):
        """
        Đăng ký tác vụ phân tích tăng dần trong khung giờ hoạt động.

        Tác vụ này chỉ trích xuất dữ liệu; báo cáo được tạo tại giờ phân tích hằng ngày.
        """
        active_start_hour = self.config_manager.get_incremental_active_start_hour()
        active_end_hour = self.config_manager.get_incremental_active_end_hour()
        interval_minutes = self.config_manager.get_incremental_interval_minutes()
        max_daily = self.config_manager.get_incremental_max_daily_analyses()

        # Tính các mốc kích hoạt trong khung giờ hoạt động.
        trigger_times = []
        current_minutes = active_start_hour * 60
        end_minutes = active_end_hour * 60

        while current_minutes < end_minutes and len(trigger_times) < max_daily:
            hour = current_minutes // 60
            minute = current_minutes % 60
            trigger_times.append((hour, minute))
            current_minutes += interval_minutes

        # Đăng ký tác vụ phân tích tăng dần.
        for hour, minute in trigger_times:
            try:
                trigger = CronTrigger(hour=hour, minute=minute)
                job_id = f"incremental_analysis_{hour:02d}{minute:02d}"

                scheduler.add_job(
                    self._run_incremental_analysis,
                    trigger=trigger,
                    id=job_id,
                    replace_existing=True,
                    misfire_grace_time=60,
                )
                self.scheduler_job_ids.append(job_id)
                logger.info(
                    f"Đã đăng ký tác vụ phân tích tăng dần: "
                    f"{hour:02d}:{minute:02d} (Job ID: {job_id})"
                )
            except Exception as e:
                logger.error(
                    f"Đăng ký tác vụ phân tích tăng dần thất bại ({hour:02d}:{minute:02d}): {e}"
                )

        logger.info(f"Đăng ký lịch tăng dần hoàn tất: {len(trigger_times)} tác vụ")

    def unschedule_jobs(self, context):
        """Huỷ các tác vụ định kỳ."""
        self._terminating = True
        if (
            not context
            or not hasattr(context, "cron_manager")
            or not context.cron_manager
        ):
            return

        scheduler = context.cron_manager.scheduler
        if not scheduler:
            return

        for job_id in self.scheduler_job_ids:
            try:
                if scheduler.get_job(job_id):
                    scheduler.remove_job(job_id)
                    logger.debug(f"Đã xoá tác vụ định kỳ: {job_id}")
            except Exception as e:
                logger.warning(f"Xoá tác vụ định kỳ thất bại ({job_id}): {e}")
        self.scheduler_job_ids.clear()

    # ================================================================
    # Hàm dùng chung để phân giải mục tiêu phân tích định kỳ
    # ================================================================

    async def _get_scheduled_targets(
        self, mode_filter: str | None = None
    ) -> list[tuple[str, str, str]]:
        """
        Xác định nhóm mục tiêu và chiến lược theo bộ lọc phân tầng.

        Quy trình:
        1. Nhóm phải nằm trong danh sách được phép ở cấu hình cơ sở.
        2. Nhóm phải vượt qua bộ lọc danh sách phân tích định kỳ.
        3. Nhóm trong danh sách tăng dần dùng chế độ tăng dần, còn lại dùng mặc định.

        Args:
            mode_filter: Chỉ trả mục tiêu khớp traditional hoặc incremental.
        """
        # Lấy thông tin cơ sở.
        all_groups = await self._get_all_groups()

        # Tải trước các danh sách và chế độ.
        sched_list = self.config_manager.get_scheduled_group_list()
        sched_list_mode = self.config_manager.get_scheduled_group_list_mode()

        incr_list = self.config_manager.get_incremental_group_list()
        incr_list_mode = self.config_manager.get_incremental_group_list_mode()

        result = []

        # Duyệt nhóm trên mọi nền tảng.
        for platform_id, group_id_orig in all_groups:
            group_id = str(group_id_orig)
            umo = f"{platform_id}:GroupMessage:{group_id}"

            # 1. Lớp truy cập: danh sách đen/trắng cơ sở.
            if not self.config_manager.is_group_allowed(umo):
                continue

            # 2. Lớp định kỳ: danh sách đen/trắng phân tích định kỳ.
            if not self.config_manager.is_group_in_filtered_list(
                umo, sched_list_mode, sched_list
            ):
                continue

            # 3. Lớp chế độ: danh sách đen/trắng tăng dần.
            if self.config_manager.is_group_in_filtered_list(
                umo, incr_list_mode, incr_list
            ):
                # Trong danh sách tăng dần.
                effective_mode = "incremental"
            else:
                # Không trong danh sách tăng dần.
                effective_mode = "traditional"

            # 4. Lọc chế độ nếu caller yêu cầu.
            if mode_filter and effective_mode != mode_filter:
                continue

            result.append((group_id, platform_id, effective_mode))

        logger.info(
            f"Phân giải lịch phân tầng hoàn tất: {len(result)} nhóm hợp lệ"
            + (f" (bộ lọc chế độ: {mode_filter})" if mode_filter else "")
        )
        return result

    # ================================================================
    # Điểm vào thống nhất cho lịch báo cáo
    # ================================================================

    async def _run_scheduled_report(self):
        """Điểm vào thống nhất cho phân tích định kỳ.

        Kích hoạt tại giờ cấu hình và phân phối theo chế độ:
        - traditional: lấy toàn bộ dữ liệu, phân tích và gửi báo cáo;
        - incremental: hợp nhất dữ liệu tăng dần và gửi báo cáo cuối.
        """
        if self._terminating:
            return
        try:
            logger.info("Đã kích hoạt báo cáo định kỳ — bắt đầu phân giải mục tiêu")

            all_targets = await self._get_scheduled_targets()

            if not all_targets:
                logger.info("Không có nhóm nào cần phân tích định kỳ")
                return

            max_concurrent = self.config_manager.get_max_concurrent_tasks()
            sem = asyncio.Semaphore(max_concurrent)
            logger.info(
                f"Báo cáo định kỳ: {len(all_targets)} mục tiêu "
                f"(giới hạn đồng thời: {max_concurrent})"
            )

            async def dispatch_group(gid, pid, mode):
                async with sem:
                    if mode == "incremental":
                        return await self._perform_incremental_final_report_for_group_with_timeout(
                            gid, pid
                        )
                    else:
                        return await self._perform_auto_analysis_for_group_with_timeout(
                            gid, pid
                        )

            tasks = []
            stagger = self.config_manager.get_stagger_seconds() or 2
            # Giãn cách tác vụ lớn để giảm tải đỉnh tức thời.
            for idx, (gid, pid, mode) in enumerate(all_targets):
                if self._terminating:
                    logger.info("Plugin đang dừng; huỷ tạo các tác vụ tiếp theo")
                    break

                # Giãn thời điểm khởi động để phân tán tải API.
                if idx > 0 and stagger > 0:
                    await asyncio.sleep(stagger)

                task = asyncio.create_task(
                    dispatch_group(gid, pid, mode),
                    name=f"report_{mode}_{gid}",
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Thống kê kết quả.
            success_count = 0
            skip_count = 0
            error_count = 0

            for i, result in enumerate(results):
                gid, _, _ = all_targets[i]
                if isinstance(result, DuplicateGroupTaskError):
                    skip_count += 1
                elif isinstance(result, Exception):
                    logger.error(
                        f"Tác vụ báo cáo định kỳ của nhóm {gid} gặp lỗi: {result}"
                    )
                    error_count += 1
                elif isinstance(result, dict) and not result.get("success", True):
                    skip_count += 1
                else:
                    success_count += 1

            logger.info(
                f"Báo cáo định kỳ hoàn tất — thành công: {success_count}, "
                f"bỏ qua: {skip_count}, thất bại: {error_count}, "
                f"tổng: {len(all_targets)}"
            )

        except Exception as e:
            logger.error(f"Chạy báo cáo định kỳ thất bại: {e}", exc_info=True)

    async def _perform_auto_analysis_for_group_with_timeout(
        self, group_id: str, target_platform_id: str | None = None
    ):
        """Phân tích tự động một nhóm với giới hạn thời gian."""
        try:
            # Mỗi nhóm có timeout 30 phút để hỗ trợ batch lớn.
            await asyncio.wait_for(
                self._perform_auto_analysis_for_group(group_id, target_platform_id),
                timeout=1800,
            )
        except asyncio.TimeoutError:
            logger.error(f"Phân tích nhóm {group_id} quá hạn 30 phút; bỏ qua nhóm")
        except Exception as e:
            logger.error(f"Tác vụ phân tích nhóm {group_id} thất bại: {e}")

    async def _perform_auto_analysis_for_group(
        self, group_id: str, target_platform_id: str | None = None
    ):
        """Phân tích tự động một nhóm qua AnalysisApplicationService."""
        try:
            # Dùng tên nhóm dễ đọc để tạo TraceID có nghĩa.
            group_name = await self._get_group_name_safe(group_id, target_platform_id)
            trace_id = TraceContext.generate(prefix="group", group_name=group_name)
            TraceContext.set(trace_id)

            if self._terminating:
                return

            logger.info(
                f"Bắt đầu phân tích tự động nhóm {group_id} "
                f"(nền tảng: {target_platform_id or 'Tự động'})"
            )

            # Kiểm tra trạng thái nền tảng qua BotManager.
            if not self.bot_manager.is_ready_for_auto_analysis():
                logger.warning(
                    f"Bỏ qua phân tích nhóm {group_id}: trình quản lý bot chưa sẵn sàng"
                )
                return

            # AnalysisApplicationService xử lý use case và khoá nhóm.
            result = await self.analysis_service.execute_daily_analysis(
                group_id=group_id, platform_id=target_platform_id, manual=False
            )

            if not result.get("success"):
                reason = result.get("reason")
                logger.info(f"Bỏ qua phân tích tự động nhóm {group_id}: {reason}")
                return

            # Lấy kết quả phân tích và adapter.
            analysis_result = result["analysis_result"]
            adapter = result["adapter"]

            # Xuất và gửi báo cáo.
            await self.report_dispatcher.dispatch(
                group_id,
                analysis_result,
                adapter.platform_id
                if hasattr(adapter, "platform_id")
                else target_platform_id,
            )

            logger.info(f"Phân tích tự động nhóm {group_id} thành công")

        except DuplicateGroupTaskError:
            # DuplicateGroupTaskError nghĩa là tác vụ đang chạy; bỏ qua an toàn.
            logger.debug(f"Bỏ qua nhóm {group_id} do xung đột khoá đồng thời")
            raise  # Ném lại để caller biết tác vụ chưa thực sự chạy.
        except Exception as e:
            logger.error(
                f"Phân tích tự động nhóm {group_id} thất bại: {e}", exc_info=True
            )
        finally:
            logger.debug(f"Kết thúc quy trình phân tích tự động nhóm {group_id}")

    # ================================================================
    # Chế độ phân tích tăng dần
    # ================================================================

    async def _run_incremental_analysis(self):
        """Phân tích các nhóm có chế độ mục tiêu là incremental."""
        if self._terminating:
            return
        try:
            logger.info("Bắt đầu phân tích tăng dần tự động ở chế độ đồng thời")

            # Chỉ chọn nhóm có chế độ incremental.
            incr_targets = await self._get_scheduled_targets(mode_filter="incremental")

            if not incr_targets:
                logger.info("Không có nhóm nào được cấu hình cho phân tích tăng dần")
                return

            target_list = incr_targets
            stagger = self.config_manager.get_incremental_stagger_seconds()
            max_concurrent = self.config_manager.get_max_concurrent_tasks()

            logger.info(
                f"Sẽ phân tích tăng dần {len(target_list)} nhóm "
                f"(giới hạn đồng thời: {max_concurrent}, giãn cách: {stagger} giây)"
            )

            sem = asyncio.Semaphore(max_concurrent)

            async def staggered_incremental(idx, gid, pid):
                if idx > 0 and stagger > 0:
                    await asyncio.sleep(stagger * idx)

                async with sem:
                    result = (
                        await self._perform_incremental_analysis_for_group_with_timeout(
                            gid, pid
                        )
                    )

                    # Tuỳ chọn báo cáo ngay phục vụ debug.
                    if self.config_manager.get_incremental_report_immediately():
                        if isinstance(result, dict) and result.get("success"):
                            logger.info(
                                f"Chế độ báo cáo ngay đang bật; đang tạo báo cáo cho nhóm {gid}..."
                            )
                            await self._perform_incremental_final_report_for_group_with_timeout(
                                gid, pid
                            )

                    return result

            analysis_tasks = []
            for idx, (gid, pid, _mode) in enumerate(target_list):
                if self._terminating:
                    logger.info(
                        "Plugin đang dừng; huỷ tạo các tác vụ tăng dần tiếp theo"
                    )
                    break
                task = asyncio.create_task(
                    staggered_incremental(idx, gid, pid),
                    name=f"incremental_group_{gid}",
                )
                analysis_tasks.append(task)

            results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

            success_count = 0
            skip_count = 0
            error_count = 0

            for i, result in enumerate(results):
                gid, _, _ = target_list[i]
                if isinstance(result, DuplicateGroupTaskError):
                    skip_count += 1
                elif isinstance(result, Exception):
                    logger.error(
                        f"Tác vụ phân tích tăng dần nhóm {gid} gặp lỗi: {result}"
                    )
                    error_count += 1
                elif isinstance(result, dict) and not result.get("success", True):
                    skip_count += 1
                else:
                    success_count += 1

            logger.info(
                f"Phân tích tăng dần hoàn tất — thành công: {success_count}, "
                f"bỏ qua: {skip_count}, thất bại: {error_count}, "
                f"tổng: {len(target_list)}"
            )

        except Exception as e:
            logger.error(f"Chạy phân tích tăng dần thất bại: {e}", exc_info=True)

    async def _perform_incremental_analysis_for_group_with_timeout(
        self, group_id: str, target_platform_id: str | None = None
    ):
        """Phân tích tăng dần một nhóm với timeout 10 phút."""
        try:
            result = await asyncio.wait_for(
                self._perform_incremental_analysis_for_group(
                    group_id, target_platform_id
                ),
                timeout=600,
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"Phân tích tăng dần nhóm {group_id} quá hạn 10 phút; bỏ qua")
            return {"success": False, "reason": "timeout"}
        except Exception as e:
            logger.error(f"Tác vụ phân tích tăng dần nhóm {group_id} thất bại: {e}")
            return {"success": False, "reason": str(e)}

    async def _perform_incremental_analysis_for_group(
        self, group_id: str, target_platform_id: str | None = None
    ):
        """Phân tích tăng dần một nhóm qua AnalysisApplicationService."""
        try:
            # Dùng tên nhóm dễ đọc để tạo TraceID có nghĩa.
            group_name = await self._get_group_name_safe(group_id, target_platform_id)
            trace_id = TraceContext.generate(prefix="incr", group_name=group_name)
            TraceContext.set(trace_id)

            if self._terminating:
                return

            logger.info(
                f"Bắt đầu phân tích tăng dần nhóm {group_id} "
                f"(nền tảng: {target_platform_id or 'Tự động'})"
            )

            # Kiểm tra trạng thái nền tảng.
            if not self.bot_manager.is_ready_for_auto_analysis():
                logger.warning(
                    f"Bỏ qua phân tích tăng dần nhóm {group_id}: trình quản lý bot chưa sẵn sàng"
                )
                return {"success": False, "reason": "bot_not_ready"}

            # AnalysisApplicationService xử lý use case và khoá nhóm.
            result = await self.analysis_service.execute_incremental_analysis(
                group_id=group_id, platform_id=target_platform_id
            )

            if not result.get("success"):
                reason = result.get("reason", "unknown")
                logger.info(f"Bỏ qua phân tích tăng dần nhóm {group_id}: {reason}")
                return result

            # Phân tích tăng dần chỉ tích luỹ dữ liệu, không gửi báo cáo.
            batch_summary = result.get("batch_summary", {})
            logger.info(
                f"Phân tích tăng dần nhóm {group_id} hoàn tất: "
                f"tin nhắn={result.get('messages_count', 0)}, "
                f"chủ đề={batch_summary.get('topics_count', 0)}, "
                f"trích dẫn={batch_summary.get('quotes_count', 0)}"
            )
            return result

        except DuplicateGroupTaskError:
            # Tác vụ đang chạy; bỏ qua an toàn.
            logger.debug(f"Bỏ qua phân tích tăng dần nhóm {group_id} do xung đột khoá")
            return {"success": False, "reason": "already_running"}
        except Exception as e:
            logger.error(
                f"Phân tích tăng dần nhóm {group_id} thất bại: {e}", exc_info=True
            )
            return {"success": False, "reason": str(e)}
        finally:
            logger.debug(f"Kết thúc quy trình tăng dần nhóm {group_id}")

    # ================================================================
    # Báo cáo tăng dần cuối cho một nhóm và logic fallback
    # ================================================================

    async def _perform_incremental_final_report_for_group_with_timeout(
        self, group_id: str, target_platform_id: str | None = None
    ):
        """Tạo báo cáo tăng dần cuối với timeout và fallback.

        Nếu báo cáo thất bại vì lý do khác thiếu tin nhắn hoặc đang chạy,
        chuyển sang phân tích đầy đủ khi fallback tự động được bật.
        """
        try:
            result = await asyncio.wait_for(
                self._perform_incremental_final_report_for_group(
                    group_id, target_platform_id
                ),
                timeout=1800,
            )

            # Xác định có cần fallback hay không, ví dụ không có dữ liệu tăng dần.
            if isinstance(result, dict) and not result.get("success"):
                reason = result.get("reason", "")
                if reason in ("below_threshold", "already_running"):
                    return result  # Bỏ qua bình thường, không cần fallback.
                if self.config_manager.get_incremental_fallback_enabled():
                    logger.warning(
                        f"Báo cáo tăng dần cuối nhóm {group_id} thất bại "
                        f"(reason={reason}); đang fallback về phân tích đầy đủ..."
                    )
                    return await self._fallback_to_traditional(
                        group_id, target_platform_id
                    )

            return result

        except asyncio.TimeoutError:
            logger.error(f"Báo cáo cuối nhóm {group_id} quá hạn 30 phút")
            if self.config_manager.get_incremental_fallback_enabled():
                logger.warning(
                    f"Báo cáo tăng dần nhóm {group_id} quá hạn; đang fallback về phân tích đầy đủ..."
                )
                return await self._fallback_to_traditional(group_id, target_platform_id)
            return {"success": False, "reason": "timeout"}

        except Exception as e:
            logger.error(f"Tác vụ báo cáo cuối nhóm {group_id} thất bại: {e}")
            if self.config_manager.get_incremental_fallback_enabled():
                logger.warning(
                    f"Báo cáo tăng dần nhóm {group_id} gặp lỗi; đang fallback về phân tích đầy đủ..."
                )
                return await self._fallback_to_traditional(group_id, target_platform_id)
            return {"success": False, "reason": str(e)}

    async def _fallback_to_traditional(
        self, group_id: str, target_platform_id: str | None = None
    ):
        """Fallback về phân tích đầy đủ khi báo cáo tăng dần thất bại."""
        try:
            logger.info(
                f"⬆️ Nhóm {group_id} fallback về phân tích đầy đủ "
                f"(nền tảng: {target_platform_id or 'Tự động'})"
            )
            await self._perform_auto_analysis_for_group_with_timeout(
                group_id, target_platform_id
            )
            return {"success": True, "fallback": True}
        except Exception as fallback_err:
            logger.error(
                f"Fallback phân tích đầy đủ nhóm {group_id} cũng thất bại: {fallback_err}",
                exc_info=True,
            )
            return {"success": False, "reason": f"fallback_failed: {fallback_err}"}

    async def _perform_incremental_final_report_for_group(
        self, group_id: str, target_platform_id: str | None = None
    ):
        """Tạo báo cáo tăng dần cuối qua AnalysisApplicationService."""
        try:
            # Dùng tên nhóm dễ đọc để tạo TraceID có nghĩa.
            group_name = await self._get_group_name_safe(group_id, target_platform_id)
            trace_id = TraceContext.generate(prefix="report", group_name=group_name)
            TraceContext.set(trace_id)

            if self._terminating:
                return

            logger.info(
                f"Bắt đầu tạo báo cáo tăng dần cuối cho nhóm {group_id} "
                f"(nền tảng: {target_platform_id or 'Tự động'})"
            )

            # Kiểm tra trạng thái nền tảng.
            if not self.bot_manager.is_ready_for_auto_analysis():
                logger.warning(
                    f"Bỏ qua báo cáo cuối nhóm {group_id}: trình quản lý bot chưa sẵn sàng"
                )
                return {"success": False, "reason": "bot_not_ready"}

            # AnalysisApplicationService xử lý use case và khoá nhóm.
            result = await self.analysis_service.execute_incremental_final_report(
                group_id=group_id, platform_id=target_platform_id
            )

            if not result.get("success"):
                reason = result.get("reason", "unknown")
                logger.info(f"Bỏ qua báo cáo cuối nhóm {group_id}: {reason}")
                return result

            # Lấy kết quả, adapter và phân phối báo cáo.
            analysis_result = result["analysis_result"]
            adapter = result["adapter"]

            await self.report_dispatcher.dispatch(
                group_id,
                analysis_result,
                adapter.platform_id
                if hasattr(adapter, "platform_id")
                else target_platform_id,
            )

            # Dọn batch quá hạn, giữ dữ liệu bằng hai lần cửa sổ làm buffer.
            try:
                analysis_days = self.config_manager.get_analysis_days()
                before_ts = time_mod.time() - (analysis_days * 2 * 24 * 3600)
                incremental_store = self.analysis_service.incremental_store
                if incremental_store:
                    cleaned = await incremental_store.cleanup_old_batches(
                        group_id, before_ts
                    )
                    if cleaned > 0:
                        logger.info(
                            f"Đã dọn {cleaned} batch quá hạn sau khi gửi báo cáo nhóm {group_id}"
                        )
            except Exception as cleanup_err:
                logger.warning(
                    f"Dọn batch quá hạn của nhóm {group_id} thất bại, "
                    f"không ảnh hưởng báo cáo: {cleanup_err}"
                )

            logger.info(f"Gửi báo cáo tăng dần cuối nhóm {group_id} thành công")
            return result

        except DuplicateGroupTaskError:
            # Tác vụ đang chạy; bỏ qua an toàn.
            logger.debug(f"Bỏ qua báo cáo cuối nhóm {group_id} do xung đột khoá")
            return {"success": False, "reason": "already_running"}
        except Exception as e:
            logger.error(f"Báo cáo cuối nhóm {group_id} thất bại: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}
        finally:
            logger.debug(f"Kết thúc quy trình báo cáo cuối nhóm {group_id}")

    # ================================================================
    # Lấy danh sách nhóm ở tầng infrastructure
    # ================================================================

    async def _get_all_groups(self) -> list[tuple[str, str]]:
        """
        Lấy danh sách nhóm của mọi bot instance qua PlatformAdapter.

        Returns:
            list[tuple[str, str]]: [(platform_id, group_id), ...]
        """
        all_groups = set()

        # 1. Thử khám phá bot lần cuối trước khi quét.
        # Nhờ đó tác vụ định kỳ vẫn làm mới trạng thái nếu cold start thất bại.
        if hasattr(self.bot_manager, "auto_discover_bot_instances"):
            try:
                await self.bot_manager.auto_discover_bot_instances()
            except Exception as e:
                logger.warning(
                    f"[AutoScheduler] Khám phá nền tảng khi quét định kỳ thất bại: {e}"
                )

        bot_ids = list(self.bot_manager._bot_instances.keys())

        if not bot_ids:
            logger.warning(
                "[AutoScheduler] Lịch phân tích đã bật nhưng không có bot online; bỏ qua tác vụ."
            )
            return []

        logger.info(
            f"[AutoScheduler] Đang quét tài nguyên nhóm trên {len(bot_ids)} nền tảng..."
        )

        for platform_id, bot_instance in self.bot_manager._bot_instances.items():
            # Kiểm tra plugin có được bật trên nền tảng này không.
            if not self.bot_manager.is_plugin_enabled(
                platform_id, "astrbot_plugin_qq_group_daily_analysis"
            ):
                logger.debug(
                    f"Plugin chưa bật trên nền tảng {platform_id}; bỏ qua danh sách nhóm"
                )
                continue

            try:
                # 1. Ưu tiên adapter đã được BotManager tạo.
                adapter = self.bot_manager.get_adapter(platform_id)

                # 2. Tạo tạm adapter làm phương án dự phòng.
                platform_name = None
                if not adapter:
                    platform_name = self.bot_manager._detect_platform_name(bot_instance)
                    if platform_name:
                        adapter = PlatformAdapterFactory.create(
                            platform_name,
                            bot_instance,
                            config={
                                "bot_self_ids": self.config_manager.get_bot_self_ids(),
                                "platform_id": str(platform_id),
                            },
                        )

                # 3. Lấy danh sách nhóm qua adapter.
                if adapter:
                    try:
                        groups = await adapter.get_group_list()
                        groups = [
                            str(group_id).strip()
                            for group_id in groups
                            if str(group_id).strip()
                        ]

                        # Lấy tên nền tảng chỉ để ghi log.
                        p_name = None
                        if hasattr(adapter, "get_platform_name"):
                            try:
                                p_name = adapter.get_platform_name()
                            except Exception:
                                p_name = None

                        for group_id in groups:
                            all_groups.add((platform_id, str(group_id)))

                        logger.info(
                            f"Nền tảng {platform_id} ({p_name or 'không rõ'}) "
                            f"đã lấy thành công {len(groups)} nhóm"
                        )
                        continue

                    except Exception as e:
                        logger.warning(
                            f"Adapter {platform_id} lấy danh sách nhóm thất bại: {e}"
                        )

                # 4. Adapter không lấy được danh sách nhóm.
                logger.debug(
                    f"Nền tảng {platform_id} không lấy được danh sách nhóm qua adapter"
                )

            except Exception as e:
                logger.error(
                    f"Lấy danh sách nhóm trên nền tảng {platform_id} gặp lỗi: {e}"
                )

        return list(all_groups)
