"""Lớp analyzer cơ sở định nghĩa quy trình và giao diện chung."""

from abc import ABC, abstractmethod
from collections.abc import Sized
from typing import Generic, TypeVar

from ....domain.models.data_models import TokenUsage
from ....shared.constants import PLUGIN_NAME
from ....utils.logger import logger
from ..utils.json_utils import parse_json_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    extract_token_usage,
    get_provider_id_with_fallback,
)
from ..utils.structured_output_schema import JSONObject, build_response_format

TDataObject = TypeVar("TDataObject")
TInputData = TypeVar("TInputData")


class BaseAnalyzer(ABC, Generic[TDataObject, TInputData]):
    """Lớp analyzer trừu tượng với giao diện và quy trình dùng chung."""

    def __init__(self, context, config_manager):
        """
        Khởi tạo analyzer cơ sở.

        Args:
            context: Context AstrBot.
            config_manager: Trình quản lý cấu hình.
        """
        self.context = context
        self.config_manager = config_manager
        # Giới hạn ghi đè cho phân tích gia tăng; None dùng cấu hình mặc định.
        self._incremental_max_count: int | None = None

    def get_provider_id_key(self) -> str | None:
        """
        Lấy tên key cấu hình Provider ID.

        Lớp con có thể ghi đè để chỉ định provider riêng; mặc định dùng provider LLM chính.

        Returns:
            Tên key cấu hình như ``topic_provider_id``.
        """
        return None

    @abstractmethod
    def get_data_type(self) -> str:
        """
        Lấy định danh loại dữ liệu.

        Returns:
            Chuỗi loại dữ liệu.
        """
        pass

    @abstractmethod
    def get_max_count(self) -> int:
        """
        Lấy số lượng trích xuất tối đa.

        Returns:
            Số lượng tối đa.
        """
        pass

    @abstractmethod
    def build_prompt(self, data: TInputData) -> str:
        """
        Xây dựng prompt LLM.

        Args:
            data: Dữ liệu đầu vào.

        Returns:
            Chuỗi prompt.
        """
        pass

    @abstractmethod
    def extract_with_regex(self, result_text: str, max_count: int) -> list[dict]:
        """
        Trích xuất dữ liệu bằng regex.

        Args:
            result_text: Văn bản phản hồi LLM.
            max_count: Số lượng trích xuất tối đa.

        Returns:
            Danh sách dữ liệu đã trích xuất.
        """
        pass

    @abstractmethod
    def create_data_objects(self, data_list: list[dict]) -> list[TDataObject]:
        """
        Tạo danh sách đối tượng dữ liệu.

        Args:
            data_list: Danh sách dữ liệu gốc.

        Returns:
            Danh sách đối tượng dữ liệu.
        """
        pass

    def get_response_schema_name(self) -> str:
        return f"{self.get_data_type()}_output"

    def get_response_schema(self) -> JSONObject | None:
        return None

    def get_response_format(self) -> JSONObject | None:
        schema = self.get_response_schema()
        if not schema:
            return None
        return build_response_format(self.get_response_schema_name(), schema)

    def get_schema_retry_max_attempts(self) -> int:
        """
        Số lần retry tối đa sau khi parse schema thất bại, không gồm lần đầu.
        """
        return 2

    def get_schema_retry_temperatures(
        self, base_temperature: float | None
    ) -> tuple[float, ...]:
        """
        Chuỗi temperature retry sau khi parse schema thất bại.

        Giảm temperature động để tăng tính ổn định của output có cấu trúc.
        """
        attempts = max(0, self.get_schema_retry_max_attempts())
        if attempts == 0:
            return ()

        base = base_temperature if base_temperature is not None else 0.7
        first_retry = max(0.1, min(2.0, base * 0.5))

        temperatures: list[float] = [round(first_retry, 2), 0.0]
        if attempts < len(temperatures):
            temperatures = temperatures[:attempts]

        deduped: list[float] = []
        for temp in temperatures:
            if not deduped or deduped[-1] != temp:
                deduped.append(temp)
        return tuple(deduped)

    async def _resolve_provider_temperature(
        self,
        provider_id_key: str | None,
        umo: str | None,
        provider_id: str | None = None,
    ) -> float | None:
        """
        Thử lấy temperature cơ sở từ cấu hình provider sắp gọi.
        """
        pid = provider_id
        if not pid:
            pid = await get_provider_id_with_fallback(
                self.context,
                self.config_manager,
                provider_id_key,
                umo,
            )
        if not pid:
            return None

        provider = self.context.get_provider_by_id(provider_id=pid)
        if provider is None:
            return None

        provider_config_obj = getattr(provider, "provider_config", None)
        if not isinstance(provider_config_obj, dict):
            return None

        raw_temperature = provider_config_obj.get("temperature")
        if raw_temperature is None:
            custom_extra_body = provider_config_obj.get("custom_extra_body")
            if isinstance(custom_extra_body, dict):
                raw_temperature = custom_extra_body.get("temperature")

        if isinstance(raw_temperature, bool):
            return None

        parsed_temperature: float | None = None
        if isinstance(raw_temperature, (int, float)):
            parsed_temperature = float(raw_temperature)
        elif isinstance(raw_temperature, str):
            try:
                parsed_temperature = float(raw_temperature.strip())
            except ValueError:
                return None

        if parsed_temperature is None:
            return None

        return max(0.0, min(2.0, parsed_temperature))

    def parse_structured_response(
        self, result_text: str
    ) -> tuple[bool, list[dict] | None, str | None]:
        """
        Parse phản hồi có cấu trúc, mặc định là mảng JSON.

        Lớp con có thể ghi đè để tuỳ chỉnh logic parse object.
        """
        return parse_json_response(result_text, self.get_data_type())

    def build_schema_retry_prompt(
        self,
        original_prompt: str,
        previous_output: str,
        parse_error: str | None,
        attempt_index: int,
    ) -> str:
        """
        Xây dựng prompt retry sửa output có cấu trúc.
        """
        err_text = parse_error or "unknown_parse_error"
        return (
            f"{original_prompt}\n\n"
            "[STRUCTURED OUTPUT RETRY]\n"
            f"Attempt: {attempt_index}\n"
            "Your previous output did not satisfy the required strict JSON schema.\n"
            "Return ONLY valid JSON that strictly matches the schema. "
            "Do not include markdown, explanation, or extra text.\n"
            f"Parse error: {err_text}\n"
            "Previous invalid output:\n"
            f"{previous_output}"
        )

    def _try_parse_with_fallback(
        self, result_text: str
    ) -> tuple[bool, list[dict] | None, str | None]:
        """
        Thử parse JSON có cấu trúc trước, sau đó fallback regex nếu thất bại.
        """
        success, parsed_data, error_msg = self.parse_structured_response(result_text)
        if success and parsed_data:
            validated_success, validated_data, validated_error = (
                self.validate_parsed_data(parsed_data)
            )
            if validated_success and validated_data:
                return True, validated_data, None
            error_msg = validated_error or error_msg

        regex_data = self.extract_with_regex(result_text, self.get_max_count())
        if regex_data:
            validated_success, validated_data, validated_error = (
                self.validate_parsed_data(regex_data)
            )
            if validated_success and validated_data:
                logger.info(
                    f"Parse có cấu trúc {self.get_data_type()} thất bại; fallback regex lấy được {len(validated_data)} mục"
                )
                return True, validated_data, None
            error_msg = validated_error or error_msg

        return False, None, error_msg

    def validate_parsed_data(
        self, data_list: list[dict]
    ) -> tuple[bool, list[dict] | None, str | None]:
        """
        Kiểm tra cục bộ lần hai cho kết quả parse, mặc định luôn hợp lệ.

        Lớp con có thể ghi đè bằng kiểm tra Pydantic.
        """
        return True, data_list, None

    def _save_debug_data(self, prompt: str, session_id: str):
        """
        Lưu dữ liệu debug vào tệp.

        Args:
            prompt: Nội dung prompt.
            session_id: ID phiên.
        """
        try:
            from astrbot.api.star import StarTools

            data_path = StarTools.get_data_dir(PLUGIN_NAME) / "debug_data"

            data_path.mkdir(parents=True, exist_ok=True)

            file_name = f"{session_id}_{self.get_data_type()}.txt"
            file_path = data_path / file_name

            logger.info(f"Đang lưu dữ liệu debug vào: {file_path}")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(prompt)

            logger.info(
                f"Đã lưu prompt phân tích {self.get_data_type()} vào {file_path}"
            )

        except Exception as e:
            logger.error(f"Lưu dữ liệu debug thất bại: {e}", exc_info=True)

    def _apply_persona_reinforcement(
        self, prompt: str, system_prompt: str | None
    ) -> str:
        """
        Inject tăng cường persona ở đầu và cuối prompt.

        Không chỉ củng cố giọng điệu mà còn yêu cầu phân tích theo góc nhìn persona.
        """
        if not system_prompt or not system_prompt.strip():
            return prompt

        logger.info(
            f"[Phân tích {self.get_data_type()}] Đã bật persona ở chế độ tăng cường sâu"
        )

        # Xây dựng marker có tính bắt buộc cao hơn.
        persona_content = system_prompt.strip()

        return (
            "【SYSTEM_CORE_IDENTITY_FIXED】\n"
            f"Danh tính hiện tại của bạn đã được hệ thống khởi tạo là:\n{persona_content}\n\n"
            "--- MISSION_DIRECTIVE_START ---\n"
            "⚠️ Yêu cầu cốt lõi: mọi phân tích tiếp theo phải dựa trên 【persona】 ở trên.\n"
            "Điều này bao gồm góc tiếp cận, độ nhạy với dữ liệu, mức sắc sảo/ấm áp khi nhận xét và cách cảm nhận không khí nhóm.\n"
            f"Hãy xử lý tác vụ phân tích ‘{self.get_data_type()}’ sau theo tư duy của persona này:\n\n"
            f"{prompt}\n"
            "--- MISSION_DIRECTIVE_END ---\n\n"
            "【FINAL_IDENTITY_REINFORCEMENT】\n"
            f"1. Bạn không còn là trợ lý AI chung chung mà là nhân vật đã thiết lập ở trên. Đây là lời nhắc lại persona:\n{persona_content}\n Persona này đang quan sát và nhận xét dữ liệu trò chuyện nhóm.\n"
            f"2. Hãy xuất ‘{self.get_data_type()}’ theo ngôi thứ nhất hoặc góc quan sát riêng của nhân vật.\n"
            "3. Kết quả phải thể hiện cá tính của nhân vật; không dùng lời lẽ AI trung lập, khách sáo hoặc rập khuôn.\n"
            "4. ⚠️ Quy tắc định dạng: dù persona có phóng khoáng đến đâu, output cuối phải tuân thủ nghiêm ngặt JSON thuần được yêu cầu trong MISSION_DIRECTIVE. Ngoài JSON, không xuất Markdown hoặc trò chuyện nhập vai bổ sung."
        )

    async def analyze(
        self, data: TInputData, umo: str | None = None, session_id: str | None = None
    ) -> tuple[list[TDataObject], TokenUsage]:
        """
        Quy trình phân tích thống nhất.

        Args:
            data: Dữ liệu đầu vào.
            umo: Định danh model duy nhất.
            session_id: ID phiên dùng cho debug mode.

        Returns:
            Tuple danh sách kết quả và thống kê token.
        """
        try:
            # 1. Xây dựng prompt.
            logger.debug(
                f"Bắt đầu xây dựng prompt {self.get_data_type()}, kiểu dữ liệu đầu vào: {type(data)}"
            )
            data_length = len(data) if isinstance(data, Sized) else "N/A"
            logger.debug(
                f"Độ dài dữ liệu đầu vào {self.get_data_type()}: {data_length}"
            )

            prompt = self.build_prompt(data)
            logger.info(f"Bắt đầu phân tích {self.get_data_type()}, đã xây dựng prompt")
            logger.debug(
                f"Độ dài prompt {self.get_data_type()}: {len(prompt) if prompt else 0}"
            )
            logger.debug(
                f"100 ký tự đầu prompt {self.get_data_type()}: {prompt[:100] if prompt else 'None'}..."
            )

            # Lưu dữ liệu debug.
            debug_mode = self.config_manager.get_debug_mode()
            if debug_mode and session_id and prompt:
                self._save_debug_data(prompt, session_id)
            elif debug_mode and not session_id:
                logger.warning("[Debug] Debug mode enabled but no session_id provided")

            # Kiểm tra prompt rỗng.
            if not prompt or not prompt.strip():
                logger.warning(
                    f"Phân tích {self.get_data_type()}: prompt rỗng hoặc chỉ có khoảng trắng, bỏ qua lời gọi LLM"
                )
                return [], TokenUsage()

            # 2. Gọi LLM bằng provider đã cấu hình.
            provider_id_key = self.get_provider_id_key()

            # Chỉ resolve provider ID một lần để tránh log lặp.
            resolved_provider_id = None
            if provider_id_key:
                resolved_provider_id = await get_provider_id_with_fallback(
                    self.context, self.config_manager, provider_id_key, umo
                )

            base_temperature = await self._resolve_provider_temperature(
                provider_id_key, umo, provider_id=resolved_provider_id
            )

            # Lấy cấu hình persona.
            system_prompt = await self._build_system_prompt(umo)

            # Inject tăng cường persona.
            prompt = self._apply_persona_reinforcement(prompt, system_prompt)

            logger.info(
                f"[Phân tích {self.get_data_type()}] Bắt đầu yêu cầu LLM, umo: {umo}"
            )

            # Ghi thông tin debug.
            if debug_mode:
                logger.debug(
                    f"[Debug] debug_mode={debug_mode}, umo={umo}, session_id={session_id}, prompt_len={len(prompt) if prompt else 0}"
                )

            response = await call_provider_with_retry(
                self.context,
                self.config_manager,
                prompt=prompt,
                umo=umo,
                provider_id_key=provider_id_key,
                provider_id=resolved_provider_id,
                system_prompt=system_prompt,
                response_format=self.get_response_format(),
            )

            if response is None:
                logger.error(
                    f"Phân tích {self.get_data_type()} gọi LLM thất bại: provider trả về None sau retry"
                )
                return [], TokenUsage()

            # 3. Trích xuất thống kê token.
            token_usage_dict = extract_token_usage(response)
            token_usage = TokenUsage(
                prompt_tokens=token_usage_dict["prompt_tokens"],
                completion_tokens=token_usage_dict["completion_tokens"],
                total_tokens=token_usage_dict["total_tokens"],
            )

            # 4. Trích xuất văn bản phản hồi.
            result_text = extract_response_text(response)
            logger.debug(f"Phản hồi gốc {self.get_data_type()}: {result_text[:500]}...")

            # 5. Thử parse có cấu trúc rồi fallback regex.
            success, parsed_data, error_msg = self._try_parse_with_fallback(result_text)

            # 5.1 Chỉ retry sửa schema với temperature giảm khi cả hai cách thất bại.
            if not success and self.get_response_format() is not None:
                temperatures = self.get_schema_retry_temperatures(base_temperature)
                for idx, temperature in enumerate(temperatures, start=1):
                    retry_prompt = self.build_schema_retry_prompt(
                        original_prompt=prompt,
                        previous_output=result_text,
                        parse_error=error_msg,
                        attempt_index=idx,
                    )
                    logger.warning(
                        f"Parse có cấu trúc {self.get_data_type()} thất bại, retry sửa schema "
                        f"(attempt={idx}, temperature={temperature:.1f})"
                    )
                    retry_response = await call_provider_with_retry(
                        self.context,
                        self.config_manager,
                        prompt=retry_prompt,
                        umo=umo,
                        provider_id_key=provider_id_key,
                        system_prompt=system_prompt,
                        response_format=self.get_response_format(),
                        extra_generate_kwargs={"temperature": temperature},
                    )
                    if retry_response is None:
                        continue

                    retry_result_text = extract_response_text(retry_response)
                    if not retry_result_text:
                        continue

                    result_text = retry_result_text
                    retry_success, retry_parsed_data, retry_error_msg = (
                        self._try_parse_with_fallback(retry_result_text)
                    )
                    if retry_success:
                        success = True
                        parsed_data = retry_parsed_data
                        error_msg = None
                        break
                    error_msg = retry_error_msg

            if success and parsed_data:
                # Parse JSON thành công, tạo object dữ liệu.
                data_objects = self.create_data_objects(parsed_data)
                logger.info(
                    f"Phân tích {self.get_data_type()} thành công, parse được {len(data_objects)} mục"
                )
                return data_objects, token_usage

            # 6. Mọi lần thử đều thất bại.
            logger.error(
                f"Phân tích {self.get_data_type()} thất bại: cả parse JSON và fallback regex đều lỗi: {error_msg}"
            )
            return [], token_usage

        except Exception as e:
            logger.error(
                f"Phân tích {self.get_data_type()} thất bại: {e}", exc_info=True
            )
            return [], TokenUsage()

    async def _build_system_prompt(self, umo: str | None) -> str | None:
        """
        Xây dựng system prompt kèm persona của phiên theo thứ tự ưu tiên:
        persona toàn cục của plugin, persona phiên/hội thoại, rồi persona mặc định UMO.

        Args:
            umo: Định danh dùng để xác định context phiên.

        Returns:
            System prompt cuối hoặc None nếu không có.
        """
        # Lấy cấu hình.
        use_specific = self.config_manager.get_use_plugin_specific_persona()
        specific_id = self.config_manager.get_plugin_specific_persona_id()
        keep_original = self.config_manager.get_keep_original_persona()

        # Lấy persona manager cốt lõi của AstrBot.
        persona_mgr = getattr(self.context, "persona_manager", None)
        if persona_mgr is None:
            return None

        persona_prompt = None

        # Ưu tiên 1: persona toàn cục cố định do plugin chỉ định.
        if use_specific and specific_id:
            try:
                persona_obj = await persona_mgr.get_persona(specific_id)
                persona_prompt = (
                    persona_obj.system_prompt
                    if hasattr(persona_obj, "system_prompt")
                    else None
                )
                if persona_prompt:
                    logger.debug(
                        f"Đã áp dụng persona toàn cục bắt buộc của plugin: {specific_id}"
                    )
            except Exception as e:
                logger.warning(
                    f"Lấy persona do plugin chỉ định thất bại (ID: {specific_id}): {e}"
                )

        # Ưu tiên 2: kế thừa persona gốc của phiên/nhóm hiện tại.
        if not persona_prompt and keep_original and umo:
            try:
                # 2.1 Thử lấy Persona ID gắn với phiên trong SharedPreferences.
                from astrbot.api import sp

                session_service_config = await sp.get_async(
                    scope="umo",
                    scope_id=str(umo),
                    key="session_service_config",
                    default={},
                )
                persona_id = (
                    session_service_config.get("persona_id")
                    if session_service_config
                    else None
                )

                if persona_id and persona_id != "[%None]":
                    persona_obj = await persona_mgr.get_persona(persona_id)
                    persona_prompt = (
                        persona_obj.system_prompt
                        if hasattr(persona_obj, "system_prompt")
                        else None
                    )
                    if persona_prompt:
                        logger.debug(
                            f"Đã kế thừa persona được chọn cho phiên: {persona_id}"
                        )

                # 2.2 Nếu phiên chưa gắn persona, thử persona cấp hội thoại.
                if not persona_prompt:
                    conv_mgr = getattr(self.context, "conversation_manager", None)
                    if conv_mgr:
                        curr_conv_id = await conv_mgr.get_curr_conversation_id(umo)
                        if curr_conv_id:
                            conv_obj = await conv_mgr.get_conversation(
                                umo, curr_conv_id
                            )
                            if (
                                conv_obj
                                and conv_obj.persona_id
                                and conv_obj.persona_id != "[%None]"
                            ):
                                persona_obj = await persona_mgr.get_persona(
                                    conv_obj.persona_id
                                )
                                persona_prompt = (
                                    persona_obj.system_prompt
                                    if hasattr(persona_obj, "system_prompt")
                                    else None
                                )
                                if persona_prompt:
                                    logger.debug(
                                        f"Đã kế thừa persona của hội thoại: {conv_obj.persona_id}"
                                    )

                # 2.3 Nếu vẫn chưa có, thử persona mặc định của UMO.
                if not persona_prompt:
                    personality = await persona_mgr.get_default_persona_v3(umo)
                    if isinstance(personality, dict):
                        persona_prompt = personality.get("prompt")
                    else:
                        persona_prompt = getattr(personality, "prompt", None)
                    if persona_prompt:
                        logger.debug("Đã kế thừa persona mặc định của UMO")

            except Exception as e:
                logger.warning(
                    f"Truy ngược persona phân tích thất bại (umo: {umo}): {e}"
                )

        # Kiểm tra kết quả.
        if not isinstance(persona_prompt, str) or not persona_prompt.strip():
            return None

        return persona_prompt.strip()
