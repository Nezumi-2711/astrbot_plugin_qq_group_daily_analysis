"""
Dịch vụ domain gộp dữ liệu phân tích gia tăng.

Phụ trách gộp danh sách ``IncrementalBatch`` thành ``IncrementalState``
và chuyển dữ liệu tích luỹ thành các entity hiện có để tái sử dụng trình
tạo và phân phối báo cáo.

Trách nhiệm chính:
- ``merge_batches``: gộp nhiều batch thành một trạng thái trong cửa sổ trượt
- ``IncrementalState`` → ``GroupStatistics``
- ``IncrementalState`` → ``list[SummaryTopic]``
- ``IncrementalState`` → ``list[GoldenQuote]``
"""

import time

from ...domain.entities.incremental_state import IncrementalBatch, IncrementalState
from ...domain.models.data_models import (
    ActivityVisualization,
    EmojiStatistics,
    GoldenQuote,
    GroupStatistics,
    QualityDimension,
    QualityReview,
    SummaryTopic,
    TokenUsage,
)
from ...utils.logger import logger


class IncrementalMergeService:
    """
    Dịch vụ gộp dữ liệu gia tăng.

    Gộp dữ liệu của nhiều batch trong cửa sổ trượt thành cấu trúc cần cho
    báo cáo, đảm bảo báo cáo cuối ở chế độ gia tăng có cùng định dạng với
    báo cáo phân tích một lần truyền thống.
    """

    def merge_batches(
        self,
        batches: list[IncrementalBatch],
        window_start: float,
        window_end: float,
    ) -> IncrementalState:
        """
        Gộp danh sách batch để xây dựng ``IncrementalState``.

        Duyệt toàn bộ batch, cộng dồn số liệu và loại trùng chủ đề cùng
        trích dẫn để tạo view tổng hợp dùng cho báo cáo.

        Args:
            batches: Danh sách batch trong cửa sổ, tăng dần theo thời gian.
            window_start: Epoch timestamp bắt đầu cửa sổ.
            window_end: Epoch timestamp kết thúc cửa sổ.

        Returns:
            IncrementalState: View tổng hợp sau khi gộp.
        """
        state = IncrementalState(
            group_id=batches[0].group_id if batches else "",
            window_start=window_start,
            window_end=window_end,
            total_analysis_count=len(batches),
            created_at=window_start,
            updated_at=time.time(),
        )

        for batch in batches:
            # Cộng dồn số tin nhắn và ký tự
            state.total_message_count += batch.messages_count
            state.total_character_count += batch.characters_count

            # Gộp phân bố tin nhắn theo giờ bằng cách cộng theo key
            for hour_key, count in batch.hourly_msg_counts.items():
                hour_str = str(hour_key)
                state.hourly_message_counts[hour_str] = (
                    state.hourly_message_counts.get(hour_str, 0) + count
                )

            # Gộp phân bố ký tự theo giờ
            for hour_key, count in batch.hourly_char_counts.items():
                hour_str = str(hour_key)
                state.hourly_character_counts[hour_str] = (
                    state.hourly_character_counts.get(hour_str, 0) + count
                )

            # Gộp thống kê thành viên bằng cách cộng dồn theo người dùng
            for raw_user_id, stats in batch.user_stats.items():
                user_id = str(raw_user_id)
                if user_id not in state.user_activities:
                    state.user_activities[user_id] = {
                        "nickname": stats.get("nickname", stats.get("name", user_id)),
                        "message_count": 0,
                        "char_count": 0,
                        "emoji_count": 0,
                        "reply_count": 0,
                        "hours": {},
                        "last_message_time": 0,
                    }
                existing = state.user_activities[user_id]
                existing["message_count"] += stats.get("message_count", 0)
                existing["char_count"] += stats.get("char_count", 0)
                existing["emoji_count"] += stats.get("emoji_count", 0)
                existing["reply_count"] += stats.get("reply_count", 0)

                # Gộp thống kê theo giờ.
                # Tương thích phiên bản cũ (active_hours là list) và mới (hours là dict).
                batch_hours = stats.get("hours", {})
                if isinstance(batch_hours, dict):
                    # Schema mới: hours là dict {hour: count}
                    for h_str, h_count in batch_hours.items():
                        h_int = int(h_str)
                        existing["hours"][h_int] = (
                            existing["hours"].get(h_int, 0) + h_count
                        )
                else:
                    # Schema cũ: chỉ có active_hours (list)
                    active_hours = stats.get("active_hours", [])
                    for h in active_hours:
                        h_int = int(h)
                        existing["hours"][h_int] = existing["hours"].get(h_int, 0) + 1

                # Lấy thời điểm của tin nhắn cuối lớn hơn
                batch_last = stats.get("last_message_time", 0)
                if batch_last > existing.get("last_message_time", 0):
                    existing["last_message_time"] = batch_last

                # Cập nhật biệt danh bằng giá trị hợp lệ mới nhất
                nickname = stats.get("nickname", stats.get("name", ""))
                if nickname and str(nickname).strip():
                    existing["nickname"] = nickname

            # Gộp thống kê biểu cảm bằng cách cộng theo key
            for emoji_key, count in batch.emoji_stats.items():
                current_val = state.emoji_counts.get(emoji_key, 0)
                if isinstance(count, dict):
                    # Gộp bộ đếm bên trong nếu là dict lồng nhau như face_details
                    if not isinstance(current_val, dict):
                        current_val = {}

                    for sub_key, sub_count in count.items():
                        # Đảm bảo current_val là dict và sub_count là số
                        if isinstance(current_val, dict):
                            current_val[sub_key] = (
                                current_val.get(sub_key, 0) + sub_count
                            )

                    state.emoji_counts[emoji_key] = current_val
                else:
                    # Nếu là số thì cộng trực tiếp
                    if isinstance(current_val, dict):
                        # Trường hợp bất thường: giá trị cũ là dict nhưng giá trị mới là số.
                        # Giữ dict và bỏ qua giá trị số để tương thích khi schema thay đổi.
                        continue

                    state.emoji_counts[emoji_key] = current_val + count

            # Gộp chủ đề và loại trùng
            for topic in batch.topics:
                if not IncrementalState.is_duplicate_topic(topic, state.topics):
                    state.topics.append(topic)

            # Gộp trích dẫn và loại trùng
            for quote in batch.golden_quotes:
                if not IncrementalState.is_duplicate_quote(quote, state.golden_quotes):
                    state.golden_quotes.append(quote)

            # Cộng dồn mức sử dụng token
            for token_key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                state.total_token_usage[token_key] = state.total_token_usage.get(
                    token_key, 0
                ) + batch.token_usage.get(token_key, 0)

            # Gộp ID người tham gia bằng phép hợp
            state.all_participant_ids.update(batch.participant_ids)

            # Thu thập đánh giá chất lượng của mọi batch để tổng hợp cuối
            if batch.chat_quality_review:
                state.all_quality_reviews.append(batch.chat_quality_review)

            # Lưu timestamp tin nhắn phân tích cuối cùng bằng giá trị lớn nhất
            if batch.last_message_timestamp > state.last_analyzed_message_timestamp:
                state.last_analyzed_message_timestamp = batch.last_message_timestamp
                # Dùng đánh giá của batch mới nhất làm fallback nếu chưa có tổng hợp
                if batch.chat_quality_review:
                    state.chat_quality_review = batch.chat_quality_review

        logger.info(
            f"Đã gộp batch: nhóm={state.group_id}, "
            f"cửa sổ={state.get_window_date_str()}, "
            f"số batch={len(batches)}, "
            f"tổng tin nhắn={state.total_message_count}, "
            f"chủ đề={len(state.topics)}, trích dẫn={len(state.golden_quotes)}"
        )

        return state

    def build_final_statistics(self, state: IncrementalState) -> GroupStatistics:
        """
        Xây dựng số liệu thống kê nhóm cuối từ trạng thái gia tăng.

        Ánh xạ dữ liệu tích luỹ trong ``IncrementalState`` sang
        ``GroupStatistics``, gồm phân bố hoạt động 24 giờ, thống kê biểu cảm
        và mức sử dụng token.

        Args:
            state: Trạng thái phân tích gia tăng do ``merge_batches`` tạo ra.

        Returns:
            GroupStatistics: Số liệu có cùng định dạng với phân tích truyền thống.
        """
        # Xây dựng phân bố hoạt động trong 24 giờ
        hourly_activity = {}
        for hour in range(24):
            hour_key = str(hour)
            hourly_activity[hour] = state.hourly_message_counts.get(hour_key, 0)

        # Lấy các khung giờ cao điểm
        peak_hours = state.get_peak_hours(3)

        # Xây dựng bảng xếp hạng hoạt động của thành viên
        user_ranking = state.get_user_activity_ranking(10)

        # Xây dựng dữ liệu trực quan hoá hoạt động
        activity_visualization = ActivityVisualization(
            hourly_activity=hourly_activity,
            daily_activity={state.get_window_date_str(): state.total_message_count},
            user_activity_ranking=user_ranking,
            peak_hours=peak_hours,
            activity_heatmap_data={},
        )

        # Xây dựng thống kê biểu cảm
        emoji_statistics = self._build_emoji_statistics(state)

        # Xây dựng thống kê sử dụng token
        token_usage = TokenUsage(
            prompt_tokens=state.total_token_usage.get("prompt_tokens", 0),
            completion_tokens=state.total_token_usage.get("completion_tokens", 0),
            total_tokens=state.total_token_usage.get("total_tokens", 0),
        )

        # Lấy mô tả khung giờ hoạt động tích cực nhất
        most_active_period = state.get_most_active_period()

        # Chuyển đổi đánh giá chất lượng trò chuyện nếu có
        chat_quality_review = None
        if state.chat_quality_review:
            review_dict = state.chat_quality_review
            dimensions_dict = review_dict.get("dimensions", [])
            dimensions = [
                QualityDimension(
                    name=d.get("name", "Không xác định"),
                    percentage=float(d.get("percentage", 0)),
                    comment=d.get("comment", ""),
                    color=d.get("color", "#607d8b"),
                )
                for d in dimensions_dict
            ]
            chat_quality_review = QualityReview(
                title=review_dict.get("title", "Đánh giá chất lượng trò chuyện"),
                subtitle=review_dict.get("subtitle", "Hôm nay nhóm đã có chuyện gì?"),
                dimensions=dimensions,
                summary=review_dict.get(
                    "summary", "Hôm nay cũng là một ngày đầy năng lượng."
                ),
            )

        statistics = GroupStatistics(
            message_count=state.total_message_count,
            total_characters=state.total_character_count,
            participant_count=len(state.all_participant_ids),
            most_active_period=most_active_period,
            golden_quotes=[],  # Trích dẫn được xây dựng riêng bởi build_quotes_for_report
            emoji_count=emoji_statistics.total_emoji_count,
            emoji_statistics=emoji_statistics,
            activity_visualization=activity_visualization,
            token_usage=token_usage,
            chat_quality_review=chat_quality_review,
        )

        logger.debug(
            f"Đã xây dựng thống kê từ trạng thái gia tăng: "
            f"tin nhắn={state.total_message_count}, "
            f"người tham gia={len(state.all_participant_ids)}, "
            f"chủ đề={len(state.topics)}, "
            f"trích dẫn={len(state.golden_quotes)}"
        )

        return statistics

    def build_topics_for_report(self, state: IncrementalState) -> list[SummaryTopic]:
        """
        Xây dựng danh sách chủ đề dùng cho báo cáo từ trạng thái gia tăng.

        Chuyển các dict chủ đề tích luỹ trong ``IncrementalState`` thành
        danh sách instance ``SummaryTopic``.

        Args:
            state: Trạng thái phân tích gia tăng do ``merge_batches`` tạo ra.

        Returns:
            list[SummaryTopic]: Danh sách chủ đề cùng định dạng với kết quả truyền thống.
        """
        topics = []
        for topic_dict in state.topics:
            topic = SummaryTopic(
                topic=topic_dict.get("topic", "Chủ đề không xác định"),
                contributors=topic_dict.get("contributors", []),
                detail=topic_dict.get("detail", ""),
                contributor_ids=topic_dict.get("contributor_ids", []),
            )
            topics.append(topic)

        logger.debug(f"Đã xây dựng {len(topics)} chủ đề từ trạng thái gia tăng")
        return topics

    def build_quotes_for_report(self, state: IncrementalState) -> list[GoldenQuote]:
        """
        Xây dựng danh sách trích dẫn dùng cho báo cáo từ trạng thái gia tăng.

        Chuyển các dict trích dẫn tích luỹ thành danh sách instance ``GoldenQuote``.

        Args:
            state: Trạng thái phân tích gia tăng do ``merge_batches`` tạo ra.

        Returns:
            list[GoldenQuote]: Danh sách trích dẫn cùng định dạng với kết quả truyền thống.
        """
        quotes = []
        for quote_dict in state.golden_quotes:
            quote = GoldenQuote(
                content=quote_dict.get("content", ""),
                sender=quote_dict.get("sender", ""),
                reason=quote_dict.get("reason", ""),
                user_id=str(quote_dict.get("user_id", "")),
            )
            quotes.append(quote)

        logger.debug(f"Đã xây dựng {len(quotes)} trích dẫn từ trạng thái gia tăng")
        return quotes

    def build_analysis_result(
        self,
        state: IncrementalState,
        user_titles: list | None = None,
    ) -> dict:
        """
        Xây dựng dict ``analysis_result`` hoàn chỉnh từ trạng thái gia tăng.

        Định dạng dict giống hoàn toàn với ``analysis_result`` do
        ``AnalysisApplicationService.execute_daily_analysis()`` trả về và có
        thể truyền trực tiếp cho ``ReportDispatcher``.

        Args:
            state: Trạng thái phân tích gia tăng do ``merge_batches`` tạo ra.
            user_titles: Danh sách danh hiệu do LLM tạo khi lập báo cáo cuối.

        Returns:
            Dict kết quả gồm statistics, topics, user_titles và user_analysis.
        """
        statistics = self.build_final_statistics(state)
        topics = self.build_topics_for_report(state)
        golden_quotes = self.build_quotes_for_report(state)

        # Gắn trích dẫn trở lại statistics để khớp quy trình truyền thống
        statistics.golden_quotes = golden_quotes

        analysis_result = {
            "statistics": statistics,
            "topics": topics,
            "user_titles": user_titles or [],
            "user_analysis": state.user_activities,
            "chat_quality_review": statistics.chat_quality_review,
        }

        logger.info(
            f"Đã xây dựng kết quả phân tích hoàn chỉnh từ trạng thái gia tăng: "
            f"nhóm={state.group_id}, cửa sổ={state.get_window_date_str()}, "
            f"tin nhắn={state.total_message_count}, "
            f"chủ đề={len(topics)}, "
            f"trích dẫn={len(golden_quotes)}, "
            f"batch={state.total_analysis_count}"
        )

        return analysis_result

    def _build_emoji_statistics(self, state: IncrementalState) -> EmojiStatistics:
        """
        Xây dựng thống kê biểu cảm từ trạng thái gia tăng.

        Ánh xạ dict ``emoji_counts`` trong ``IncrementalState`` vào các
        trường của ``EmojiStatistics``.

        Args:
            state: Trạng thái phân tích gia tăng.

        Returns:
            EmojiStatistics: Instance thống kê biểu cảm.
        """
        emoji_counts = state.emoji_counts

        # Trích xuất và kiểm tra kiểu rõ ràng để hỗ trợ Pylance suy luận kiểu
        face_details = emoji_counts.get("face_details")
        if not isinstance(face_details, dict):
            face_details = {}

        return EmojiStatistics(
            face_count=emoji_counts.get("face_count", 0),
            mface_count=emoji_counts.get("mface_count", 0),
            bface_count=emoji_counts.get("bface_count", 0),
            sface_count=emoji_counts.get("sface_count", 0),
            other_emoji_count=emoji_counts.get("other_emoji_count", 0),
            face_details=face_details,
        )
