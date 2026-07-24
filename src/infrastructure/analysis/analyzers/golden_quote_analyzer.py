"""Module trích xuất và phân tích trích dẫn nổi bật trong nhóm."""

from datetime import datetime

from ....domain.models.data_models import GoldenQuote, TokenUsage
from ....utils.logger import logger
from ...utils.template_utils import render_template
from ..utils import InfoUtils
from ..utils.json_utils import extract_golden_quotes_with_regex
from ..utils.response_validation import validate_golden_quote_items
from ..utils.structured_output_schema import JSONObject, build_golden_quotes_schema
from .base_analyzer import BaseAnalyzer


class GoldenQuoteAnalyzer(BaseAnalyzer[GoldenQuote, list[dict]]):
    """Analyzer trích xuất và phân tích trích dẫn nổi bật."""

    def get_provider_id_key(self) -> str:
        """Lấy tên key cấu hình Provider ID."""
        return "golden_quote_provider_id"

    def get_data_type(self) -> str:
        """Lấy định danh loại dữ liệu."""
        return "Trích dẫn nổi bật"

    def get_max_count(self) -> int:
        """Lấy số trích dẫn tối đa, dùng giá trị ghi đè trong chế độ gia tăng."""
        if self._incremental_max_count is not None:
            return self._incremental_max_count
        return self.config_manager.get_max_golden_quotes()

    def get_response_schema_name(self) -> str:
        return "daily_golden_quotes"

    def get_response_schema(self) -> JSONObject:
        return build_golden_quotes_schema(self.get_max_count())

    def build_prompt(self, data: list[dict]) -> str:
        """
        Xây dựng prompt phân tích trích dẫn.

        Args:
            messages: Danh sách tin nhắn văn bản của nhóm.

        Returns:
            Chuỗi prompt.
        """
        if not data:
            return ""

        # Dùng [user_id] thay nickname để khôi phục chính xác và tránh nhiễu emoji.
        messages_text = "\n".join(
            [f"[{msg['time']}] [{msg['user_id']}]: {msg['content']}" for msg in data]
        )

        max_golden_quotes = self.get_max_count()

        # Đọc template prompt từ cấu hình, mặc định kiểu ``default``.
        prompt_template = self.config_manager.get_golden_quote_analysis_prompt()

        if prompt_template:
            try:
                prompt = render_template(
                    prompt_template,
                    max_golden_quotes=max_golden_quotes,
                    messages_text=messages_text,
                )
                logger.info("Đang dùng prompt phân tích trích dẫn trong cấu hình")
                return prompt
            except Exception as e:
                logger.warning(f"Áp dụng prompt phân tích trích dẫn thất bại: {e}")

        logger.warning("Không tìm thấy cấu hình prompt phân tích trích dẫn hợp lệ")
        return ""

    def extract_with_regex(self, result_text: str, max_count: int) -> list[dict]:
        """
        Trích xuất trích dẫn bằng regex.

        Args:
            result_text: Văn bản phản hồi LLM.
            max_count: Số lượng tối đa.

        Returns:
            Danh sách dữ liệu trích dẫn.
        """
        return extract_golden_quotes_with_regex(result_text, max_count)

    def create_data_objects(self, data_list: list[dict]) -> list[GoldenQuote]:
        """
        Tạo danh sách object trích dẫn.

        Args:
            quotes_data: Danh sách dữ liệu trích dẫn gốc.

        Returns:
            Danh sách object GoldenQuote.
        """
        try:
            quotes = []
            max_quotes = self.get_max_count()

            for quote_data in data_list[:max_quotes]:
                # Đảm bảo định dạng dữ liệu đúng.
                content = quote_data.get("content", "").strip()
                sender = quote_data.get("sender", "").strip()
                reason = quote_data.get("reason", "").strip()

                # Xác thực trường bắt buộc.
                if not content or not sender or not reason:
                    logger.warning(
                        f"Dữ liệu trích dẫn không đầy đủ, bỏ qua: {quote_data}"
                    )
                    continue

                quotes.append(
                    GoldenQuote(content=content, sender=sender, reason=reason)
                )

            return quotes

        except Exception as e:
            logger.error(f"Tạo object trích dẫn thất bại: {e}")
            return []

    def validate_parsed_data(
        self, data_list: list[dict]
    ) -> tuple[bool, list[dict] | None, str | None]:
        return validate_golden_quote_items(data_list)

    async def analyze_golden_quotes(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[GoldenQuote], TokenUsage]:
        """
        Phân tích trích dẫn nổi bật trong nhóm.

        Args:
            messages: Danh sách tin nhắn nhóm.
            umo: Định danh model.
            session_id: ID phiên dùng cho debug mode.

        Returns:
            Tuple danh sách trích dẫn và thống kê token.
        """
        try:
            # Trích xuất tin nhắn văn bản đáng chú ý.
            interesting_messages = self.extract_interesting_messages(messages)

            if not interesting_messages:
                logger.info(
                    "Không có tin nhắn phù hợp để trích dẫn, trả về kết quả rỗng"
                )
                return [], TokenUsage()

            logger.info(
                f"Bắt đầu trích xuất trích dẫn từ {len(interesting_messages)} tin nhắn đáng chú ý"
            )
            quotes, usage = await self.analyze(interesting_messages, umo, session_id)

            # Lập ánh xạ ID sang biệt danh để khôi phục hiển thị.
            id_to_nickname = {}
            for msg in interesting_messages:
                uid = str(msg.get("user_id", ""))
                if uid:
                    id_to_nickname[uid] = msg.get("sender", "")

            # Điền User ID và khôi phục biệt danh người gửi.
            for quote in quotes:
                # quote.sender hiện chứa [user_id] trong prompt; loại ngoặc nếu có.
                potential_id = quote.sender.strip().strip("[]")

                if potential_id in id_to_nickname:
                    quote.user_id = potential_id
                    quote.sender = id_to_nickname[potential_id]
                else:
                    logger.warning(
                        f"[Phân tích trích dẫn] Không khớp User ID: {potential_id}; không thể hiển thị avatar thật"
                    )

            return quotes, usage

        except Exception as e:
            logger.error(f"Phân tích trích dẫn thất bại: {e}")
            return [], TokenUsage()

    def extract_interesting_messages(self, messages: list[dict]) -> list[dict]:
        """
        Trích xuất đoạn tin nhắn có ý nghĩa từ dữ liệu đã làm sạch.

        Args:
            messages: Danh sách tin nhắn legacy đã qua MessageCleaner.

        Returns:
            Danh sách tin nhắn văn bản đã trích xuất.
        """
        interesting_messages = []

        for msg in messages:
            # Lấy tên hiển thị của người gửi.
            sender = msg.get("sender", {})
            nickname = InfoUtils.get_user_nickname(self.config_manager, sender)
            msg_time = datetime.fromtimestamp(msg.get("time", 0)).strftime("%H:%M")

            for content in msg.get("message", []):
                if content.get("type") == "text":
                    text = content.get("data", {}).get("text", "").strip()
                    # Lọc nhiễu quá ngắn hoặc quá dài sau bước cleaner cơ bản.
                    if 2 <= len(text) <= 500:
                        interesting_messages.append(
                            {
                                "sender": nickname,
                                "time": msg_time,
                                "content": text,
                                "user_id": str(sender.get("user_id", "")),
                            }
                        )

        return interesting_messages
