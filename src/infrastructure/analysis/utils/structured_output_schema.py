from __future__ import annotations

from typing import TypeAlias

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | dict[str, "JSONValue"] | list["JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]


def build_response_format(name: str, schema: JSONObject) -> JSONObject:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def build_topics_schema(max_items: int) -> JSONObject:
    return {
        "type": "array",
        "maxItems": max(1, int(max_items)),
        "items": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Tên chủ đề được viết bằng tiếng Việt.",
                },
                "contributors": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "detail": {
                    "type": "string",
                    "description": "Mô tả chủ đề được viết bằng tiếng Việt.",
                },
            },
            "required": ["topic", "contributors", "detail"],
            "additionalProperties": False,
        },
    }


def build_user_titles_schema(max_items: int) -> JSONObject:
    return {
        "type": "array",
        "maxItems": max(1, int(max_items)),
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "user_id": {"type": "string"},
                "title": {
                    "type": "string",
                    "description": "Danh hiệu được viết bằng tiếng Việt.",
                },
                "mbti": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Lý do trao danh hiệu được viết bằng tiếng Việt.",
                },
            },
            "required": ["name", "user_id", "title", "mbti", "reason"],
            "additionalProperties": False,
        },
    }


def build_golden_quotes_schema(max_items: int) -> JSONObject:
    return {
        "type": "array",
        "maxItems": max(1, int(max_items)),
        "items": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Nội dung trích dẫn bằng tiếng Việt; dịch sang tiếng Việt nếu bản gốc dùng ngôn ngữ khác.",
                },
                "sender": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Lý do lựa chọn được viết bằng tiếng Việt.",
                },
            },
            "required": ["content", "sender", "reason"],
            "additionalProperties": False,
        },
    }


def build_chat_quality_schema(max_dimensions: int) -> JSONObject:
    return {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Tiêu đề báo cáo được viết bằng tiếng Việt.",
            },
            "subtitle": {
                "type": "string",
                "description": "Phụ đề báo cáo được viết bằng tiếng Việt.",
            },
            "dimensions": {
                "type": "array",
                "maxItems": max(1, int(max_dimensions)),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Tên khía cạnh được viết bằng tiếng Việt.",
                        },
                        "percentage": {"type": "number"},
                        "comment": {
                            "type": "string",
                            "description": "Nhận xét được viết bằng tiếng Việt.",
                        },
                    },
                    "required": ["name", "percentage", "comment"],
                    "additionalProperties": False,
                },
            },
            "summary": {
                "type": "string",
                "description": "Tổng kết được viết bằng tiếng Việt.",
            },
        },
        "required": ["title", "subtitle", "dimensions", "summary"],
        "additionalProperties": False,
    }
