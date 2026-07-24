"""Công cụ gọi API LLM và thống kê token."""

import asyncio
import random

from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context

from ....utils.logger import logger
from ....utils.resilience import CircuitBreaker, GlobalRateLimiter
from ...config.config_manager import ConfigManager
from .structured_output_schema import JSONObject, JSONValue

_circuit_breakers = {}


def _is_response_format_unsupported_error(error: Exception) -> bool:
    """
    Kiểm tra lỗi tương thích khi provider/gateway không hỗ trợ response_format.
    """
    text = str(error).lower()
    patterns = [
        "response_format",
        "json_schema",
        "unexpected keyword argument",
        "extra fields not permitted",
        "unknown field",
        "not support",
        "not supported",
        "invalid request",
    ]
    return any(pattern in text for pattern in patterns)


def _get_circuit_breaker(provider_id: str) -> CircuitBreaker:
    if provider_id not in _circuit_breakers:
        _circuit_breakers[provider_id] = CircuitBreaker(name=f"provider_{provider_id}")
    return _circuit_breakers[provider_id]


async def _call_provider_stream(
    context: Context, provider_id: str, llm_kwargs: dict[str, JSONValue]
) -> LLMResponse:
    provider = context.get_provider_by_id(provider_id=provider_id)
    if provider is None:
        raise RuntimeError(f"Provider không tồn tại: {provider_id}")

    stream_kwargs = dict(llm_kwargs)
    stream_kwargs.pop("chat_provider_id", None)

    final_resp = None
    content_parts: list[str] = []
    async for resp in provider.text_chat_stream(**stream_kwargs):
        final_resp = resp
        if getattr(resp, "is_chunk", False):
            text = getattr(resp, "completion_text", "")
            if text:
                content_parts.append(text)

    if final_resp is None:
        raise RuntimeError("Lời gọi LLM streaming không trả về phản hồi")

    final_text = extract_response_text(final_resp)
    if final_text and not getattr(final_resp, "is_chunk", False):
        return final_resp

    return LLMResponse(
        role="assistant",
        completion_text="".join(content_parts),
        usage=getattr(final_resp, "usage", None),
        raw_completion=getattr(final_resp, "raw_completion", None),
    )


async def _try_get_provider_id_by_id(
    context, provider_id: str, description: str
) -> str | None:
    """
    Thử xác thực và lấy Provider ID theo ID cấu hình.

    Args:
        context: Context AstrBot.
        provider_id: Provider ID
        description: Mô tả dùng trong log.

    Returns:
        Provider ID hoặc None.
    """
    if not provider_id or not isinstance(provider_id, str) or not provider_id.strip():
        return None

    provider_id = provider_id.strip()
    logger.info(f"Thử dùng {description}: {provider_id}")
    try:
        # Xác thực provider tồn tại.
        provider = context.get_provider_by_id(provider_id=provider_id)
        if provider:
            logger.info(f"✓ Dùng {description}: {provider_id}")
            return provider_id
    except Exception as e:
        logger.warning(f"Không tìm thấy {description} '{provider_id}': {e}")
    return None


async def _try_get_session_provider_id(context, umo: str | None) -> str | None:
    """
    Thử lấy Provider ID của phiên hiện tại.

    Args:
        context: Context AstrBot.
        umo: unified_msg_origin

    Returns:
        Provider ID hoặc None.
    """
    try:
        # Dùng API mới để lấy Provider ID của phiên hiện tại.
        provider_id = await context.get_current_chat_provider_id(umo=umo)
        if provider_id:
            logger.info(f"✓ Dùng provider của phiên hiện tại: {provider_id}")
            return provider_id
    except Exception as e:
        logger.warning(f"Không thể lấy Provider ID của phiên: {e}")
    return None


