"""Công cụ phân tích, sửa JSON và trích xuất bằng regex."""

import json
import re
from typing import Any

from ....utils.logger import logger


def fix_json(text: str) -> str:
    """
    Sửa lỗi định dạng JSON, bao gồm thay thế dấu câu tiếng Trung.

    Args:
        text: Văn bản JSON cần sửa.

    Returns:
        Văn bản JSON sau khi sửa.
    """
    try:
        # 1. Xoá marker code block Markdown.
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*$", "", text)

        # 2. Làm sạch cơ bản.
        text = text.replace("\n", " ").replace("\r", " ")
        text = re.sub(r"\s+", " ", text)

        # 3. Thay dấu câu tiếng Trung bằng dấu câu ASCII để sửa JSON.
        # Dấu ngoặc kép tiếng Trung -> dấu ngoặc kép ASCII.
        text = text.replace("“", '"').replace("”", '"')
        text = text.replace("‘", "'").replace("’", "'")
        # Dấu phẩy tiếng Trung -> dấu phẩy ASCII.
        text = text.replace("，", ",")
        # Dấu hai chấm tiếng Trung -> dấu hai chấm ASCII.
        text = text.replace("：", ":")
        # Dấu ngoặc tiếng Trung -> dấu ngoặc ASCII.
        text = text.replace("（", "(").replace("）", ")")
        text = text.replace("【", "[").replace("】", "]")

        # 4. Xử lý ký tự đặc biệt trong chuỗi.
        # Escape dấu ngoặc kép bên trong chuỗi.
        def escape_quotes_in_strings(match):
            content = match.group(1)
            # Escape dấu ngoặc kép bên trong.
            content = content.replace('"', '\\"')
            return f'"{content}"'

        # Xử lý dấu ngoặc kép trong giá trị trường trước.
        text = re.sub(r'"([^"]*(?:"[^"]*)*)"', escape_quotes_in_strings, text)

        # 5. Sửa JSON bị cắt ngắn.
        if not text.endswith("]"):
            last_complete = text.rfind("}")
            if last_complete > 0:
                text = text[: last_complete + 1] + "]"

        # 6. Sửa các lỗi định dạng JSON phổ biến.
        # 1. Bổ sung dấu phẩy bị thiếu.
        text = re.sub(r"}\s*{", "}, {", text)

        # 2. Đảm bảo tên trường có dấu ngoặc kép mà không phá giá trị chuỗi.
        def quote_field_names(match):
            prefix = match.group(1)
            key = match.group(2)
            return f'{prefix}"{key}":'

        # Chỉ khớp tên trường sau { hoặc , để tránh khớp nhầm trong giá trị.
        text = re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:", quote_field_names, text)

        # 3. Xoá dấu phẩy thừa.
        text = re.sub(r",\s*}", "}", text)
        text = re.sub(r",\s*]", "]", text)

        return text.strip()

    except Exception as e:
        logger.error(f"Sửa JSON thất bại: {e}")
        return text


def _parse_json_with_pattern(
    result_text: str, pattern: str, data_type: str, expected_type_name: str = "dữ liệu"
) -> tuple[bool, Any, str | None]:
    """
    Logic phân tích JSON nội bộ: trích xuất, parse trực tiếp và thử lại sau sửa.
    """
    fixed_json_text = None
    try:
        # 1. Làm sạch cơ bản: xoá marker code block Markdown.
        clean_text = result_text.strip()
        clean_text = re.sub(r"```(?:json)?\s*", "", clean_text)
        clean_text = re.sub(r"```\s*$", "", clean_text)

        # 2. Trích xuất phần JSON.
        json_match = re.search(pattern, clean_text, re.DOTALL)
        if not json_match:
            error_msg = (
                f"Không tìm thấy JSON {expected_type_name} trong phản hồi {data_type}"
            )
            logger.warning(error_msg)
            return False, None, error_msg

        json_text = json_match.group()
        logger.debug(f"JSON gốc của phân tích {data_type}: {json_text[:500]}...")

        # 3. Thử parse trực tiếp.
        try:
            data = json.loads(json_text)
            count_info = f", gồm {len(data)} mục" if isinstance(data, list) else ""
            logger.info(f"Parse trực tiếp {data_type} thành công{count_info}")
            return True, data, None
        except json.JSONDecodeError:
            logger.debug(f"Parse trực tiếp {data_type} thất bại, thử sửa JSON...")

        # 4. Thử lại sau khi sửa.
        fixed_json_text = fix_json(json_text)
        # Trích xuất lại vì fix_json có thể thay đổi cấu trúc văn bản.
        fixed_match = re.search(pattern, fixed_json_text, re.DOTALL)
        if fixed_match:
            try:
                data = json.loads(fixed_match.group())
                count_info = f", gồm {len(data)} mục" if isinstance(data, list) else ""
                logger.info(f"Parse {data_type} thành công sau khi sửa{count_info}")
                return True, data, None
            except json.JSONDecodeError as e:
                error_msg = f"Parse JSON {data_type} vẫn thất bại sau khi sửa: {e}"
                logger.warning(error_msg)
                return False, None, error_msg

        error_msg = (
            f"Không tìm thấy JSON {expected_type_name} của {data_type} sau khi sửa"
        )
        return False, None, error_msg

    except Exception as e:
        error_msg = f"Lỗi parse {data_type}: {e}"
        logger.error(error_msg)
        return False, None, error_msg


