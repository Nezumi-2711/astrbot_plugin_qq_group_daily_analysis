"""Điều phối analyzer LLM cho chủ đề, danh hiệu và trích dẫn nổi bật."""

import asyncio

from ...domain.models.data_models import (
    GoldenQuote,
    QualityReview,
    SummaryTopic,
    TokenUsage,
    UserTitle,
)
from ...domain.repositories.analysis_repository import IAnalysisProvider
from ...shared.constants import PLUGIN_NAME
from ...utils.logger import logger
from .analyzers.chat_quality_analyzer import ChatQualityAnalyzer
from .analyzers.golden_quote_analyzer import GoldenQuoteAnalyzer
from .analyzers.topic_analyzer import TopicAnalyzer
from .analyzers.user_title_analyzer import UserTitleAnalyzer
from .utils.json_utils import fix_json
from .utils.llm_utils import call_provider_with_retry


class LLMAnalyzer(IAnalysisProvider):
    """
    Analyzer LLM làm điểm vào thống nhất cho các loại phân tích chuyên biệt,
    đồng thời duy trì giao diện tương thích ngược.
    """

    topic_analyzer: TopicAnalyzer
    user_title_analyzer: UserTitleAnalyzer
    golden_quote_analyzer: GoldenQuoteAnalyzer

    def __init__(self, context, config_manager):
        """
        Khởi tạo analyzer LLM.

        Args:
            context: Context AstrBot.
            config_manager: Trình quản lý cấu hình.
        """
        self.context = context
        self.config_manager = config_manager

        # Khởi tạo các analyzer chuyên biệt.
        self.topic_analyzer = TopicAnalyzer(context, config_manager)
        self.user_title_analyzer = UserTitleAnalyzer(context, config_manager)
        self.golden_quote_analyzer = GoldenQuoteAnalyzer(context, config_manager)
        self.chat_quality_analyzer = ChatQualityAnalyzer(context, config_manager)

    @staticmethod
    def _make_session_id(
        session_id: str | None, umo: str | None = None, prefix: str = ""
    ) -> str:
        """Generate a session ID if not already provided."""
        if session_id:
            return session_id
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if umo:
            safe_umo = umo.replace(":", "_")
            return f"{prefix}{timestamp}_{safe_umo}"
        return f"{prefix}{timestamp}"

    async def analyze_topics(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[SummaryTopic], TokenUsage]:
        """
        Phân tích chủ đề bằng LLM qua TopicAnalyzer chuyên biệt.

        Args:
            messages: Danh sách tin nhắn nhóm.
            umo: Định danh duy nhất của model.
            session_id: ID phiên dùng cho debug mode.

        Returns:
            Tuple danh sách chủ đề và thống kê token.
        """
        try:
            session_id = self._make_session_id(session_id, umo)

            logger.info(f"Bắt đầu phân tích chủ đề, session_id: {session_id}")
            return await self.topic_analyzer.analyze_topics(messages, umo, session_id)
        except Exception as e:
            logger.error(f"Phân tích chủ đề thất bại: {e}")
            return [], TokenUsage()

    async def analyze_user_titles(
        self,
        messages: list[dict],
        user_activity: dict,
        umo: str | None = None,
        top_users: list[dict] | None = None,
        session_id: str | None = None,
    ) -> tuple[list[UserTitle], TokenUsage]:
        """
        Phân tích danh hiệu thành viên bằng LLM qua UserTitleAnalyzer.

        Args:
            messages: Danh sách tin nhắn nhóm.
            user_activity: Thống kê hoạt động thành viên.
            umo: Định danh duy nhất của model.
            top_users: Danh sách thành viên tích cực, tuỳ chọn.
            session_id: ID phiên dùng cho debug mode.

        Returns:
            Tuple danh sách danh hiệu và thống kê token.
        """
        try:
            session_id = self._make_session_id(session_id, umo)

            logger.info(f"Bắt đầu phân tích danh hiệu, session_id: {session_id}")
            return await self.user_title_analyzer.analyze_user_titles(
                messages, user_activity, umo, top_users, session_id
            )
        except Exception as e:
            logger.error(f"Phân tích danh hiệu thất bại: {e}")
            return [], TokenUsage()

    async def analyze_golden_quotes(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[GoldenQuote], TokenUsage]:
        """
        Phân tích trích dẫn nổi bật bằng LLM qua GoldenQuoteAnalyzer.

        Args:
            messages: Danh sách tin nhắn nhóm.
            umo: Định danh duy nhất của model.
            session_id: ID phiên dùng cho debug mode.

        Returns:
            Tuple danh sách trích dẫn và thống kê token.
        """
        try:
            session_id = self._make_session_id(session_id, umo)

            logger.info(f"Bắt đầu phân tích trích dẫn, session_id: {session_id}")
            return await self.golden_quote_analyzer.analyze_golden_quotes(
                messages, umo, session_id
            )
        except Exception as e:
            logger.error(f"Phân tích trích dẫn thất bại: {e}")
            return [], TokenUsage()

    async def summarize_quality_reviews(
        self,
        batch_reviews: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[QualityReview | None, TokenUsage]:
        """
        Tổng hợp nhiều báo cáo chất lượng trong chế độ gia tăng.
        """
        return await self.chat_quality_analyzer.summarize_batch_reviews(
            batch_reviews, umo, session_id
        )

    async def analyze_all_concurrent(
        self,
        messages: list[dict],
        user_activity: dict,
        umo: str | None = None,
        top_users: list[dict] | None = None,
        topic_enabled: bool = True,
        user_title_enabled: bool = True,
        golden_quote_enabled: bool = True,
        chat_quality_enabled: bool = False,
    ) -> tuple[
        list[SummaryTopic],
        list[UserTitle],
        list[GoldenQuote],
        TokenUsage,
        QualityReview | None,
    ]:
        """
        Thực thi đồng thời các tác vụ phân tích được bật.

        Args:
            messages: Danh sách tin nhắn nhóm.
            user_activity: Thống kê hoạt động thành viên.
            umo: Định danh model.
            top_users: Danh sách thành viên tích cực.
            topic_enabled: Có bật phân tích chủ đề hay không.
            user_title_enabled: Có bật phân tích danh hiệu hay không.
            golden_quote_enabled: Có bật phân tích trích dẫn hay không.

        Returns:
            Danh sách chủ đề, danh hiệu, trích dẫn và tổng token.
        """
        try:
            session_id = self._make_session_id(None, umo)

            logger.info(
                f"Bắt đầu phân tích đồng thời (chủ đề:{topic_enabled}, danh hiệu:{user_title_enabled}, trích dẫn:{golden_quote_enabled}), session_id: {session_id}"
            )

            # Lưu dữ liệu tin nhắn gốc trong debug mode.
            if self.config_manager.get_debug_mode():
                self._save_debug_messages(messages, session_id)

            # Xây dựng danh sách tác vụ đồng thời.
            tasks = []
            task_names = []

            if topic_enabled:
                tasks.append(
                    self.topic_analyzer.analyze_topics(messages, umo, session_id)
                )
                task_names.append("topic")

            if user_title_enabled:
                tasks.append(
                    self.user_title_analyzer.analyze_user_titles(
                        messages, user_activity, umo, top_users, session_id
                    )
                )
                task_names.append("user_title")

            if golden_quote_enabled:
                tasks.append(
                    self.golden_quote_analyzer.analyze_golden_quotes(
                        messages, umo, session_id
                    )
                )
                task_names.append("golden_quote")

            if chat_quality_enabled:
                tasks.append(
                    self.chat_quality_analyzer.analyze_quality(
                        messages, umo, session_id
                    )
                )
                task_names.append("chat_quality")

            if not tasks:
                return [], [], [], TokenUsage(), None

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Xử lý kết quả.
            topics, topic_usage = [], TokenUsage()
            user_titles, title_usage = [], TokenUsage()
            golden_quotes, quote_usage = [], TokenUsage()
            chat_quality_review = None
            quality_usage = TokenUsage()  # Initialize here

            for i, result in enumerate(results):
                name = task_names[i]
                if isinstance(result, Exception):
                    logger.error(f"Tác vụ phân tích {name} thất bại: {result}")
                    continue

                if name == "topic" and isinstance(result, tuple):
                    topics, topic_usage = result
                elif name == "user_title" and isinstance(result, tuple):
                    user_titles, title_usage = result
                elif name == "golden_quote" and isinstance(result, tuple):
                    golden_quotes, quote_usage = result
                elif name == "chat_quality" and isinstance(result, tuple):
                    chat_quality_review, quality_usage = result
                    if not isinstance(quality_usage, TokenUsage):
                        quality_usage = TokenUsage()

            # Gộp thống kê sử dụng token.
            total_usage = TokenUsage(
                prompt_tokens=topic_usage.prompt_tokens
                + title_usage.prompt_tokens
                + quote_usage.prompt_tokens
                + quality_usage.prompt_tokens,
                completion_tokens=topic_usage.completion_tokens
                + title_usage.completion_tokens
                + quote_usage.completion_tokens
                + quality_usage.completion_tokens,
                total_tokens=topic_usage.total_tokens
                + title_usage.total_tokens
                + quote_usage.total_tokens
                + quality_usage.total_tokens,
            )

            logger.info(
                f"Hoàn tất phân tích đồng thời - chủ đề: {len(topics)}, danh hiệu: {len(user_titles)}, trích dẫn: {len(golden_quotes)}, đánh giá chất lượng: {1 if chat_quality_review else 0}"
            )
            return (
                topics,
                user_titles,
                golden_quotes,
                total_usage,
                chat_quality_review,
            )

        except Exception as e:
            logger.error(f"Phân tích đồng thời thất bại: {e}")
            return [], [], [], TokenUsage(), None

    async def analyze_incremental_concurrent(
        self,
        messages: list[dict],
        umo: str | None = None,
        topics_per_batch: int = 2,
        quotes_per_batch: int = 1,
        topic_enabled: bool = True,
        golden_quote_enabled: bool = True,
        chat_quality_enabled: bool = False,
    ) -> tuple[list[SummaryTopic], list[GoldenQuote], TokenUsage, QualityReview | None]:
        """
        Thực thi đồng thời trong chế độ gia tăng. Chỉ phân tích chủ đề,
        trích dẫn và chất lượng; danh hiệu được xử lý khi tạo báo cáo cuối.

        Args:
            messages: Tin nhắn nhóm của lần phân tích gia tăng.
            umo: Định danh model.
            topics_per_batch: Số chủ đề tối đa trong batch.
            quotes_per_batch: Số trích dẫn tối đa trong batch.
            topic_enabled: Có bật phân tích chủ đề hay không.
            golden_quote_enabled: Có bật phân tích trích dẫn hay không.

        Returns:
            Danh sách chủ đề, trích dẫn và tổng token.
        """
        try:
            session_id = self._make_session_id(None, umo, "incr_")

            logger.info(
                f"Bắt đầu phân tích gia tăng đồng thời (chủ đề:{topic_enabled}/{topics_per_batch}, trích dẫn:{golden_quote_enabled}/{quotes_per_batch}, chất lượng:{chat_quality_enabled}), "
                f"tin nhắn: {len(messages)}, session_id: {session_id}"
            )

            # Lưu dữ liệu tin nhắn gốc trong debug mode.
            if self.config_manager.get_debug_mode():
                self._save_debug_messages(messages, session_id)

            # Thiết lập giới hạn ghi đè cho chế độ gia tăng.
            self.topic_analyzer._incremental_max_count = topics_per_batch
            self.golden_quote_analyzer._incremental_max_count = quotes_per_batch

            try:
                # Xây dựng tác vụ đồng thời, không gồm danh hiệu thành viên.
                tasks = []
                task_names = []

                if topic_enabled:
                    tasks.append(
                        self.topic_analyzer.analyze_topics(messages, umo, session_id)
                    )
                    task_names.append("topic")

                if golden_quote_enabled:
                    tasks.append(
                        self.golden_quote_analyzer.analyze_golden_quotes(
                            messages, umo, session_id
                        )
                    )
                    task_names.append("golden_quote")

                if chat_quality_enabled:
                    tasks.append(
                        self.chat_quality_analyzer.analyze_quality(
                            messages, umo, session_id
                        )
                    )
                    task_names.append("chat_quality")

                if not tasks:
                    return [], [], TokenUsage(), None

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Xử lý kết quả.
                topics, topic_usage = [], TokenUsage()
                golden_quotes, quote_usage = [], TokenUsage()
                chat_quality_review = None
                quality_usage = TokenUsage()

                for i, result in enumerate(results):
                    name = task_names[i]
                    if isinstance(result, Exception):
                        logger.error(f"Phân tích gia tăng {name} thất bại: {result}")
                        continue

                    if name == "topic" and isinstance(result, tuple):
                        topics, topic_usage = result
                    elif name == "golden_quote" and isinstance(result, tuple):
                        golden_quotes, quote_usage = result
                    elif name == "chat_quality" and isinstance(result, tuple):
                        chat_quality_review, quality_usage = result
                        if not isinstance(quality_usage, TokenUsage):
                            quality_usage = TokenUsage()

                # Gộp thống kê sử dụng token.
                total_usage = TokenUsage(
                    prompt_tokens=topic_usage.prompt_tokens
                    + quote_usage.prompt_tokens
                    + quality_usage.prompt_tokens,
                    completion_tokens=topic_usage.completion_tokens
                    + quote_usage.completion_tokens
                    + quality_usage.completion_tokens,
                    total_tokens=topic_usage.total_tokens
                    + quote_usage.total_tokens
                    + quality_usage.total_tokens,
                )

                logger.info(
                    f"Hoàn tất phân tích gia tăng đồng thời - chủ đề: {len(topics)}, trích dẫn: {len(golden_quotes)}, đánh giá chất lượng: {1 if chat_quality_review else 0}, "
                    f"token: {total_usage.total_tokens}"
                )
                return topics, golden_quotes, total_usage, chat_quality_review

            finally:
                # Luôn khôi phục giới hạn ban đầu dù thành công hay thất bại.
                self.topic_analyzer._incremental_max_count = None
                self.golden_quote_analyzer._incremental_max_count = None

        except Exception as e:
            logger.error(f"Phân tích gia tăng đồng thời thất bại: {e}", exc_info=True)
            return [], [], TokenUsage(), None

    def _save_debug_messages(self, messages: list[dict], session_id: str):
        """
        Lưu dữ liệu tin nhắn debug vào tệp.

        Args:
            messages: Danh sách tin nhắn nhóm.
            session_id: ID phiên.
        """
        try:
            import json

            from astrbot.api.star import StarTools

            debug_dir = StarTools.get_data_dir(PLUGIN_NAME) / "debug_data"
            debug_dir.mkdir(parents=True, exist_ok=True)

            msg_file_path = debug_dir / f"{session_id}_messages.json"
            with open(msg_file_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # Phương thức tương thích ngược, giữ cách gọi cũ.
    async def _call_provider_with_retry(
        self,
        provider,
        prompt: str,
        umo: str | None = None,
        provider_id_key: str | None = None,
    ):
        """
        Phương thức gọi LLM tương thích ngược, uỷ quyền cho llm_utils.

        Args:
            provider: Provider LLM hoặc None; đã deprecated.
            prompt: Prompt đầu vào.
            umo: Định danh model cần dùng.
            provider_id_key: Tên key provider_id tuỳ chọn trong cấu hình.

        Returns:
            Kết quả do LLM tạo.
        """
        return await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt,
            umo,
            provider_id_key,
        )

    def _fix_json(self, text: str) -> str:
        """
        Phương thức sửa JSON tương thích ngược, uỷ quyền cho json_utils.

        Args:
            text: Văn bản JSON cần sửa.

        Returns:
            Văn bản JSON sau khi sửa.
        """
        return fix_json(text)