async def _try_get_first_available_provider_id(context) -> str | None:
    """
    Thử lấy Provider ID khả dụng đầu tiên.

    Args:
        context: Context AstrBot.

    Returns:
        Provider ID hoặc None.
    """
    try:
        all_providers = context.get_all_providers()
        if all_providers and len(all_providers) > 0:
            provider = all_providers[0]
            try:
                meta = provider.meta()
                provider_id = meta.id
                logger.info(f"✓ Dùng provider khả dụng đầu tiên: {provider_id}")
                return provider_id
            except Exception:
                logger.warning("Không thể lấy ID của provider đầu tiên")
    except Exception as e:
        logger.warning(f"Không thể lấy provider nào: {e}")
    return None


async def get_provider_id_with_fallback(
    context: Context,
    config_manager: ConfigManager,
    provider_id_key: str | None,
    umo: str | None = None,
) -> str | None:
    """
    Lấy Provider ID theo key cấu hình với fallback nhiều cấp.

    Thứ tự fallback: provider riêng của tác vụ, provider LLM chính, provider
    của phiên hiện tại, rồi provider khả dụng đầu tiên.

    Args:
        context: Context AstrBot.
        config_manager: Trình quản lý cấu hình.
        provider_id_key: Key provider_id trong cấu hình.
        umo: unified_msg_origin để lấy provider mặc định của phiên.

    Returns:
        Provider ID hoặc None.
    """
    try:
        # Ghi log bắt đầu chọn provider.
        task_desc = provider_id_key if provider_id_key else "tác vụ mặc định"
        logger.info(f"[Chọn provider] Bắt đầu chọn provider cho {task_desc}...")

        # Định nghĩa danh sách chiến lược fallback.
        strategies = []
        strategy_names = []

        # 1. provider_id riêng của tác vụ.
        if provider_id_key:
            getter_method = f"get_{provider_id_key}"
            if hasattr(config_manager, getter_method):
                specific_provider_id = getattr(config_manager, getter_method)()
                if specific_provider_id:
                    strategies.append(
                        lambda pid=specific_provider_id: _try_get_provider_id_by_id(
                            context, pid, f"{provider_id_key} đã cấu hình"
                        )
                    )
                    strategy_names.append(f"1. {provider_id_key} đã cấu hình")

        # 2. provider_id LLM chính.
        main_provider_id = config_manager.get_llm_provider_id()
        if main_provider_id:
            strategies.append(
                lambda pid=main_provider_id: _try_get_provider_id_by_id(
                    context, pid, "provider LLM chính"
                )
            )
            strategy_names.append("2. Provider LLM chính")

        # 3. Provider của phiên hiện tại.
        strategies.append(lambda: _try_get_session_provider_id(context, umo))
        strategy_names.append("3. Provider phiên hiện tại")

        # 4. Provider khả dụng đầu tiên.
        strategies.append(lambda: _try_get_first_available_provider_id(context))
        strategy_names.append("4. Provider khả dụng đầu tiên")

        # Ghi thứ tự chiến lược fallback.
        logger.info(f"[Chọn provider] Thứ tự fallback: {' -> '.join(strategy_names)}")

        # Thử lần lượt từng chiến lược.
        for idx, strategy in enumerate(strategies):
            provider_id = await strategy()
            if provider_id:
                logger.info(
                    f"[Chọn provider] ✓ Thành công với chiến lược #{idx + 1}, Provider ID: {provider_id}"
                )
                return provider_id

        logger.error(
            "[Chọn provider] ✗ Thất bại: không chiến lược nào trả về provider khả dụng"
        )
        return None

    except Exception as e:
        logger.error(f"[Chọn provider] ✗ Lỗi trong quá trình chọn provider: {e}")
        return None


