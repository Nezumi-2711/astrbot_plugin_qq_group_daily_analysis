"""Module phân tích và đánh giá đa chiều chất lượng trò chuyện nhóm."""

from datetime import datetime

from ....domain.models.data_models import QualityDimension, QualityReview, TokenUsage
from ....utils.logger import logger
from ...utils.template_utils import render_template
from ..utils import InfoUtils
from ..utils.json_utils import extract_quality_with_regex, parse_json_object_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    extract_token_usage,
)
from ..utils.response_validation import validate_quality_review_item
from ..utils.structured_output_schema import JSONObject, build_chat_quality_schema
from .base_analyzer import BaseAnalyzer


class ChatQualityAnalyzer(BaseAnalyzer[QualityReview, list[dict]]):
    """
    Analyzer chất lượng trò chuyện và đánh giá đa chiều.

    Vì kết quả là object JSON thay vì mảng, analyzer ghi đè ``analyze()``, dùng
    ``parse_json_object_response`` và fallback bằng ``extract_quality_with_regex``.
    """

    def get_provider_id_key(self) -> str:
        """Lấy tên key cấu hình Provider ID."""
        return "quality_provider_id"

    def get_data_type(self) -> str:
        """Lấy định danh loại dữ liệu."""
        return "Chất lượng trò chuyện"

    def get_max_count(self) -> int:
        """Lấy số chiều tối đa."""
        return 8

    def get_response_schema_name(self) -> str:
        return "daily_chat_quality_review"

    def get_response_schema(self) -> JSONObject:
        return build_chat_quality_schema(self.get_max_count())

    def build_prompt(self, data: list[dict]) -> str:
        """
        Xây dựng prompt phân tích chất lượng trò chuyện.
        """
        if not data:
            return ""

        # Trích xuất tin nhắn văn bản.
        text_messages = []
        for msg in data:
            if not isinstance(msg, dict):
                continue

            sender = msg.get("sender", {})
            user_id = str(sender.get("user_id", ""))
            bot_self_ids = self.config_manager.get_bot_self_ids()
            if bot_self_ids and user_id in [str(uid) for uid in bot_self_ids]:
                continue

            nickname = InfoUtils.get_user_nickname(self.config_manager, sender)
            msg_time = datetime.fromtimestamp(msg.get("time", 0)).strftime("%H:%M")
            message_list = msg.get("message", [])

            text_parts = []
            for content in message_list:
                if content.get("type") == "text":
                    text = content.get("data", {}).get("text", "").strip()
                    if text:
                        text_parts.append(text)

            combined_text = "".join(text_parts).strip()
            if combined_text and not combined_text.startswith("/"):
                text_messages.append(f"[{msg_time}] [{nickname}]: {combined_text}")

        messages_text = "\n".join(text_messages[:1000])

        prompt_template = self.config_manager.get_quality_analysis_prompt()

        if prompt_template:
            return render_template(prompt_template, messages_text=messages_text)

        prompt_template = """Hãy phân tích lịch sử trò chuyện nhóm sau và đưa ra một bản "đánh giá chất lượng trò chuyện".

    ## Mục tiêu:
    1. **Phân chia chiều**: chia nội dung thành 3-6 chiều 【cấp cao, trừu tượng, khái quát】, ví dụ: lo âu nghề nghiệp, hoạch định tương lai, nghiên cứu giải pháp kỹ thuật, chia sẻ cảm xúc hoặc trò chuyện lan man.
    2. **Tên chiều (name) tuyệt đối không chứa tên thành viên, dự án, lỗi cụ thể hay sự kiện vụn vặt. Tiêu đề phải trừu tượng và ngắn gọn (2-6 từ).**
    3. Ước tính tỷ lệ phần trăm cho từng chiều, tổng không vượt quá 100%.
    4. **Nội dung nhận xét**: viết một câu sắc sảo, hài hước, châm biếm hoặc ấm áp cho từng chiều; đặt chi tiết cụ thể ở đây.
    5. **Biểu hiện toàn nhóm**: đưa ra một câu tổng kết nổi bật.
    6. **Thiết lập chủ đề**: đặt tiêu đề và phụ đề cho báo cáo.

    ## Hướng dẫn phong cách:
    - Ngôn ngữ gần gũi, tự nhiên, có thể dùng tiếng lóng Internet; nhận xét phải chính xác.
    - **Chỉ tên chiều (name) cần trừu tượng; comment và summary có thể cụ thể, sinh động.**

    ## Yêu cầu định dạng:
    Chỉ trả về JSON thuần, không chứa Markdown.

```json
{{
    "title": "Chủ đề trò chuyện hôm nay",
    "subtitle": "Phụ đề",
  "dimensions": [
    {{
    "name": "Tên chiều trừu tượng",
    "percentage": 25,
    "comment": "Nhận xét sắc sảo về chiều này"
    }}
  ],
    "summary": "Một câu tổng kết nổi bật"
}}
```

Lịch sử trò chuyện nhóm:
${messages_text}
"""

        return render_template(prompt_template, messages_text=messages_text)

    def extract_with_regex(self, result_text: str, max_count: int) -> list[dict]:
        """
        Trích xuất dữ liệu chất lượng bằng regex theo giao diện BaseAnalyzer.

        Analyzer này ghi đè ``analyze()`` nên thực tế fallback được gọi từ
        ``analyze_quality()`` qua ``extract_quality_with_regex``.
        """
        return []

    def create_data_objects(self, data_list: list[dict]) -> list[QualityReview]:
        """
        Đáp ứng giao diện trừu tượng BaseAnalyzer; object được tạo trong analyze_quality.
        """
        return []

    def _build_review_from_dict(self, data: dict) -> QualityReview:
        """
        Xây dựng object QualityReview từ dict đã parse.

        Args:
            data: Dict object JSON đã parse.

        Returns:
            Object QualityReview.
        """
        # Đảm bảo tổng tỷ lệ các chiều không vượt 100%.
        total_percentage = sum(
            max(0.0, min(100.0, float(d.get("percentage", 0))))
            for d in data.get("dimensions", [])
        )

        factor = 1.0
        if total_percentage > 100:
            factor = 100.0 / total_percentage

        dimensions = []
        for d in data.get("dimensions", []):
            raw_p = float(d.get("percentage", 0))

            final_p = round(max(0.0, min(100.0, raw_p)) * factor, 1)

            dimensions.append(
                QualityDimension(
                    name=d.get("name", "Không xác định"),
                    percentage=final_p,
                    comment=d.get("comment", ""),
                )
            )

        # Tự phân bổ màu.
        colors = [
            "#607d8b",
            "#2196f3",
            "#f44336",
            "#e91e63",
            "#ff9800",
            "#4caf50",
            "#009688",
            "#9c27b0",
        ]
        for i, d in enumerate(dimensions):
            d.color = colors[i % len(colors)]

        return QualityReview(
            title=data.get("title", "Đánh giá chất lượng trò chuyện"),
            subtitle=data.get("subtitle", "Hôm nay nhóm đã có chuyện gì?"),
            dimensions=dimensions,
            summary=data.get("summary", "Hôm nay cũng là một ngày đầy năng lượng."),
        )

    def _validate_review_payload(
        self, data: dict
    ) -> tuple[bool, dict | None, str | None]:
        return validate_quality_review_item(data)

    async def _retry_parse_quality_object(
        self,
        *,
        original_prompt: str,
        previous_output: str,
        parse_error: str | None,
        umo: str | None,
        system_prompt: str | None,
        base_temperature: float | None,
    ) -> dict | None:
        response_format = self.get_response_format()
        if response_format is None:
            return None

        for idx, temperature in enumerate(
            self.get_schema_retry_temperatures(base_temperature), start=1
        ):
            retry_prompt = self.build_schema_retry_prompt(
                original_prompt=original_prompt,
                previous_output=previous_output,
                parse_error=parse_error,
                attempt_index=idx,
            )
            logger.warning(
                f"Parse có cấu trúc chất lượng trò chuyện thất bại, retry sửa schema "
                f"(attempt={idx}, temperature={temperature:.1f})"
            )
            retry_response = await call_provider_with_retry(
                self.context,
                self.config_manager,
                prompt=retry_prompt,
                umo=umo,
                provider_id_key=self.get_provider_id_key(),
                system_prompt=system_prompt,
                response_format=response_format,
                extra_generate_kwargs={"temperature": temperature},
            )
            if retry_response is None:
                continue

            retry_text = extract_response_text(retry_response)
            if not retry_text:
                continue

            retry_success, retry_parsed_data, _ = parse_json_object_response(
                retry_text, self.get_data_type()
            )
            if retry_success and retry_parsed_data:
                valid, normalized, _ = self._validate_review_payload(retry_parsed_data)
                if valid and normalized:
                    return normalized

            retry_regex_data = extract_quality_with_regex(retry_text)
            if retry_regex_data:
                valid, normalized, _ = self._validate_review_payload(retry_regex_data)
                if valid and normalized:
                    return normalized

        return None

    async def summarize_batch_reviews(
        self,
        batch_reviews: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[QualityReview | None, TokenUsage]:
        """
        Tổng hợp báo cáo chất lượng từ nhiều batch gia tăng thành đánh giá cả ngày.
        """
        if not batch_reviews:
            return None, TokenUsage()

        if len(batch_reviews) == 1:
            return self._build_review_from_dict(batch_reviews[0]), TokenUsage()

        try:
            # Xây dựng prompt tổng hợp.
            reviews_text = ""
            for i, rev in enumerate(batch_reviews):
                title = rev.get("title", "Chưa đặt tên")
                summary = rev.get("summary", "")
                dims = ", ".join(
                    [
                        f"{d.get('name')}({d.get('percentage')}%)"
                        for d in rev.get("dimensions", [])
                    ]
                )
                reviews_text += f"\nBatch {i + 1} [{title}]:\n- Biểu hiện theo chiều: {dims}\n- Tóm tắt cốt lõi: {summary}\n"

            # Dùng prompt tổng hợp trong cấu hình hoặc template mặc định.
            prompt_template = (
                self.config_manager.get_quality_summary_prompt()
                or """Bạn có nhiều ghi chú đánh giá theo batch gia tăng ở các khoảng thời gian trong ngày.
Nhiệm vụ là tổng hợp chúng thành một bản đánh giá chất lượng trò chuyện cuối cùng cho cả ngày.

## Mục tiêu:
1. **Chiều trừu tượng toàn cục**: cân bằng trọng số giữa các batch và rút ra 3-6 chiều cốt lõi cấp cao bao phủ cả ngày, như xu hướng nghề nghiệp/ngành, phát triển kiến trúc kỹ thuật hoặc tâm lý nơi làm việc.
2. **Tên chiều (name) không được chứa chi tiết batch; tiêu đề phải đại diện cho xu hướng cả ngày.**
3. **Hợp nhất phần trăm**: dựa trên tần suất và cường độ để đưa ra phân bố cả ngày, tổng không vượt 100%.
4. **Nhận xét cuối**: viết nhận xét tổng kết nâng cao cho từng chiều, có thể kết hợp chi tiết thú vị từ các batch.
5. **Tổng kết cuối**: đặt tiêu đề, phụ đề và một câu kết mạnh mẽ cho cả ngày.

## Yêu cầu phong cách:
- Chỉ tên chiều (name) cần khái quát và trừu tượng cao.
- Comment và summary phải sinh động, cụ thể và kết nối các điểm đáng nhớ trong ngày.

## Yêu cầu định dạng:
Chỉ trả về JSON thuần, không chứa Markdown.

```json
{{
    "title": "Chủ đề trò chuyện hôm nay",
    "subtitle": "Phụ đề",
  "dimensions": [
    {{
    "name": "Tên chiều khái quát",
    "percentage": 25,
    "comment": "Nhận xét cả ngày cho chiều này"
    }}
  ],
    "summary": "Câu tổng kết nổi bật cho cả ngày"
}}
```
"""
            )
            prompt = render_template(prompt_template, reviews_text=reviews_text)

            # Gọi LLM để tổng hợp.
            system_prompt = await self._build_system_prompt(umo)
            base_temperature = await self._resolve_provider_temperature(
                self.get_provider_id_key(), umo
            )

            # Inject tăng cường persona.
            prompt = self._apply_persona_reinforcement(prompt, system_prompt)

            response = await call_provider_with_retry(
                self.context,
                self.config_manager,
                prompt=prompt,
                umo=umo,
                provider_id_key=self.get_provider_id_key(),
                system_prompt=system_prompt,
                response_format=self.get_response_format(),
            )

            if response is None:
                return None, TokenUsage()

            token_usage_dict = extract_token_usage(response)
            usage = TokenUsage(
                prompt_tokens=token_usage_dict["prompt_tokens"],
                completion_tokens=token_usage_dict["completion_tokens"],
                total_tokens=token_usage_dict["total_tokens"],
            )

            result_text = extract_response_text(response)
            if not result_text:
                return None, usage

            success, parsed_data, error_msg = parse_json_object_response(
                result_text, "tổng hợp chất lượng"
            )

            if success and parsed_data:
                valid, normalized, validation_error = self._validate_review_payload(
                    parsed_data
                )
                if valid and normalized:
                    review = self._build_review_from_dict(normalized)
                    logger.info(
                        f"Tổng hợp chất lượng trò chuyện thành công, parse được {len(review.dimensions)} chiều"
                    )
                    return review, usage
                error_msg = validation_error or error_msg

            repaired_data = await self._retry_parse_quality_object(
                original_prompt=prompt,
                previous_output=result_text,
                parse_error=error_msg,
                umo=umo,
                system_prompt=system_prompt,
                base_temperature=base_temperature,
            )
            if repaired_data:
                review = self._build_review_from_dict(repaired_data)
                logger.info(
                    f"Retry sửa schema tổng hợp chất lượng thành công, parse được {len(review.dimensions)} chiều"
                )
                return review, usage

            # Fallback về batch mới nhất nếu tổng hợp thất bại.
            logger.warning(
                f"Tổng hợp chất lượng thất bại, dùng batch mới nhất: {error_msg}"
            )
            return self._build_review_from_dict(batch_reviews[-1]), usage

        except Exception as e:
            logger.error(f"Lỗi tổng hợp chất lượng trò chuyện: {e}", exc_info=True)
            return self._build_review_from_dict(batch_reviews[-1]), TokenUsage()

    async def analyze_quality(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[QualityReview | None, TokenUsage]:
        """
        Phân tích chất lượng trò chuyện.

        Theo mẫu BaseAnalyzer: xây prompt, gọi LLM, lấy token, parse JSON và fallback regex.
        """
        try:
            # 1. Lấy persona.
            system_prompt = await self._build_system_prompt(umo)
            base_temperature = await self._resolve_provider_temperature(
                self.get_provider_id_key(), umo
            )

            # 2. Xây dựng prompt.
            prompt = self.build_prompt(messages)
            if not prompt:
                return None, TokenUsage()

            # Inject tăng cường persona.
            prompt = self._apply_persona_reinforcement(prompt, system_prompt)

            # 3. Gọi LLM.
            response = await call_provider_with_retry(
                self.context,
                self.config_manager,
                prompt=prompt,
                umo=umo,
                provider_id_key=self.get_provider_id_key(),
                system_prompt=system_prompt,
                response_format=self.get_response_format(),
            )

            if response is None:
                return None, TokenUsage()

            # 4. Trích xuất thống kê token.
            token_usage_dict = extract_token_usage(response)
            usage = TokenUsage(
                prompt_tokens=token_usage_dict["prompt_tokens"],
                completion_tokens=token_usage_dict["completion_tokens"],
                total_tokens=token_usage_dict["total_tokens"],
            )

            # 5. Trích xuất văn bản phản hồi.
            result_text = extract_response_text(response)
            if not result_text:
                return None, usage

            # 6. Parse JSON bằng parse_json_object_response.
            success, parsed_data, error_msg = parse_json_object_response(
                result_text, self.get_data_type()
            )

            if success and parsed_data:
                valid, normalized, validation_error = self._validate_review_payload(
                    parsed_data
                )
                if valid and normalized:
                    review = self._build_review_from_dict(normalized)
                    logger.debug(
                        f"Phân tích chất lượng thành công, parse được {len(review.dimensions)} chiều"
                    )
                    return review, usage
                error_msg = validation_error or error_msg

            regex_data = extract_quality_with_regex(result_text)
            if regex_data:
                valid, normalized, validation_error = self._validate_review_payload(
                    regex_data
                )
                if valid and normalized:
                    review = self._build_review_from_dict(normalized)
                    logger.debug(
                        f"Lần parse có cấu trúc đầu thất bại; regex lấy được {len(review.dimensions)} chiều"
                    )
                    return review, usage
                error_msg = validation_error or error_msg

            repaired_data = await self._retry_parse_quality_object(
                original_prompt=prompt,
                previous_output=result_text,
                parse_error=error_msg,
                umo=umo,
                system_prompt=system_prompt,
                base_temperature=base_temperature,
            )
            if repaired_data:
                review = self._build_review_from_dict(repaired_data)
                logger.debug(
                    f"Retry sửa schema chất lượng thành công, parse được {len(review.dimensions)} chiều"
                )
                return review, usage

            # 7. Mọi cách đều thất bại.
            logger.error(
                f"Phân tích chất lượng thất bại: cả parse JSON và regex đều lỗi: {error_msg}"
            )
            return None, usage

        except Exception as e:
            logger.error(f"Phân tích chất lượng thất bại: {e}", exc_info=True)
            return None, TokenUsage()

    # Override analyze to bridge the base class interface
    async def analyze(
        self,
        data: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[QualityReview], TokenUsage]:
        review, usage = await self.analyze_quality(data, umo, session_id)
        return [review] if review else [], usage
