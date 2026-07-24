"""Module phân tích chủ đề trò chuyện nhóm."""

import re
from datetime import datetime

from ....domain.models.data_models import SummaryTopic, TokenUsage
from ....utils.logger import logger
from ...utils.template_utils import render_template
from ..utils import InfoUtils
from ..utils.json_utils import extract_topics_with_regex
from ..utils.response_validation import validate_topic_items
from ..utils.structured_output_schema import JSONObject, build_topics_schema
from .base_analyzer import BaseAnalyzer


class TopicAnalyzer(BaseAnalyzer[SummaryTopic, list[dict]]):
    """Trích xuất và phân tích chủ đề trò chuyện nhóm."""

    def get_provider_id_key(self) -> str:
        """Lấy tên key cấu hình Provider ID."""
        return "topic_provider_id"

    def get_data_type(self) -> str:
        """Lấy định danh loại dữ liệu."""
        return "Chủ đề"

    def get_max_count(self) -> int:
        """Lấy số chủ đề tối đa, ưu tiên giá trị override ở chế độ gia tăng."""
        if self._incremental_max_count is not None:
            return self._incremental_max_count
        return self.config_manager.get_max_topics()

    def get_response_schema_name(self) -> str:
        return "daily_topics"

    def get_response_schema(self) -> JSONObject:
        return build_topics_schema(self.get_max_count())

    def build_prompt(self, data: list[dict]) -> str:
        """
        Xây dựng prompt phân tích chủ đề.

        Args:
            data: Danh sách tin nhắn nhóm.

        Returns:
            Chuỗi prompt.
        """
        # Xác thực định dạng input.
        if not isinstance(data, list):
            logger.error(f"build_prompt cần list nhưng nhận được: {type(data)}")
            return ""

        # Kiểm tra danh sách tin nhắn rỗng.
        if not data:
            logger.warning("build_prompt nhận danh sách tin nhắn rỗng")
            return ""

        # Trích xuất tin nhắn văn bản.
        text_messages = []
        for i, msg in enumerate(data):
            # Bỏ qua msg không phải dict để tránh lỗi thuộc tính get.
            if not isinstance(msg, dict):
                continue

            try:
                sender = msg.get("sender", {})
                # Bỏ qua sender không phải dict.
                if not isinstance(sender, dict):
                    continue

                # Lấy ID người gửi và lọc tin nhắn bot.
                user_id = str(sender.get("user_id", ""))
                bot_self_ids = self.config_manager.get_bot_self_ids()

                # Bỏ qua tin nhắn của bot.
                if bot_self_ids and user_id in [str(uid) for uid in bot_self_ids]:
                    continue

                nickname = InfoUtils.get_user_nickname(self.config_manager, sender)
                msg_time = datetime.fromtimestamp(msg.get("time", 0)).strftime("%H:%M")

                message_list = msg.get("message", [])

                # Nội dung văn bản có thể nằm trong nhiều phần content.
                text_parts = []
                for j, content in enumerate(message_list):
                    if not isinstance(content, dict):
                        continue

                    content_type = content.get("type", "")

                    if content_type == "text":
                        text = content.get("data", {}).get("text", "").strip()
                        if text:
                            text_parts.append(text)
                    elif content_type == "at":
                        # Chuyển mention thành văn bản.
                        at_data = content.get("data", {})
                        # Tương thích trường ID giữa các nền tảng.
                        at_id = at_data.get("id") or at_data.get("user_id")
                        if at_id:
                            at_text = f"@{at_id}"
                            text_parts.append(at_text)
                    elif content_type == "reply":
                        # Thêm nhãn cho tin nhắn trả lời.
                        reply_id = content.get("data", {}).get("id", "")
                        if reply_id:
                            reply_text = f"[Trả lời:{reply_id}]"
                            text_parts.append(reply_text)

                # Gộp mọi phần văn bản.
                combined_text = "".join(text_parts).strip()

                if (
                    combined_text
                    and len(combined_text) > 2
                    and not combined_text.startswith("/")
                ):
                    # Làm sạch nội dung tin nhắn.
                    cleaned_text = combined_text.replace("“", '"').replace("”", '"')
                    cleaned_text = cleaned_text.replace("‘", "'").replace("’", "'")
                    cleaned_text = cleaned_text.replace("\n", " ").replace("\r", " ")
                    cleaned_text = cleaned_text.replace("\t", " ")
                    cleaned_text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned_text)

                    text_messages.append(
                        {
                            "sender": nickname,
                            "time": msg_time,
                            "content": cleaned_text,
                            "user_id": str(user_id),
                        }
                    )
            except Exception as e:
                logger.error(
                    f"build_prompt lỗi khi xử lý tin nhắn thứ {i + 1}: {e}",
                    exc_info=True,
                )
                continue

        if not text_messages:
            logger.warning(
                "build_prompt không trích xuất được tin nhắn hợp lệ; trả về prompt rỗng"
            )
            return ""

        # Dựng văn bản theo định dạng chỉ ID: [HH:MM] [ID người dùng]: nội dung.
        messages_text = "\n".join(
            [
                f"[{msg['time']}] [{msg['user_id']}]: {msg['content']}"
                for msg in text_messages
            ]
        )

        max_topics = self.get_max_count()

        # Đọc template prompt từ cấu hình.
        prompt_template = self.config_manager.get_topic_analysis_prompt()

        if prompt_template:
            try:
                prompt = render_template(
                    prompt_template,
                    max_topics=max_topics,
                    messages_text=messages_text,
                )
                logger.info("Đang dùng prompt phân tích chủ đề trong cấu hình")
                return prompt
            except Exception as e:
                logger.warning(f"Áp dụng prompt phân tích chủ đề thất bại: {e}")

        logger.warning(
            "Không tìm thấy prompt phân tích chủ đề hợp lệ; hãy kiểm tra cấu hình"
        )
        return ""

    def extract_with_regex(self, result_text: str, max_count: int) -> list[dict]:
        """
        Trích xuất thông tin chủ đề bằng regex.

        Args:
            result_text: Văn bản phản hồi LLM.
            max_count: Số chủ đề tối đa.

        Returns:
            Danh sách dữ liệu chủ đề.
        """
        return extract_topics_with_regex(result_text, max_count)

    def create_data_objects(self, data_list: list[dict]) -> list[SummaryTopic]:
        """
        Tạo danh sách object chủ đề.

        Args:
            data_list: Danh sách dữ liệu chủ đề thô.

        Returns:
            Danh sách object SummaryTopic.
        """
        logger.debug(
            f"create_data_objects bắt đầu, số mục input: {len(data_list) if data_list else 0}"
        )
        logger.debug(f"Loại dữ liệu input: {type(data_list)}")

        try:
            topics = []
            max_topics = self.get_max_count()

            logger.debug(f"Đang xử lý tối đa {max_topics} chủ đề đầu")

            for i, topic_data in enumerate(data_list[:max_topics]):
                logger.debug(f"Đang xử lý chủ đề thứ {i + 1}, loại: {type(topic_data)}")

                # Bỏ qua dữ liệu chủ đề không phải dict.
                if not isinstance(topic_data, dict):
                    logger.warning(
                        f"Bỏ qua dữ liệu chủ đề không phải dict: {type(topic_data)} - {topic_data}"
                    )
                    continue

                try:
                    # Chuẩn hoá định dạng dữ liệu.
                    topic_name = topic_data.get("topic", "").strip()
                    contributors = topic_data.get("contributors", [])
                    detail = topic_data.get("detail", "").strip()

                    logger.debug(
                        f"Chủ đề - tên: {topic_name}, người tham gia: {contributors}, chi tiết: {detail[:50]}..."
                    )

                    # Xác thực các trường bắt buộc.
                    if not topic_name or not detail:
                        logger.warning(
                            f"Dữ liệu chủ đề không đầy đủ, bỏ qua: {topic_data}"
                        )
                        continue

                    # Đảm bảo danh sách người tham gia hợp lệ.
                    if not contributors or not isinstance(contributors, list):
                        contributors = ["Thành viên nhóm"]
                    else:
                        # Làm sạch tên người tham gia.
                        contributors = [
                            str(c).strip() for c in contributors if c and str(c).strip()
                        ] or ["Thành viên nhóm"]

                    topics.append(
                        SummaryTopic(
                            topic=topic_name,
                            contributors=contributors[:5],  # Tối đa 5 người tham gia.
                            detail=detail,
                        )
                    )
                except Exception as e:
                    logger.error(
                        f"Lỗi khi xử lý chủ đề thứ {i + 1}: {e}", exc_info=True
                    )
                    continue

            logger.debug(f"create_data_objects hoàn tất, đã tạo {len(topics)} chủ đề")
            return topics

        except Exception as e:
            logger.error(f"Tạo object chủ đề thất bại: {e}", exc_info=True)
            return []

    def validate_parsed_data(
        self, data_list: list[dict]
    ) -> tuple[bool, list[dict] | None, str | None]:
        return validate_topic_items(data_list)

    def extract_text_messages(self, messages: list[dict]) -> list[dict]:
        """
        Trích xuất tin nhắn văn bản đã làm sạch để phân tích chủ đề.

        Args:
            messages: Danh sách tin nhắn legacy đã qua MessageCleaner.

        Returns:
            Danh sách tin nhắn văn bản được trích xuất.
        """
        text_messages = []

        for msg in messages:
            # Lấy tên hiển thị người gửi.
            sender = msg.get("sender", {})
            nickname = InfoUtils.get_user_nickname(self.config_manager, sender)
            msg_time = datetime.fromtimestamp(msg.get("time", 0)).strftime("%H:%M")

            for content in msg.get("message", []):
                if content.get("type") == "text":
                    text = content.get("data", {}).get("text", "").strip()
                    # Nội dung rác cơ bản đã được MessageCleaner xử lý.
                    if text:
                        # Làm sạch bổ sung đơn giản.
                        cleaned_text = text.replace("\n", " ").replace("\r", " ")
                        cleaned_text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned_text)

                        text_messages.append(
                            {
                                "sender": nickname,
                                "time": msg_time,
                                "content": cleaned_text.strip(),
                                "user_id": str(sender.get("user_id", "")),
                            }
                        )
        return text_messages

    async def analyze_topics(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[SummaryTopic], TokenUsage]:
        """
        Phân tích chủ đề trò chuyện nhóm.

        Args:
            messages: Danh sách tin nhắn nhóm.
            umo: Định danh UMO.
            session_id: ID phiên dùng trong debug mode.

        Returns:
            Tuple danh sách chủ đề và thống kê token.
        """
        try:
            logger.debug(
                f"analyze_topics bắt đầu, số tin nhắn: {len(messages) if messages else 0}"
            )
            logger.debug(f"Loại dữ liệu tin nhắn: {type(messages)}")
            if messages:
                logger.debug(
                    f"Loại tin nhắn đầu tiên: {type(messages[0]) if messages else 'không có'}"
                )
                logger.debug(
                    f"Nội dung tin nhắn đầu tiên: {messages[0] if messages else 'không có'}"
                )

            # Kiểm tra tin nhắn văn bản hợp lệ.
            text_messages = self.extract_text_messages(messages)
            logger.debug(f"Đã trích xuất {len(text_messages)} tin nhắn văn bản")

            if not text_messages:
                logger.info("Không có tin nhắn văn bản hợp lệ; trả về kết quả rỗng")
                return [], TokenUsage()

            logger.info(f"Bắt đầu phân tích chủ đề từ {len(text_messages)} tin nhắn")
            logger.debug(f"Loại dữ liệu tin nhắn văn bản: {type(text_messages)}")
            if text_messages:
                logger.debug(
                    f"Loại tin nhắn văn bản đầu tiên: {type(text_messages[0])}"
                )
                logger.debug(f"Nội dung tin nhắn văn bản đầu tiên: {text_messages[0]}")

            # Lập bảng ánh xạ ID sang nickname.
            id_to_nickname = {}
            for msg in text_messages:
                sender = msg.get("sender")
                user_id = msg.get("user_id")
                if sender and user_id:
                    id_to_nickname[user_id] = sender

            # Truyền tin nhắn gốc để build_prompt xử lý.
            topics, usage = await self.analyze(messages, umo, session_id)

            # Hậu xử lý: ánh xạ contributor ID về nickname.
            for topic in topics:
                raw_ids = topic.contributors  # LLM trả về danh sách ID.

                # member_openid QQ Official không chỉ gồm số; chỉ nhận ID đã biết
                # trong batch hoặc ID bot cấu hình thay vì lọc bằng isdigit.
                bot_ids = {str(uid) for uid in self.config_manager.get_bot_self_ids()}
                known_ids = set(id_to_nickname) | bot_ids
                valid_ids = []
                for raw_uid in raw_ids:
                    uid = str(raw_uid).strip().strip("[]")
                    if uid and uid in known_ids and uid not in valid_ids:
                        valid_ids.append(uid)
                topic.contributor_ids = valid_ids

                # Ánh xạ về nickname để hiển thị.
                resolved_names = []
                for uid in valid_ids:
                    # Thử ánh xạ từ batch hiện tại.
                    name = id_to_nickname.get(uid)
                    if not name:
                        # Thử tìm trong cấu hình toàn cục, ví dụ bot.
                        if uid in bot_ids:
                            name = "Bot"
                        else:
                            name = uid  # Fallback to ID
                    resolved_names.append(name)

                topic.contributors = resolved_names

            return topics, usage

        except Exception as e:
            logger.error(f"Phân tích chủ đề thất bại: {e}", exc_info=True)
            return [], TokenUsage()
