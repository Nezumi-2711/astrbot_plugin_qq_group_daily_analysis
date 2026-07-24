from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

HAN_CHARACTER_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _require_vietnamese_text(value: object) -> str:
    """Chuẩn hoá và từ chối nội dung sinh còn chứa chữ Hán."""
    text = str(value).strip()
    if HAN_CHARACTER_PATTERN.search(text):
        raise ValueError("Nội dung sinh phải được viết bằng tiếng Việt")
    return text


class TopicItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    contributors: list[str]
    detail: str

    @field_validator("topic", "detail", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return _require_vietnamese_text(value)

    @field_validator("contributors", mode="before")
    @classmethod
    def _normalize_contributors(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        contributors: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                contributors.append(text)
        return contributors


class UserTitleItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    user_id: str
    title: str
    mbti: str
    reason: str

    @field_validator("name", "user_id", "mbti", mode="before")
    @classmethod
    def _normalize_identity(cls, value: object) -> str:
        return str(value).strip()

    @field_validator("title", "reason", mode="before")
    @classmethod
    def _normalize_generated_text(cls, value: object) -> str:
        return _require_vietnamese_text(value)


class GoldenQuoteItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    sender: str
    reason: str

    @field_validator("sender", mode="before")
    @classmethod
    def _normalize_identity(cls, value: object) -> str:
        return str(value).strip()

    @field_validator("content", "reason", mode="before")
    @classmethod
    def _normalize_generated_text(cls, value: object) -> str:
        return _require_vietnamese_text(value)


class QualityDimensionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    percentage: float
    comment: str

    @field_validator("name", "comment", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return _require_vietnamese_text(value)


class QualityReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    subtitle: str
    dimensions: list[QualityDimensionModel]
    summary: str

    @field_validator("title", "subtitle", "summary", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return _require_vietnamese_text(value)


def validate_topic_items(
    data_list: list[dict],
) -> tuple[bool, list[dict] | None, str | None]:
    try:
        normalized = [
            TopicItemModel.model_validate(item).model_dump() for item in data_list
        ]
        return True, normalized, None
    except ValidationError as e:
        return False, None, str(e)


def validate_user_title_items(
    data_list: list[dict],
) -> tuple[bool, list[dict] | None, str | None]:
    try:
        normalized = [
            UserTitleItemModel.model_validate(item).model_dump() for item in data_list
        ]
        return True, normalized, None
    except ValidationError as e:
        return False, None, str(e)


def validate_golden_quote_items(
    data_list: list[dict],
) -> tuple[bool, list[dict] | None, str | None]:
    try:
        normalized = [
            GoldenQuoteItemModel.model_validate(item).model_dump() for item in data_list
        ]
        return True, normalized, None
    except ValidationError as e:
        return False, None, str(e)


def validate_quality_review_item(data: dict) -> tuple[bool, dict | None, str | None]:
    try:
        normalized = QualityReviewModel.model_validate(data).model_dump()
        return True, normalized, None
    except ValidationError as e:
        return False, None, str(e)