async def call_provider_with_retry(
    context: Context,
    config_manager: ConfigManager,
    prompt: str,
    umo: str | None = None,
    provider_id_key: str | None = None,
    provider_id: str | None = None,
    system_prompt: str | None = None,
    response_format: JSONObject | None = None,
    extra_generate_kwargs: dict[str, JSONValue] | None = None,
) -> LLMResponse | None:
    """
    Gọi provider LLM với retry và backoff, hỗ trợ chọn provider theo cấu hình.

    Args:
        context: Context AstrBot.
        config_manager: Trình quản lý cấu hình.
        prompt: Prompt đầu vào.
        umo: Định danh model cần dùng.
        provider_id_key: Key provider_id để chọn provider riêng.
        system_prompt: System prompt.
        response_format: Ràng buộc output có cấu trúc kiểu OpenAI.
        extra_generate_kwargs: Tham số bổ sung cho context.llm_generate.

    Returns:
        Kết quả LLM hoặc None nếu thất bại.
    """
    # Timeout do provider AstrBot kiểm soát và có thể cấu hình trong WebUI.
    retries = config_manager.get_llm_retries()
    backoff = config_manager.get_llm_backoff()
    enable_streaming_llm_call = config_manager.get_enable_streaming_llm_call()

    # 1. Xác định hàng đợi provider cần thử.
    attempt_queue = []

    # Thử lấy provider được chỉ định.
    specific_provider_id = provider_id
    if not specific_provider_id:
        specific_provider_id = await get_provider_id_with_fallback(
            context, config_manager, provider_id_key, umo
        )
    if specific_provider_id:
        attempt_queue.extend([(specific_provider_id, False)] * retries)

    if not attempt_queue:
        logger.error("Không có provider khả dụng để gọi llm_generate")
        return None

    # 2. Closure thực thi yêu cầu cốt lõi.
    async def _execute_llm_request(
        pid: str, r_format: JSONObject | None
    ) -> LLMResponse:
        cb = _get_circuit_breaker(pid)
        if not cb.allow_request():
            logger.warning(
                f"Circuit breaker của provider {pid} đang mở, bỏ qua yêu cầu"
            )
            raise Exception("Circuit breaker open")

        try:
            async with GlobalRateLimiter.get_instance().semaphore:
                llm_kwargs: dict[str, JSONValue] = {
                    "chat_provider_id": pid,
                    "prompt": prompt,
                }
                if system_prompt is not None:
                    llm_kwargs["system_prompt"] = system_prompt
                if r_format is not None:
                    llm_kwargs["response_format"] = r_format
                if extra_generate_kwargs:
                    llm_kwargs.update(extra_generate_kwargs)

                if enable_streaming_llm_call:
                    resp = await _call_provider_stream(context, pid, llm_kwargs)
                else:
                    resp = await context.llm_generate(**llm_kwargs)
            cb.record_success()
            return resp
        except Exception as err:
            if r_format is not None and _is_response_format_unsupported_error(err):
                raise err
            cb.record_failure()
            raise err

    # 3. Bắt đầu xử lý hàng đợi.
    last_exc = None
    current_response_format = response_format

    # Lưu Provider ID trước để phát hiện chuyển provider.
    previous_pid = None
    # Chỉ resolve fallback sau khi hết lượt retry provider chính.
    needs_fallback = provider_id_key is not None

    for i, (current_pid, is_fallback) in enumerate(attempt_queue):
        attempt_num = i + 1

        # Reset response_format khi chuyển sang provider mới.
        if current_pid != previous_pid:
            current_response_format = response_format
        previous_pid = current_pid

        prefix = "[Fallback] " if is_fallback else "[Gọi LLM] "
        logger.info(
            f"{prefix}Lần thử #{attempt_num} | Provider ID: {current_pid} | "
            f"độ dài prompt={len(prompt) if prompt else 0} ký tự"
        )

        if not prompt or not prompt.strip():
            logger.error("LLM provider: prompt rỗng, không thể gọi")
            return None

        try:
            return await _execute_llm_request(current_pid, current_response_format)

        except Exception as e:
            last_exc = e

            # Xử lý provider không hỗ trợ response_format.
            if (
                current_response_format is not None
                and _is_response_format_unsupported_error(e)
            ):
                logger.warning(
                    f"{prefix}Provider hiện tại có thể không hỗ trợ response_format; tự chuyển sang không ràng buộc schema."
                )
                current_response_format = None
                # Thử lại ngay yêu cầu không có schema trong lượt hiện tại.
                try:
                    return await _execute_llm_request(
                        current_pid, current_response_format
                    )
                except Exception as inner_e:
                    last_exc = inner_e

            logger.warning(f"{prefix}Yêu cầu thất bại: {last_exc}")
            # Chỉ resolve và thêm fallback khi hết retry provider chính.
            if not is_fallback and i == retries - 1 and needs_fallback:
                fallback_provider_id = await get_provider_id_with_fallback(
                    context, config_manager, None, umo
                )
                if (
                    fallback_provider_id
                    and fallback_provider_id != specific_provider_id
                ):
                    for _ in range(retries):
                        attempt_queue.append((fallback_provider_id, True))

            is_last_attempt = i == len(attempt_queue) - 1
            if not is_last_attempt:
                # Exponential backoff with jitter: backoff * (2 ^ (attempt_num - 1)) + random jitter
                sleep_time = backoff * (2 ** (attempt_num - 1)) + random.uniform(0, 1)
                logger.debug(f"Chờ {sleep_time:.2f} giây trước khi thử lại...")
                await asyncio.sleep(sleep_time)

    logger.error(f"Đã dùng hết hàng đợi yêu cầu LLM, lỗi cuối: {last_exc}")
    return None