def parse_json_response(
    result_text: str, data_type: str
) -> tuple[bool, list[dict] | None, str | None]:
    """
    Phương thức parse thống nhất cho phản hồi mảng JSON.
    """
    return _parse_json_with_pattern(
        result_text, r"\[.*\]", data_type, expected_type_name="mảng"
    )


def parse_json_object_response(
    result_text: str, data_type: str
) -> tuple[bool, dict | None, str | None]:
    """
    Phương thức parse thống nhất cho phản hồi object JSON.
    """
    return _parse_json_with_pattern(
        result_text, r"\{.*\}", data_type, expected_type_name="đối tượng"
    )


def _clean_json_string(text: str) -> str:
    """
    Làm sạch ký tự escape trong chuỗi JSON sau khi trích xuất bằng regex.
    """
    return text.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")


def extract_topics_with_regex(result_text: str, max_topics: int) -> list[dict]:
    """
    Trích xuất thông tin chủ đề bằng regex.

    Args:
        result_text: Văn bản cần trích xuất.
        max_topics: Số chủ đề tối đa.

    Returns:
        Danh sách dữ liệu chủ đề.
    """
    try:
        # Regex mạnh hơn để xử lý ký tự escape và khớp object chủ đề hoàn chỉnh.
        topic_pattern = r'\{\s*"topic":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"contributors":\s*\[(.*?)\],?\s*"detail":\s*"([^"]*(?:\\.[^"]*)*)"\s*\}'
        matches = re.findall(topic_pattern, result_text, re.DOTALL)

        if not matches:
            # Thử pattern linh hoạt hơn.
            topic_pattern = r'"topic":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"contributors":\s*\[(.*?)\][^}]*"detail":\s*"([^"]*(?:\\.[^"]*)*)"'
            matches = re.findall(topic_pattern, result_text, re.DOTALL)

        topics = []
        for match in matches[:max_topics]:
            topic_name = match[0].strip()
            contributors_str = match[1].strip()
            detail = _clean_json_string(match[2].strip())

            # Parse danh sách người tham gia.
            contributors = [
                contrib.strip()
                for contrib in re.findall(r'"([^"]+)"', contributors_str)
            ] or ["Thành viên"]

            topics.append(
                {
                    "topic": topic_name,
                    "contributors": contributors[:5],  # Tối đa 5 người tham gia
                    "detail": detail,
                }
            )

        logger.info(
            f"Trích xuất chủ đề bằng regex thành công: {len(topics)} chủ đề hợp lệ"
        )
        return topics

    except Exception as e:
        logger.error(f"Trích xuất chủ đề bằng regex thất bại: {e}")
        return []


