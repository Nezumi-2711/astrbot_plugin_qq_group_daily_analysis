"""Module phân tích danh hiệu thành viên và kiểu MBTI."""

from ....domain.models.data_models import TokenUsage, UserTitle
from ....utils.logger import logger
from ...utils.template_utils import render_template
from ..utils.json_utils import extract_user_titles_with_regex
from ..utils.response_validation import validate_user_title_items
from ..utils.structured_output_schema import JSONObject, build_user_titles_schema
from .base_analyzer import BaseAnalyzer


class UserTitleAnalyzer(BaseAnalyzer[UserTitle, dict]):
    """Analyzer phân bổ danh hiệu thành viên và phân tích MBTI."""

    def get_provider_id_key(self) -> str:
        """Lấy tên key cấu hình Provider ID."""
        return "user_title_provider_id"

    def get_data_type(self) -> str:
        """Lấy định danh loại dữ liệu."""
        return "Danh hiệu thành viên"

    def get_max_count(self) -> int:
        """Lấy số danh hiệu thành viên tối đa."""
        return self.config_manager.get_max_user_titles()

    def get_response_schema_name(self) -> str:
        return "daily_user_titles"

    def get_response_schema(self) -> JSONObject:
        return build_user_titles_schema(self.get_max_count())

    def build_prompt(self, data: dict) -> str:
        """
        Xây dựng prompt phân tích danh hiệu thành viên.

        Args:
            user_data: Dict dữ liệu và thống kê thành viên.

        Returns:
            Chuỗi prompt.
        """
        user_summaries = data.get("user_summaries", [])

        if not user_summaries:
            return ""

        # Xây dựng văn bản dữ liệu thành viên.
        users_text = "\n".join(
            [
                f"- {user['name']} (ID:{user['user_id']}): "
                f"{user['message_count']} tin nhắn, trung bình {user['avg_chars']} ký tự, "
                f"tỷ lệ biểu cảm {user['emoji_ratio']}, tỷ lệ chat ban đêm {user['night_ratio']}, "
                f"tỷ lệ trả lời {user['reply_ratio']}"
                for user in user_summaries
            ]
        )

        # Đọc template prompt từ cấu hình, mặc định kiểu ``default``.
        prompt_template = self.config_manager.get_user_title_analysis_prompt()

        if prompt_template:
            try:
                prompt = render_template(prompt_template, users_text=users_text)
                logger.info("Đang dùng prompt phân tích danh hiệu trong cấu hình")
                return prompt
            except Exception as e:
                logger.warning(f"Áp dụng prompt phân tích danh hiệu thất bại: {e}")

        logger.warning("Không tìm thấy cấu hình prompt phân tích danh hiệu hợp lệ")
        return ""

    def extract_with_regex(self, result_text: str, max_count: int) -> list[dict]:
        """
        Trích xuất thông tin danh hiệu bằng regex.

        Args:
            result_text: Văn bản phản hồi LLM.
            max_count: Số lượng tối đa.

        Returns:
            Danh sách dữ liệu danh hiệu.
        """
        return extract_user_titles_with_regex(result_text, max_count)

    def create_data_objects(self, data_list: list[dict]) -> list[UserTitle]:
        """
        Tạo danh sách object danh hiệu.

        Args:
            titles_data: Danh sách dữ liệu danh hiệu gốc.

        Returns:
            Danh sách object UserTitle.
        """
        try:
            titles = []
            max_titles = self.get_max_count()

            for title_data in data_list[:max_titles]:
                # Đảm bảo định dạng dữ liệu đúng.
                name = title_data.get("name", "").strip()
                user_id = title_data.get("user_id")
                title = title_data.get("title", "").strip()
                mbti = title_data.get("mbti", "").strip()
                reason = title_data.get("reason", "").strip()

                # Xác thực trường bắt buộc.
                if not name or not title or not mbti or not reason:
                    logger.warning(
                        f"Dữ liệu danh hiệu không đầy đủ, bỏ qua: {title_data}"
                    )
                    continue

                # Đảm bảo user_id là chuỗi.
                if user_id is not None:
                    user_id = str(user_id)
                else:
                    logger.warning(f"Không tìm thấy user_id, bỏ qua: {title_data}")
                    continue

                titles.append(
                    UserTitle(
                        name=name,
                        user_id=user_id,
                        title=title,
                        mbti=mbti,
                        reason=reason,
                    )
                )

            return titles

        except Exception as e:
            logger.error(f"Tạo object danh hiệu thất bại: {e}")
            return []

    def validate_parsed_data(
        self, data_list: list[dict]
    ) -> tuple[bool, list[dict] | None, str | None]:
        return validate_user_title_items(data_list)

    def prepare_user_data(
        self,
        messages: list[dict],
        user_analysis: dict,
        top_users: list[dict] | None = None,
    ) -> dict:
        """
        Chuẩn bị dữ liệu thành viên.

        Args:
            messages: Danh sách tin nhắn nhóm.
            user_analysis: Thống kê phân tích thành viên.
            top_users: Danh sách thành viên tích cực từ get_top_users.

        Returns:
            Dict dữ liệu thành viên đã chuẩn bị.
        """
        try:
            # Lấy danh sách ID bot để lọc.
            bot_self_ids = self.config_manager.get_bot_self_ids()

            user_summaries = []

            # Chỉ phân tích thành viên tích cực nếu có top_users.
            if top_users:
                logger.info(
                    f"Phân tích danh hiệu cho {len(top_users)} thành viên tích cực do get_top_users lọc"
                )
                target_user_ids = {str(user["user_id"]) for user in top_users}
            else:
                # Tương thích logic cũ: dùng thành viên có ít nhất 5 tin nhắn.
                logger.info(
                    "Không có danh sách tích cực, dùng thành viên có ít nhất 5 tin nhắn"
                )
                target_user_ids = {
                    user_id
                    for user_id, stats in user_analysis.items()
                    if stats["message_count"] >= 5
                }

            for user_id, stats in user_analysis.items():
                user_id_str = str(user_id)
                # MessageCleaner đã lọc bot; đây là lớp phòng vệ thứ hai.
                if bot_self_ids and user_id_str in [str(uid) for uid in bot_self_ids]:
                    continue

                # Chỉ xử lý thành viên tích cực.
                if user_id_str not in target_user_ids:
                    continue

                # Phân tích đặc trưng từ stats đã làm sạch; ưu tiên hours dạng dict.
                hours_data = stats.get("hours")
                if hours_data is None:
                    # Tương thích schema cũ hoặc bản đơn giản.
                    active_hours = stats.get("active_hours", [])
                    hours_data = dict.fromkeys(active_hours, 1)

                # Tính an toàn số tin nhắn ban đêm.
                night_messages = sum(hours_data.get(h, 0) for h in range(6))

                message_count = stats.get("message_count", 0)
                if message_count <= 0:
                    continue

                avg_chars = stats.get("char_count", 0) / message_count

                # Các chiều cần cho danh hiệu.
                user_summaries.append(
                    {
                        "name": stats.get("nickname", stats.get("name", user_id_str)),
                        "user_id": user_id_str,
                        "message_count": message_count,
                        "avg_chars": round(avg_chars, 1),
                        "emoji_ratio": round(
                            stats.get("emoji_count", 0) / message_count, 2
                        ),
                        "night_ratio": round(night_messages / message_count, 2),
                        "reply_ratio": round(
                            stats.get("reply_count", 0) / message_count, 2
                        ),
                    }
                )

            if not user_summaries:
                return {"user_summaries": []}

            # Sắp xếp theo số tin nhắn.
            user_summaries.sort(key=lambda x: x["message_count"], reverse=True)

            return {"user_summaries": user_summaries}

        except Exception as e:
            logger.error(f"Chuẩn bị dữ liệu thành viên thất bại: {e}")
            return {"user_summaries": []}

    async def analyze_user_titles(
        self,
        messages: list[dict],
        user_activity: dict,
        umo: str | None = None,
        top_users: list[dict] | None = None,
        session_id: str | None = None,
    ) -> tuple[list[UserTitle], TokenUsage]:
        """
        Phân tích danh hiệu thành viên.

        Args:
            messages: Danh sách tin nhắn nhóm.
            user_analysis: Thống kê phân tích thành viên.
            umo: Định danh model.
            top_users: Danh sách thành viên tích cực, tuỳ chọn.
            session_id: ID phiên dùng cho debug mode.

        Returns:
            Tuple danh sách danh hiệu và thống kê token.
        """
        try:
            # Chuẩn bị dữ liệu và truyền danh sách thành viên tích cực.
            user_data = self.prepare_user_data(messages, user_activity, top_users)

            if not user_data["user_summaries"]:
                logger.info("Không có thành viên phù hợp, trả về kết quả rỗng")
                return [], TokenUsage()

            logger.info(
                f"Bắt đầu phân tích danh hiệu cho {len(user_data['user_summaries'])} thành viên tích cực"
            )
            return await self.analyze(user_data, umo, session_id)

        except Exception as e:
            logger.error(f"Phân tích danh hiệu thất bại: {e}")
            return [], TokenUsage()