def extract_token_usage(response) -> dict:
    """
    Trích xuất thống kê token từ phản hồi LLM.

    Args:
        response: Đối tượng phản hồi LLM.

    Returns:
        Dict gồm prompt_tokens, completion_tokens và total_tokens.
    """
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    try:
        # 1. Thử lấy trực tiếp response.usage.
        usage = getattr(response, "usage", None)

        # 2. Thử response.raw_completion.usage để tương thích bản cũ.
        if not usage and hasattr(response, "raw_completion"):
            usage = getattr(response.raw_completion, "usage", None)

        # 3. Xử lý trường hợp response là dict.
        if not usage and isinstance(response, dict):
            usage = response.get("usage")

        if usage:
            # Ưu tiên các trường của TokenUsage AstrBot.
            # AstrBot TokenUsage define: input (prop), output (attr), total (prop)
            if hasattr(usage, "input") and hasattr(usage, "output"):
                token_usage["prompt_tokens"] = getattr(usage, "input", 0) or 0
                token_usage["completion_tokens"] = getattr(usage, "output", 0) or 0
                token_usage["total_tokens"] = getattr(usage, "total", 0) or 0

            # Xử lý usage dạng dict.
            elif isinstance(usage, dict):
                token_usage["prompt_tokens"] = usage.get("prompt_tokens", 0) or 0
                token_usage["completion_tokens"] = (
                    usage.get("completion_tokens", 0) or 0
                )
                token_usage["total_tokens"] = usage.get("total_tokens", 0) or 0

            # Xử lý object chuẩn như OpenAI CompletionUsage.
            else:
                token_usage["prompt_tokens"] = getattr(usage, "prompt_tokens", 0) or 0
                token_usage["completion_tokens"] = (
                    getattr(usage, "completion_tokens", 0) or 0
                )
                token_usage["total_tokens"] = getattr(usage, "total_tokens", 0) or 0

        return token_usage

    except Exception as e:
        logger.error(f"Trích xuất thống kê token thất bại: {e}")
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def extract_response_text(response) -> str:
    """
    Trích xuất nội dung văn bản từ phản hồi LLM.

    Args:
        response: Đối tượng phản hồi LLM.

    Returns:
        Nội dung văn bản phản hồi.
    """
    try:
        if hasattr(response, "completion_text"):
            return response.completion_text
        else:
            return str(response)
    except Exception as e:
        logger.error(f"Trích xuất văn bản phản hồi thất bại: {e}")
        return ""