def extract_user_titles_with_regex(result_text: str, max_count: int) -> list[dict]:
    """
    Trích xuất thông tin danh hiệu bằng regex.

    Args:
        result_text: Văn bản cần trích xuất.
        max_count: Số lượng tối đa.

    Returns:
        Danh sách dữ liệu danh hiệu.
    """
    try:
        titles = []

        # Pattern khớp object danh hiệu hoàn chỉnh.
        pattern = r'\{\s*"name":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"user_id":\s*"([^"]+)"\s*,\s*"title":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"mbti":\s*"([^"]+)"\s*,\s*"reason":\s*"([^"]*(?:\\.[^"]*)*)"\s*\}'
        matches = re.findall(pattern, result_text, re.DOTALL)

        if not matches:
            # Thử pattern linh hoạt hơn, cho phép thứ tự trường thay đổi.
            pattern = r'"name":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"user_id":\s*"([^"]+)"[^}]*"title":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"mbti":\s*"([^"]+)"[^}]*"reason":\s*"([^"]*(?:\\.[^"]*)*)"'
            matches = re.findall(pattern, result_text, re.DOTALL)

        for match in matches[:max_count]:
            name = match[0].strip()
            user_id = match[1].strip()
            title = match[2].strip()
            mbti = match[3].strip()
            reason = _clean_json_string(match[4].strip())

            titles.append(
                {
                    "name": name,
                    "user_id": user_id,
                    "title": title,
                    "mbti": mbti,
                    "reason": reason,
                }
            )

        logger.info(
            f"Trích xuất danh hiệu bằng regex thành công: {len(titles)} danh hiệu hợp lệ"
        )
        return titles

    except Exception as e:
        logger.error(f"Trích xuất danh hiệu bằng regex thất bại: {e}")
        return []


def extract_golden_quotes_with_regex(result_text: str, max_count: int) -> list[dict]:
    """
    Trích xuất trích dẫn nổi bật bằng regex.

    Args:
        result_text: Văn bản cần trích xuất.
        max_count: Số lượng tối đa.

    Returns:
        Danh sách dữ liệu trích dẫn.
    """
    try:
        quotes = []

        # Pattern khớp object trích dẫn hoàn chỉnh.
        pattern = r'\{\s*"content":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"sender":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"reason":\s*"([^"]*(?:\\.[^"]*)*)"\s*\}'
        matches = re.findall(pattern, result_text, re.DOTALL)

        if not matches:
            # Thử pattern linh hoạt hơn, cho phép thứ tự trường thay đổi.
            pattern = r'"content":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"sender":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"reason":\s*"([^"]*(?:\\.[^"]*)*)"'
            matches = re.findall(pattern, result_text, re.DOTALL)

        for match in matches[:max_count]:
            content = _clean_json_string(match[0].strip())
            sender = match[1].strip()
            reason = _clean_json_string(match[2].strip())

            quotes.append({"content": content, "sender": sender, "reason": reason})

        logger.info(
            f"Trích xuất trích dẫn bằng regex thành công: {len(quotes)} mục hợp lệ"
        )
        return quotes

    except Exception as e:
        logger.error(f"Trích xuất trích dẫn bằng regex thất bại: {e}")
        return []


def extract_quality_with_regex(result_text: str) -> dict | None:
    """
    Trích xuất dữ liệu chất lượng trò chuyện bằng regex.

    Dùng làm fallback khi parse JSON thất bại.

    Args:
        result_text: Văn bản gốc do LLM trả về.

    Returns:
        Dict chất lượng sau khi parse hoặc None nếu thất bại.
    """
    try:
        title_m = re.search(r'"title"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', result_text)
        subtitle_m = re.search(r'"subtitle"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', result_text)
        summary_m = re.search(r'"summary"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', result_text)

        # Extract dimensions array
        dims_match = re.search(r'"dimensions"\s*:\s*\[(.*)\]', result_text, re.DOTALL)
        dims = []
        if dims_match:
            dim_objects = re.findall(
                r'\{[^}]*"name"\s*:\s*"([^"]*)"[^}]*'
                r'"percentage"\s*:\s*([\d.]+)[^}]*'
                r'"comment"\s*:\s*"([^"]*(?:\\.[^"]*)*)"[^}]*\}',
                dims_match.group(1),
            )
            for dm in dim_objects:
                dims.append(
                    {
                        "name": dm[0],
                        "percentage": float(dm[1]),
                        "comment": dm[2],
                    }
                )

        if not dims:
            logger.warning(
                "Regex chất lượng trò chuyện không tìm thấy dữ liệu chiều hợp lệ"
            )
            return None

        data = {
            "title": title_m.group(1) if title_m else "Đánh giá chất lượng trò chuyện",
            "subtitle": subtitle_m.group(1)
            if subtitle_m
            else "Hôm nay nhóm đã có chuyện gì?",
            "dimensions": dims,
            "summary": summary_m.group(1)
            if summary_m
            else "Hôm nay cũng là một ngày đầy năng lượng.",
        }

        logger.info(f"Trích xuất chất lượng bằng regex thành công: {len(dims)} chiều")
        return data

    except Exception as e:
        logger.error(f"Trích xuất chất lượng bằng regex thất bại: {e}")
        return None
