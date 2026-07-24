import json
from pathlib import Path

from src.infrastructure.analysis.analyzers.base_analyzer import (
    BaseAnalyzer,
    VIETNAMESE_OUTPUT_POLICY,
)
from src.infrastructure.analysis.utils.response_validation import (
    validate_golden_quote_items,
    validate_quality_review_item,
    validate_topic_items,
    validate_user_title_items,
)


class DummyAnalyzer(BaseAnalyzer[dict, list[dict]]):
    def get_data_type(self) -> str:
        return "Kiểm thử"

    def get_max_count(self) -> int:
        return 1

    def build_prompt(self, data: list[dict]) -> str:
        return "请使用中文回答"

    def extract_with_regex(self, result_text: str, max_count: int) -> list[dict]:
        return []

    def create_data_objects(self, data_list: list[dict]) -> list[dict]:
        return data_list


def test_vietnamese_policy_overrides_chinese_prompt_and_persona():
    analyzer = DummyAnalyzer(context=None, config_manager=None)
    prompt = analyzer._apply_persona_reinforcement(
        "请使用中文回答", "你是一个中文助手，只能使用中文。"
    )
    final_prompt, final_system_prompt = analyzer._apply_vietnamese_output_policy(
        prompt, "你是一个中文助手，只能使用中文。"
    )

    assert final_prompt.endswith(VIETNAMESE_OUTPUT_POLICY)
    assert final_system_prompt.endswith(VIETNAMESE_OUTPUT_POLICY)
    assert "mức ưu tiên cao hơn" in final_prompt
    assert "Persona chỉ quyết định phong cách" in final_system_prompt
    assert "dịch phát biểu sang tiếng Việt" in final_prompt


def test_vietnamese_policy_is_also_used_without_persona():
    analyzer = DummyAnalyzer(context=None, config_manager=None)

    final_prompt, final_system_prompt = analyzer._apply_vietnamese_output_policy(
        "Phân tích dữ liệu", None
    )

    assert final_prompt.endswith(VIETNAMESE_OUTPUT_POLICY)
    assert final_system_prompt == VIETNAMESE_OUTPUT_POLICY


def test_vietnamese_semantic_payloads_are_valid():
    topic_ok, _, _ = validate_topic_items(
        [
            {
                "topic": "Kế hoạch cuối tuần",
                "contributors": ["123"],
                "detail": "Các thành viên thống nhất đi dã ngoại vào sáng Chủ nhật.",
            }
        ]
    )
    title_ok, _, _ = validate_user_title_items(
        [
            {
                "name": "测试用户",
                "user_id": "123",
                "title": "Người kết nối",
                "mbti": "ENFJ",
                "reason": "Luôn chủ động trả lời và kết nối mọi người.",
            }
        ]
    )
    quote_ok, _, _ = validate_golden_quote_items(
        [
            {
                "content": "Chạy được đã là một loại thành công.",
                "sender": "[123]",
                "reason": "Câu nói hài hước nhưng rất đúng với tình huống.",
            }
        ]
    )
    quality_ok, _, _ = validate_quality_review_item(
        {
            "title": "Một ngày nhiều ý tưởng",
            "subtitle": "Từ chuyện vui đến kế hoạch mới",
            "dimensions": [
                {
                    "name": "Chia sẻ kiến thức",
                    "percentage": 60,
                    "comment": "Mọi người hỗ trợ nhau rất nhiệt tình.",
                }
            ],
            "summary": "Không khí tích cực và có nhiều nội dung hữu ích.",
        }
    )

    assert topic_ok
    assert title_ok
    assert quote_ok
    assert quality_ok


def test_chinese_generated_fields_are_rejected_but_identity_is_preserved():
    topic_ok, _, topic_error = validate_topic_items(
        [{"topic": "技术讨论", "contributors": ["测试用户"], "detail": "讨论很深入"}]
    )
    title_ok, _, title_error = validate_user_title_items(
        [
            {
                "name": "测试用户",
                "user_id": "123",
                "title": "技术专家",
                "mbti": "INTJ",
                "reason": "经常帮助大家",
            }
        ]
    )
    quote_ok, _, quote_error = validate_golden_quote_items(
        [{"content": "代码能跑就不要动", "sender": "[123]", "reason": "很幽默"}]
    )
    quality_ok, _, quality_error = validate_quality_review_item(
        {
            "title": "今日群聊",
            "subtitle": "副标题",
            "dimensions": [
                {"name": "技术交流", "percentage": 100, "comment": "讨论热烈"}
            ],
            "summary": "大家都很活跃",
        }
    )

    assert not topic_ok and "tiếng Việt" in (topic_error or "")
    assert not title_ok and "tiếng Việt" in (title_error or "")
    assert not quote_ok and "tiếng Việt" in (quote_error or "")
    assert not quality_ok and "tiếng Việt" in (quality_error or "")


def test_default_golden_quote_prompt_requires_translation():
    schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    prompt = schema["prompts"]["items"]["golden_quote_analysis_prompts"]["items"][
        "golden_quote_v2_prompt"
    ]["default"]

    assert "dịch phát biểu sang tiếng Việt" in prompt
    assert "content` phải giữ nguyên lời nói gốc" not in prompt
