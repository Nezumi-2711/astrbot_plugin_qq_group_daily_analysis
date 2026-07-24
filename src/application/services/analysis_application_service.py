"""
Dịch vụ application cho phân tích.

Triển khai các use case cốt lõi gồm phân tích nhóm hằng ngày, tạo báo cáo
và phân tích gia tăng; điều phối domain service, platform adapter và persistence.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time as time_mod
import weakref
from collections import defaultdict
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from ...domain.entities.incremental_state import IncrementalBatch
from ...domain.models.data_models import TokenUsage
from ...domain.repositories.analysis_repository import IAnalysisProvider
from ...domain.repositories.report_repository import IReportGenerator
from ...domain.services.analysis_domain_service import (
    AnalysisDomainService,
    UserActivityStats,
)
from ...domain.services.incremental_merge_service import IncrementalMergeService
from ...domain.services.statistics_service import StatisticsService
from ...domain.value_objects.unified_message import UnifiedMessage
from ...infrastructure.persistence.incremental_store import IncrementalStore
from ...utils.logger import logger


class DuplicateGroupTaskError(Exception):
    """Được phát sinh khi một nhóm khởi chạy trùng loại tác vụ cùng lúc."""

    pass


class AnalysisApplicationService:
    """Điều phối quy trình phân tích hằng ngày và phân tích gia tăng."""

    def __init__(
        self,
        config_manager: Any,
        bot_manager: Any,
        history_manager: Any,
        report_generator: IReportGenerator,
        llm_analyzer: IAnalysisProvider,
        statistics_service: StatisticsService,
        analysis_domain_service: AnalysisDomainService,
        incremental_store: IncrementalStore | None = None,
        incremental_merge_service: IncrementalMergeService | None = None,
    ):
        self.config_manager = config_manager
        self.bot_manager = bot_manager
        self.history_manager = history_manager
        self.report_generator = report_generator
        self.llm_analyzer = llm_analyzer
        self.statistics_service = statistics_service
        self.analysis_domain_service = analysis_domain_service
        self.incremental_store = incremental_store
        self.incremental_merge_service = incremental_merge_service
        self._locks = weakref.WeakValueDictionary()
        # Semaphore LLM toàn cục để kiểm soát tải đồng thời lên API.
        # Dùng giá trị cấu hình đồng thời riêng cho LLM.
        max_concurrent = self.config_manager.get_llm_max_concurrent()
        self.llm_semaphore = asyncio.Semaphore(max_concurrent)
        # Theo dõi tác vụ đang chạy để kiểm tra và thiết lập nguyên tử,
        # tránh điều kiện tranh chấp khi dùng locked().
        self._active_tasks = set()

    @asynccontextmanager
    async def group_lock(self, group_id: str, task_type: str = "analysis"):
        """
        Chỉ cho phép một tác vụ cùng loại của cùng một nhóm chạy tại một thời điểm.
        Khoá tự động được giải phóng khi thoát khỏi context.
        """
        lock_key = f"{task_type}:{group_id}"

        # Lấy hoặc tạo khoá riêng cho nhóm, làm lớp giới hạn tài nguyên thứ hai.
        lock = self._locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[lock_key] = lock

        # Dùng set đồng bộ để kiểm tra trạng thái đang chạy theo cách nguyên tử.
        # Trong event loop đơn luồng của asyncio, đoạn đồng bộ không bị ngắt.
        if lock_key in self._active_tasks:
            logger.warning(
                f"Tác vụ {task_type} của nhóm {group_id} đang chạy; bỏ qua yêu cầu này"
            )
            raise DuplicateGroupTaskError(f"Duplicate task for {lock_key}")

        # Đánh dấu bắt đầu tác vụ.
        self._active_tasks.add(lock_key)

        try:
            async with lock:
                logger.debug(
                    f"[Lock] Đã lấy khoá độc quyền {task_type} của nhóm {group_id}"
                )
                yield
        finally:
            # Giải phóng: đánh dấu tác vụ kết thúc.
            self._active_tasks.discard(lock_key)
            logger.debug(
                f"[Lock] Đã giải phóng khoá độc quyền {task_type} của nhóm {group_id}"
            )

    async def execute_daily_analysis(
        self,
        group_id: str,
        platform_id: str | None = None,
        manual: bool = False,
        days: int | None = None,
    ) -> dict[str, Any]:
        """
        Thực thi use case phân tích hằng ngày.

        Quy trình:
        1. Lấy adapter.
        2. Lấy tin nhắn (infrastructure).
        3. Thống kê cơ bản (domain service).
        4. Phân tích thành viên (domain service).
        5. Phân tích ngữ nghĩa bằng LLM.
        6. Tạo báo cáo.
        7. Lưu bản tóm tắt.
        8. Trả kết quả.
        """

        async with self.group_lock(group_id, "daily"):
            logger.info(
                f"Bắt đầu use case phân tích: nhóm {group_id}, platform_id={platform_id or 'mặc định'}, days={days or 'mặc định'}"
            )

            # 1. Lấy adapter
            adapter = self.bot_manager.get_adapter(platform_id)
            if not adapter:
                raise ValueError(f"Không tìm thấy adapter cho nền tảng {platform_id}")

            # Kiểm tra nhóm có bị tắt quyền gửi tin hay không.
            if hasattr(adapter, "is_group_muted"):
                try:
                    if await adapter.is_group_muted(group_id):
                        logger.info(
                            f"Nhóm {group_id} đang tắt quyền gửi tin toàn nhóm hoặc với bot; bỏ qua phân tích"
                        )
                        return {"success": False, "reason": "muted"}
                except Exception as e:
                    logger.warning(
                        f"Lỗi khi kiểm tra trạng thái tắt quyền gửi của nhóm {group_id}: {e}"
                    )

            # Feishu kiểm tra quyền và làm nóng cache ảnh thành viên trước khi phân tích.
            if hasattr(adapter, "prepare_group_member_cache"):
                try:
                    logger.info(
                        "Kiểm tra trước thành viên nền tảng: group=%s, platform=%s",
                        group_id,
                        platform_id or "default",
                    )
                    ok, err = await adapter.prepare_group_member_cache(group_id)  # type: ignore[attr-defined]
                    if not ok and err:
                        raise ValueError(err)
                    logger.info(
                        "Kiểm tra trước thành viên nền tảng thành công: group=%s, platform=%s",
                        group_id,
                        platform_id or "default",
                    )
                except Exception as e:
                    raise ValueError(
                        "Kiểm tra trước thông tin thành viên Feishu thất bại; "
                        f"vui lòng cấp đủ quyền cho ứng dụng: {e}"
                    ) from e

            # 2. Lấy tin nhắn
            if days is None:
                days = self.config_manager.get_analysis_days()
            max_count = self.config_manager.get_max_messages()

            raw_messages = await adapter.fetch_messages(
                group_id=group_id, days=days, max_count=max_count
            )
            logger.info(
                "Đã lấy tin nhắn: group=%s, platform=%s, raw_count=%s, days=%s, max_count=%s",
                group_id,
                platform_id or "default",
                len(raw_messages),
                days,
                max_count,
            )

            if not raw_messages:
                logger.warning(
                    f"Nhóm {group_id} không có tin nhắn hoặc không thể lấy tin trong {days} ngày gần đây"
                )
                return {"success": False, "reason": "no_messages"}

            # 3. Làm sạch tin nhắn: lọc command, tin nhắn bot và nhiễu.
            from ...domain.services.message_cleaner_service import MessageCleanerService

            cleaner = MessageCleanerService()
            bot_self_ids = self.config_manager.get_bot_self_ids()
            if not self.config_manager.get_filter_bot_messages():
                bot_self_ids = []
            logger.debug(
                "filter_bot_messages=%s, bot_self_ids=%s",
                self.config_manager.get_filter_bot_messages(),
                bot_self_ids,
            )

            # Luôn lọc command để báo cáo không bị nhiễu.
            unified_messages = cleaner.clean_messages(
                raw_messages, bot_self_ids=bot_self_ids, filter_commands=True
            )
            logger.info(
                "Đã làm sạch tin nhắn: group=%s, platform=%s, cleaned_count=%s, dropped=%s",
                group_id,
                platform_id or "default",
                len(unified_messages),
                max(len(raw_messages) - len(unified_messages), 0),
            )

            # 4. Kiểm tra ngưỡng tin nhắn tối thiểu sau khi làm sạch.
            threshold = self.config_manager.get_min_messages_threshold()
            if len(unified_messages) < threshold and not manual:
                logger.info(
                    f"Số tin nhắn hợp lệ của nhóm {group_id} ({len(unified_messages)}) chưa đạt ngưỡng phân tích tự động ({threshold})"
                )
                return {"success": False, "reason": "below_threshold"}

            # 5. Thống kê cơ bản (domain service)
            statistics = await asyncio.to_thread(
                self.statistics_service.calculate_group_statistics, unified_messages
            )

            # 4. Phân tích thành viên (domain service)
            user_activity = await asyncio.to_thread(
                self.analysis_domain_service.analyze_user_activity,
                unified_messages,
                bot_self_ids,
            )

            max_user_titles = self.config_manager.get_max_user_titles()
            top_users = self.analysis_domain_service.get_top_users(
                user_activity, limit=max_user_titles
            )

            # 5. Phân tích ngữ nghĩa bằng LLM.
            # LLMAnalyzer có thể tự xử lý việc chuyển đổi dữ liệu.
            topic_enabled = self.config_manager.get_topic_analysis_enabled()
            user_title_enabled = self.config_manager.get_user_title_analysis_enabled()
            golden_quote_enabled = (
                self.config_manager.get_golden_quote_analysis_enabled()
            )
            chat_quality_enabled = (
                self.config_manager.get_chat_quality_analysis_enabled()
            )

            topics = []
            user_titles = []
            golden_quotes = []
            chat_quality_review = None
            total_token_usage = TokenUsage()

            # LLMAnalyzer hiện có thể chỉ nhận format cũ hoặc adapter UnifiedMessage.
            # Tạm chuyển về format cũ để ổn định cho đến khi LLMAnalyzer được refactor.
            legacy_messages = self.statistics_service._convert_to_legacy_dict(
                unified_messages
            )

            unified_msg_origin = (
                f"{platform_id}:GroupMessage:{group_id}" if platform_id else group_id
            )

            if (
                topic_enabled
                or user_title_enabled
                or golden_quote_enabled
                or chat_quality_enabled
            ):
                async with self.llm_semaphore:
                    logger.debug(f"[LLM] Đã vào hàng đợi phân tích (nhóm: {group_id})")
                    (
                        topics,
                        user_titles,
                        golden_quotes,
                        total_token_usage,
                        chat_quality_review,
                    ) = await self.llm_analyzer.analyze_all_concurrent(
                        legacy_messages,
                        user_activity,
                        umo=unified_msg_origin,
                        top_users=top_users,
                        topic_enabled=topic_enabled,
                        user_title_enabled=user_title_enabled,
                        golden_quote_enabled=golden_quote_enabled,
                        chat_quality_enabled=chat_quality_enabled,
                    )

            # Gắn kết quả trở lại
            statistics.golden_quotes = golden_quotes
            statistics.token_usage = total_token_usage

            analysis_result = {
                "statistics": statistics,
                "topics": topics,
                "user_titles": user_titles,
                "user_analysis": user_activity,
                "chat_quality_review": chat_quality_review,
            }

            # 6. Lưu bản tóm tắt (persistence)
            await self.history_manager.save_analysis(group_id, analysis_result)

            # 7. Tạo và gửi báo cáo (application điều phối thao tác gửi).
            # Caller xử lý việc gửi; service chỉ trả kết quả và sản phẩm trực quan.
            return {
                "success": True,
                "analysis_result": analysis_result,
                "messages_count": len(unified_messages),
                "adapter": adapter,
                "group_id": group_id,
                "platform_id": getattr(adapter, "platform_id", platform_id),
            }

    # ----------------------------------------------------------------
    # Use case phân tích gia tăng
    # ----------------------------------------------------------------

    async def execute_incremental_analysis(
        self, group_id: str, platform_id: str | None = None
    ) -> dict[str, Any]:
        """
        Thực thi một use case phân tích gia tăng theo kiến trúc batch cửa sổ trượt.

        Khác với phân tích hằng ngày, mỗi lần chỉ xử lý tin nhắn gần đây,
        trích xuất một số chủ đề và trích dẫn rồi lưu kết quả thành batch độc
        lập trong KV. Danh hiệu thành viên và báo cáo được tạo ở bước cuối.

        Quy trình: lấy adapter và tin nhắn, làm sạch, loại trùng theo timestamp,
        kiểm tra ngưỡng, tính thống kê, phân tích gia tăng bằng LLM, lưu
        ``IncrementalBatch``, cập nhật tiến độ và trả kết quả batch.

        Args:
            group_id: ID nhóm.
            platform_id: ID nền tảng; mặc định dùng nền tảng mặc định.

        Returns:
            Dict chứa success, batch_summary và các thông tin liên quan.
        """
        async with self.group_lock(group_id, "incremental"):
            if not self.incremental_store:
                raise RuntimeError(
                    "Phân tích tăng cường chưa được khởi tạo: thiếu IncrementalStore"
                )

            logger.info(
                f"Bắt đầu phân tích gia tăng: nhóm {group_id}, nền tảng {platform_id or 'mặc định'}"
            )

            # 1. Lấy adapter
            adapter = self.bot_manager.get_adapter(platform_id)
            if not adapter:
                raise ValueError(f"Không tìm thấy adapter cho nền tảng {platform_id}")

            # Kiểm tra nhóm có tắt quyền gửi tin hay không.
            if hasattr(adapter, "is_group_muted"):
                try:
                    if await adapter.is_group_muted(group_id):
                        logger.info(
                            f"Nhóm {group_id} đang tắt quyền gửi tin toàn nhóm hoặc với bot; bỏ qua phân tích gia tăng"
                        )
                        return {"success": False, "reason": "muted"}
                except Exception as e:
                    logger.warning(
                        f"Lỗi khi kiểm tra trạng thái tắt quyền gửi của nhóm {group_id}: {e}"
                    )

            # 2. Lấy tiến độ và xác định số lượng tin nhắn cần truy xuất.
            last_analyzed_ts = await self.incremental_store.get_last_analyzed_timestamp(
                group_id
            )
            days = self.config_manager.get_analysis_days()
            # Giới hạn an toàn giúp bắt kịp tiến độ mà không gây tràn dữ liệu.
            max_count = self.config_manager.get_incremental_safe_limit()

            # 3. Lấy tin nhắn từ điểm tiến độ gần nhất để không bỏ sót khoảng trống.
            raw_messages = await adapter.fetch_messages(
                group_id=group_id,
                days=days,
                max_count=max_count,
                since_ts=last_analyzed_ts,
            )

            if not raw_messages:
                logger.warning(
                    f"Nhóm {group_id} không có tin nhắn hoặc không thể lấy tin trong {days} ngày gần đây"
                )
                return {"success": False, "reason": "no_messages"}

            # 3. Làm sạch tin nhắn
            from ...domain.services.message_cleaner_service import MessageCleanerService

            cleaner = MessageCleanerService()
            bot_self_ids = self.config_manager.get_bot_self_ids()
            if not self.config_manager.get_filter_bot_messages():
                bot_self_ids = []
            logger.debug(
                "filter_bot_messages=%s, bot_self_ids=%s (incremental)",
                self.config_manager.get_filter_bot_messages(),
                bot_self_ids,
            )
            unified_messages = cleaner.clean_messages(
                raw_messages, bot_self_ids=bot_self_ids, filter_commands=True
            )

            # 5. Loại trùng lần hai để chỉ giữ tin nhắn mới sau điểm tiến độ.
            if last_analyzed_ts > 0:
                unified_messages = [
                    msg for msg in unified_messages if msg.timestamp > last_analyzed_ts
                ]

            # 5. Kiểm tra ngưỡng tin nhắn tối thiểu.
            min_messages = self.config_manager.get_incremental_min_messages()
            if len(unified_messages) < min_messages:
                logger.info(
                    f"Phân tích gia tăng nhóm {group_id}: số tin nhắn mới ({len(unified_messages)}) "
                    f"chưa đạt ngưỡng ({min_messages}); bỏ qua lần phân tích này"
                )
                return {"success": False, "reason": "below_threshold"}

            # 6. Tính thống kê cơ bản
            statistics = await asyncio.to_thread(
                self.statistics_service.calculate_group_statistics, unified_messages
            )
            user_activity = await asyncio.to_thread(
                self.analysis_domain_service.analyze_user_activity,
                unified_messages,
                bot_self_ids,
            )

            # Tính phân bố theo giờ của batch này
            hourly_msg_counts, hourly_char_counts = self._compute_hourly_counts(
                unified_messages
            )

            # 7. Phân tích gia tăng bằng LLM (chủ đề và trích dẫn)
            topics_per_batch = self.config_manager.get_incremental_topics_per_batch()
            quotes_per_batch = self.config_manager.get_incremental_quotes_per_batch()

            # Lấy trạng thái các công tắc tính năng
            topic_enabled = self.config_manager.get_topic_analysis_enabled()
            golden_quote_enabled = (
                self.config_manager.get_golden_quote_analysis_enabled()
            )
            chat_quality_enabled = (
                self.config_manager.get_chat_quality_analysis_enabled()
            )

            # Chuyển UnifiedMessage sang format cũ cho analyzer LLM
            legacy_messages = self.statistics_service._convert_to_legacy_dict(
                unified_messages
            )
            unified_msg_origin = (
                f"{platform_id}:GroupMessage:{group_id}" if platform_id else group_id
            )

            topics = []
            golden_quotes = []
            token_usage = TokenUsage()
            chat_quality_review = None

            if topic_enabled or golden_quote_enabled or chat_quality_enabled:
                async with self.llm_semaphore:
                    logger.debug(
                        f"[LLM] Đã vào hàng đợi phân tích gia tăng (nhóm: {group_id})"
                    )
                    (
                        topics,
                        golden_quotes,
                        token_usage,
                        chat_quality_review,
                    ) = await self.llm_analyzer.analyze_incremental_concurrent(
                        legacy_messages,
                        umo=unified_msg_origin,
                        topics_per_batch=topics_per_batch,
                        quotes_per_batch=quotes_per_batch,
                        topic_enabled=topic_enabled,
                        golden_quote_enabled=golden_quote_enabled,
                        chat_quality_enabled=chat_quality_enabled,
                    )

            # 8. Xây dựng IncrementalBatch
            # 8a. Chuyển chủ đề: SummaryTopic -> dict
            new_topics = [
                {
                    "topic": t.topic,
                    "contributors": t.contributors,
                    "detail": t.detail,
                    "contributor_ids": t.contributor_ids,
                }
                for t in topics
            ]

            # 8b. Chuyển trích dẫn: GoldenQuote -> dict
            new_quotes = [
                {
                    "content": q.content,
                    "sender": q.sender,
                    "reason": q.reason,
                    "user_id": q.user_id,
                }
                for q in golden_quotes
            ]

            # 8c. Chuyển mức sử dụng token: TokenUsage -> dict
            token_usage_dict = {
                "prompt_tokens": token_usage.prompt_tokens,
                "completion_tokens": token_usage.completion_tokens,
                "total_tokens": token_usage.total_tokens,
            }

            # 8d. Chuyển thống kê thành viên sang format IncrementalBatch
            user_stats = self._convert_user_activity_for_merge(
                user_activity, unified_messages
            )

            # 8e. Chuyển thống kê biểu cảm: EmojiStatistics -> dict
            emoji_stats = {
                "face_count": statistics.emoji_statistics.face_count,
                "mface_count": statistics.emoji_statistics.mface_count,
                "bface_count": statistics.emoji_statistics.bface_count,
                "sface_count": statistics.emoji_statistics.sface_count,
                "other_emoji_count": statistics.emoji_statistics.other_emoji_count,
                "face_details": statistics.emoji_statistics.face_details,
            }

            # 8f. Chuyển đánh giá chất lượng: QualityReview -> dict
            chat_quality_dict = None
            if chat_quality_review:
                chat_quality_dict = {
                    "title": chat_quality_review.title,
                    "subtitle": chat_quality_review.subtitle,
                    "dimensions": [
                        {
                            "name": d.name,
                            "percentage": d.percentage,
                            "comment": d.comment,
                            "color": d.color,
                        }
                        for d in chat_quality_review.dimensions
                    ],
                    "summary": chat_quality_review.summary,
                }

            # 8g. Lấy ID người tham gia và timestamp tin nhắn cuối
            participant_ids = list({msg.sender_id for msg in unified_messages})
            last_message_timestamp = max(
                (msg.timestamp for msg in unified_messages), default=0
            )

            # 8g. Tính tổng số ký tự của batch
            characters_count = sum(msg.get_text_length() for msg in unified_messages)

            # Xây dựng đối tượng batch
            batch = IncrementalBatch(
                group_id=group_id,
                timestamp=time_mod.time(),
                messages_count=len(unified_messages),
                characters_count=characters_count,
                hourly_msg_counts={str(k): v for k, v in hourly_msg_counts.items()},
                hourly_char_counts={str(k): v for k, v in hourly_char_counts.items()},
                user_stats=user_stats,
                emoji_stats=emoji_stats,
                topics=new_topics,
                golden_quotes=new_quotes,
                token_usage=token_usage_dict,
                chat_quality_review=chat_quality_dict,
                last_message_timestamp=last_message_timestamp,
                participant_ids=participant_ids,
            )

            # 9. Lưu batch và cập nhật timestamp phân tích cuối.
            await self.incremental_store.save_batch(batch)

            # Cập nhật mốc an toàn, không vượt quá hiện tại + 1 phút để tránh timestamp tương lai.
            import time

            safe_now = int(time.time()) + 60
            safe_ts = min(last_message_timestamp, safe_now)

            await self.incremental_store.update_last_analyzed_timestamp(
                group_id, safe_ts
            )

            logger.info(
                f"Hoàn tất phân tích gia tăng nhóm {group_id}: "
                f"tin nhắn batch={len(unified_messages)}, "
                f"chủ đề mới={len(new_topics)}, trích dẫn mới={len(new_quotes)}"
            )

            return {
                "success": True,
                "batch_summary": batch.get_summary(),
                "messages_count": len(unified_messages),
                "group_id": group_id,
                "platform_id": getattr(adapter, "platform_id", platform_id),
            }

    async def execute_incremental_final_report(
        self, group_id: str, platform_id: str | None = None
    ) -> dict[str, Any]:
        """
        Tạo báo cáo cuối từ các batch gia tăng trong cửa sổ trượt.

        Truy vấn batch theo cửa sổ ``analysis_days × 24 giờ``, gộp thành
        ``IncrementalState``, phân tích thêm danh hiệu thành viên rồi tạo
        ``analysis_result`` cùng định dạng với phân tích hằng ngày.

        Quy trình: tính cửa sổ, truy vấn và kiểm tra batch, gộp trạng thái,
        phân tích danh hiệu bằng LLM, dựng kết quả, lưu lịch sử và trả kết quả.

        Args:
            group_id: ID nhóm.
            platform_id: ID nền tảng; mặc định dùng nền tảng mặc định.

        Returns:
            Dict chứa success, analysis_result, adapter và thông tin liên quan.
        """
        async with self.group_lock(group_id, "final"):
            if not self.incremental_store or not self.incremental_merge_service:
                raise RuntimeError(
                    "Phân tích tăng cường chưa được khởi tạo: thiếu "
                    "IncrementalStore hoặc IncrementalMergeService"
                )

            logger.info(
                f"Bắt đầu báo cáo gia tăng cuối: nhóm {group_id}, nền tảng {platform_id or 'mặc định'}"
            )

            # 1. Tính phạm vi cửa sổ trượt
            analysis_days = self.config_manager.get_analysis_days()
            window_end = time_mod.time()
            window_start = window_end - (analysis_days * 24 * 3600)

            # 2. Truy vấn mọi batch trong cửa sổ
            batches = await self.incremental_store.query_batches(
                group_id, window_start, window_end
            )

            # 3. Kiểm tra tính hợp lệ của batch
            if not batches:
                logger.warning(
                    f"Nhóm {group_id} không có dữ liệu gia tăng trong cửa sổ trượt; không thể tạo báo cáo cuối"
                )
                return {"success": False, "reason": "no_incremental_data"}

            # 4. Gộp batch thành IncrementalState
            state = self.incremental_merge_service.merge_batches(
                batches, window_start, window_end
            )

            # 5. Lấy adapter cần cho việc gửi báo cáo
            adapter = self.bot_manager.get_adapter(platform_id)
            if not adapter:
                raise ValueError(f"Không tìm thấy adapter cho nền tảng {platform_id}")

            # Kiểm tra nhóm có tắt quyền gửi tin hay không.
            if hasattr(adapter, "is_group_muted"):
                try:
                    if await adapter.is_group_muted(group_id):
                        logger.info(
                            f"Nhóm {group_id} đang tắt quyền gửi tin toàn nhóm hoặc với bot; bỏ qua báo cáo cuối"
                        )
                        return {"success": False, "reason": "muted"}
                except Exception as e:
                    logger.warning(
                        f"Lỗi khi kiểm tra trạng thái tắt quyền gửi của nhóm {group_id}: {e}"
                    )

            # 6. Chuẩn bị biến cho quá trình phân tích
            user_titles = []
            user_title_enabled = self.config_manager.get_user_title_analysis_enabled()
            unified_msg_origin = (
                f"{platform_id}:GroupMessage:{group_id}" if platform_id else group_id
            )

            if user_title_enabled and state.user_activities:
                max_user_titles = self.config_manager.get_max_user_titles()
                # Lấy các thành viên hàng đầu từ user_activities đã gộp
                top_users = state.get_user_activity_ranking(max_user_titles)

                try:
                    async with self.llm_semaphore:
                        logger.debug(
                            f"[LLM] Đã vào hàng đợi phân tích danh hiệu (nhóm: {group_id})"
                        )
                        (
                            user_titles_result,
                            title_token_usage,
                        ) = await self.llm_analyzer.analyze_user_titles(
                            messages=[],  # Không truyền tin nhắn gốc ở chế độ gia tăng
                            user_activity=state.user_activities,
                            umo=unified_msg_origin,
                            top_users=top_users,
                        )
                    user_titles = user_titles_result

                    # Cộng mức sử dụng token của phân tích danh hiệu vào trạng thái
                    state.total_token_usage["prompt_tokens"] = (
                        state.total_token_usage.get("prompt_tokens", 0)
                        + title_token_usage.prompt_tokens
                    )
                    state.total_token_usage["completion_tokens"] = (
                        state.total_token_usage.get("completion_tokens", 0)
                        + title_token_usage.completion_tokens
                    )
                    state.total_token_usage["total_tokens"] = (
                        state.total_token_usage.get("total_tokens", 0)
                        + title_token_usage.total_tokens
                    )
                except Exception as e:
                    logger.error(
                        f"Phân tích danh hiệu cho báo cáo gia tăng cuối thất bại: {e}",
                        exc_info=True,
                    )

            # 6.5 Tổng hợp chất lượng trò chuyện nếu có đánh giá từ nhiều batch
            if (
                self.config_manager.get_chat_quality_analysis_enabled()
                and state.all_quality_reviews
            ):
                try:
                    async with self.llm_semaphore:
                        logger.debug(
                            f"[LLM] Đã vào hàng đợi tổng hợp chất lượng trò chuyện (nhóm: {group_id})"
                        )
                        (
                            summarized_review,
                            quality_token_usage,
                        ) = await self.llm_analyzer.summarize_quality_reviews(
                            batch_reviews=state.all_quality_reviews,
                            umo=unified_msg_origin,
                        )
                    if summarized_review:
                        # Cập nhật review trong state bằng kết quả tổng hợp.
                        # build_analysis_result sử dụng state.chat_quality_review.
                        state.chat_quality_review = {
                            "title": summarized_review.title,
                            "subtitle": summarized_review.subtitle,
                            "dimensions": [
                                {
                                    "name": d.name,
                                    "percentage": d.percentage,
                                    "comment": d.comment,
                                    "color": d.color,
                                }
                                for d in summarized_review.dimensions
                            ],
                            "summary": summarized_review.summary,
                        }

                        # Cộng dồn token
                        state.total_token_usage["prompt_tokens"] = (
                            state.total_token_usage.get("prompt_tokens", 0)
                            + quality_token_usage.prompt_tokens
                        )
                        state.total_token_usage["completion_tokens"] = (
                            state.total_token_usage.get("completion_tokens", 0)
                            + quality_token_usage.completion_tokens
                        )
                        state.total_token_usage["total_tokens"] = (
                            state.total_token_usage.get("total_tokens", 0)
                            + quality_token_usage.total_tokens
                        )
                except Exception as e:
                    logger.error(
                        f"Tổng hợp chất lượng trò chuyện cho báo cáo cuối thất bại: {e}",
                        exc_info=True,
                    )

            # 7. Xây dựng analysis_result
            analysis_result = self.incremental_merge_service.build_analysis_result(
                state, user_titles
            )

            # 8. Lưu vào history_manager
            await self.history_manager.save_analysis(group_id, analysis_result)

            logger.info(
                f"Hoàn tất báo cáo gia tăng cuối của nhóm {group_id}: "
                f"cửa sổ={state.get_window_date_str()}, "
                f"tin nhắn tích luỹ={state.total_message_count}, "
                f"chủ đề={len(state.topics)}, trích dẫn={len(state.golden_quotes)}, "
                f"batch={state.total_analysis_count}"
            )

            return {
                "success": True,
                "analysis_result": analysis_result,
                "messages_count": state.total_message_count,
                "adapter": adapter,
                "group_id": group_id,
                "platform_id": getattr(adapter, "platform_id", platform_id),
            }

    # ----------------------------------------------------------------
    # Phương thức hỗ trợ
    # ----------------------------------------------------------------

    @staticmethod
    def _compute_hourly_counts(
        messages: list[UnifiedMessage],
    ) -> tuple[dict[int, int], dict[int, int]]:
        """
        Tính phân bố số tin nhắn và ký tự theo giờ từ danh sách tin nhắn.

        Args:
            messages: Danh sách tin nhắn thống nhất.

        Returns:
            Tuple gồm số tin nhắn và số ký tự theo giờ.
        """
        hourly_msg: dict[int, int] = defaultdict(int)
        hourly_char: dict[int, int] = defaultdict(int)

        for msg in messages:
            hour = dt.datetime.fromtimestamp(msg.timestamp).hour
            hourly_msg[hour] += 1
            hourly_char[hour] += msg.get_text_length()

        return dict(hourly_msg), dict(hourly_char)

    @staticmethod
    def _convert_user_activity_for_merge(
        user_activity: Mapping[str, UserActivityStats],
        messages: list[UnifiedMessage],
    ) -> dict[str, dict]:
        """
        Chuyển kết quả của ``AnalysisDomainService.analyze_user_activity()``
        sang format ``user_stats`` mà ``IncrementalBatch`` yêu cầu.

        Ánh xạ chuyển đổi:
        - nickname -> name
        - hours (defaultdict) -> active_hours (list)
        - Thêm last_message_time lấy từ timestamp tin nhắn.

        Args:
            user_activity: Dữ liệu hoạt động do AnalysisDomainService trả về.
            messages: Tin nhắn batch dùng để lấy thời gian cuối của từng thành viên.

        Returns:
            Dict ``user_stats`` theo format IncrementalBatch yêu cầu.
        """
        # Tính trước timestamp tin nhắn cuối của từng thành viên
        user_last_time: dict[str, int] = {}
        for msg in messages:
            current = user_last_time.get(msg.sender_id, 0)
            if msg.timestamp > current:
                user_last_time[msg.sender_id] = msg.timestamp

        result: dict[str, dict] = {}
        for user_id, stats in user_activity.items():
            result[user_id] = {
                "nickname": stats.get("nickname", user_id),
                "message_count": stats.get("message_count", 0),
                "char_count": stats.get("char_count", 0),
                "emoji_count": stats.get("emoji_count", 0),
                "reply_count": stats.get("reply_count", 0),
                "hours": dict(
                    stats.get("hours", {})
                ),  # hours là defaultdict(int), chuyển thành dict
                "last_message_time": user_last_time.get(user_id, 0),
            }

        return result
