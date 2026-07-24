"""Công cụ render template an toàn, tương thích String Template."""

import re
from string import Template

from ...utils.logger import logger

# Placeholder mặc định dùng chung
PLACEHOLDERS = {
    # Biến cốt lõi của phân tích
    "messages_text": "${messages_text}",
    "reviews_text": "${reviews_text}",
    "max_topics": "${max_topics}",
    "users_text": "${users_text}",
    "max_golden_quotes": "${max_golden_quotes}",
    # Biến dùng khi render tên tệp
    "group_id": "${group_id}",
    "date": "${date}",
    "ulid": "${ulid}",
}


def is_str_format_template(template: str) -> bool:
    """Kiểm tra template có dùng cú pháp str.format hay không.

    Chỉ coi là str.format khi không chứa placeholder String Template
    `${var}` hoặc `$var`, đồng thời có `{var}` nhưng không phải `{{...}}`.
    """
    if not template:
        return False

    # 1. Tạo pattern loại trừ cho ${var} hoặc $var.
    dollar_patterns = [re.escape(v) for v in PLACEHOLDERS.values()] + [
        rf"\${re.escape(k)}" for k in PLACEHOLDERS.keys()
    ]
    exclude_regex = "|".join(dollar_patterns)

    # Có placeholder liên quan đến $ thì không coi là template str.format.
    if re.search(exclude_regex, template):
        return False

    # 2. Kiểm tra {key} chuẩn trong một cặp ngoặc đơn.
    for key in PLACEHOLDERS.keys():
        # (?<!\{): phía trước không được là {
        # \{{key}\}: khớp {key}
        # (?!\}): phía sau không được là }
        # (?<!\$): phía trước không được là $
        pattern = rf"(?<![\{{\$])\{{{key}\}}(?!\}})"
        if re.search(pattern, template):
            return True
    return False


def upgrade_str_format_template(template: str) -> tuple[str, bool]:
    """Tự nâng cấp template str.format sang string.Template.

    Trả về tuple gồm template sau nâng cấp và trạng thái đã nâng cấp.
    """
    if template is None:
        return "", False

    if not is_str_format_template(template):
        return template, False

    # Escape $ trong văn bản gốc để Template không hiểu nhầm là placeholder.
    safe_template = template.replace("$", "$$")

    # Chuyển {var} thành ${var}.
    safe_template = re.sub(
        r"(?<![\{\$])\{([_a-zA-Z][_a-zA-Z0-9]*)\}(?!\})",
        lambda m: f"${{{m.group(1)}}}",
        safe_template,
    )

    # Chuyển ngoặc kép về ngoặc đơn (ngoặc literal trong str.format).
    safe_template = safe_template.replace("{{", "{").replace("}}", "}")

    return safe_template, True


def render_template(template: str, strict: bool = False, **kwargs) -> str:
    """Render template bằng String Template.

    Args:
        template: Chuỗi template.
        strict: Có dùng strict mode hay không; thiếu biến sẽ phát sinh lỗi.
        **kwargs: Biến dùng để render.

    Plugin nâng cấp tương thích str.format khi khởi động nên runtime render
    trực tiếp bằng string.Template.
    """
    if template is None:
        return ""

    try:
        t = Template(template)
        return t.substitute(**kwargs) if strict else t.safe_substitute(**kwargs)
    except Exception as e:
        if strict:
            raise
        logger.warning(
            f"[template_utils] Render template thất bại, trả văn bản gốc; lỗi: {e}",
            exc_info=True,
        )
        return template
