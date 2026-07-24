"""
Giao diện dịch vụ phân tích thuộc tầng domain.

Định nghĩa hợp đồng trừu tượng cho chức năng phân tích ngữ nghĩa.
"""

from abc import ABC, abstractmethod

from ..models.data_models import (
    GoldenQuote,
    QualityReview,
    SummaryTopic,
    TokenUsage,
    UserTitle,
)


class IAnalysisProvider(ABC):
    """Giao diện nhà cung cấp dịch vụ phân tích bằng LLM."""

    @abstractmethod
    async def analyze_topics(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[SummaryTopic], TokenUsage]:
        """Phân tích các chủ đề thảo luận."""
        pass

    @abstractmethod
    async def analyze_user_titles(
        self,
        messages: list[dict],
        user_activity: dict,
        umo: str | None = None,
        top_users: list[dict] | None = None,
        session_id: str | None = None,
    ) -> tuple[list[UserTitle], TokenUsage]:
        """Phân tích danh hiệu của thành viên."""
        pass

    @abstractmethod
    async def analyze_golden_quotes(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[GoldenQuote], TokenUsage]:
        """Phân tích các trích dẫn nổi bật."""
        pass

    @abstractmethod
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
        """Phân tích đồng thời tất cả nội dung."""
        pass

    @abstractmethod
    async def analyze_incremental_concurrent(
        self,
        messages: list[dict],
        umo: str | None = None,
        topics_per_batch: int = 3,
        quotes_per_batch: int = 3,
        topic_enabled: bool = True,
        golden_quote_enabled: bool = True,
        chat_quality_enabled: bool = False,
    ) -> tuple[list[SummaryTopic], list[GoldenQuote], TokenUsage, QualityReview | None]:
        """Phân tích đồng thời ở chế độ gia tăng."""
        pass

    @abstractmethod
    async def summarize_quality_reviews(
        self,
        batch_reviews: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[QualityReview | None, TokenUsage]:
        """Tổng hợp nhiều báo cáo chất lượng trò chuyện ở chế độ gia tăng."""
        pass
