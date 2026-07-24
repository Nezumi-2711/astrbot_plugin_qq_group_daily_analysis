"""Tiện ích thuần để kiểm tra ngôn ngữ đầu ra báo cáo."""

from __future__ import annotations

import re

HAN_CHARACTER_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def contains_han_characters(value: object) -> bool:
    """Kiểm tra chuỗi có chứa chữ Hán hay không."""
    return bool(HAN_CHARACTER_PATTERN.search(str(value or "")))


def sanitize_analysis_result_language(analysis_result: dict) -> list[str]:
    """Loại nội dung sinh còn chữ Hán trước khi tạo báo cáo.

    Đây là cổng phòng vệ cuối cho dữ liệu cũ hoặc đường gọi không đi qua
    analyzer. Các trường danh tính như tên người dùng, người gửi, ID và người
    tham gia không bị kiểm tra.
    """

    def get_value(container: object, field: str, default=None):
        if isinstance(container, dict):
            return container.get(field, default)
        return getattr(container, field, default)

    def set_value(container: object, field: str, value: object) -> None:
        if isinstance(container, dict):
            container[field] = value
        else:
            setattr(container, field, value)

    known_identities: set[str] = set()
    user_analysis = analysis_result.get("user_analysis", {})
    if isinstance(user_analysis, dict):
        for user_id, user_data in user_analysis.items():
            known_identities.add(str(user_id).strip())
            if isinstance(user_data, dict):
                for field in ("nickname", "name", "card"):
                    known_identities.add(str(user_data.get(field, "")).strip())

    for topic in analysis_result.get("topics", []):
        contributors = get_value(topic, "contributors", [])
        if isinstance(contributors, list):
            known_identities.update(str(item).strip() for item in contributors)
    for title in analysis_result.get("user_titles", []):
        known_identities.add(str(get_value(title, "name", "")).strip())

    statistics = analysis_result.get("statistics")
    if statistics is not None:
        quotes = get_value(statistics, "golden_quotes", [])
        if isinstance(quotes, list):
            for quote in quotes:
                known_identities.add(str(get_value(quote, "sender", "")).strip())

    known_identities.discard("")

    def contains_untranslated_han(value: object) -> bool:
        text = str(value or "")
        for identity in sorted(known_identities, key=len, reverse=True):
            text = text.replace(identity, "")
        return contains_han_characters(text)

    removed: list[str] = []
    for collection_name, fields in (
        ("topics", ("topic", "detail")),
        ("user_titles", ("title", "reason")),
    ):
        collection = analysis_result.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        kept = []
        for index, item in enumerate(collection):
            invalid_fields = [
                field
                for field in fields
                if contains_untranslated_han(get_value(item, field, ""))
            ]
            if invalid_fields:
                removed.extend(
                    f"{collection_name}[{index}].{field}" for field in invalid_fields
                )
                continue
            kept.append(item)
        analysis_result[collection_name] = kept

    if statistics is not None:
        quotes = get_value(statistics, "golden_quotes", [])
        if isinstance(quotes, list):
            kept_quotes = []
            for index, quote in enumerate(quotes):
                invalid_fields = [
                    field
                    for field in ("content", "reason")
                    if contains_untranslated_han(get_value(quote, field, ""))
                ]
                if invalid_fields:
                    removed.extend(
                        f"statistics.golden_quotes[{index}].{field}"
                        for field in invalid_fields
                    )
                    continue
                kept_quotes.append(quote)
            set_value(statistics, "golden_quotes", kept_quotes)

    quality_review = analysis_result.get("chat_quality_review")
    if quality_review is None and statistics is not None:
        quality_review = get_value(statistics, "chat_quality_review")
    if quality_review is not None:
        invalid_fields = [
            field
            for field in ("title", "subtitle", "summary")
            if contains_untranslated_han(get_value(quality_review, field, ""))
        ]
        dimensions = get_value(quality_review, "dimensions", [])
        if isinstance(dimensions, list):
            for index, dimension in enumerate(dimensions):
                invalid_fields.extend(
                    f"dimensions[{index}].{field}"
                    for field in ("name", "comment")
                    if contains_untranslated_han(get_value(dimension, field, ""))
                )
        if invalid_fields:
            removed.extend(f"chat_quality_review.{field}" for field in invalid_fields)
            analysis_result["chat_quality_review"] = None
            if statistics is not None:
                set_value(statistics, "chat_quality_review", None)

    return removed
