import json
from dataclasses import dataclass
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
from src.shared.vietnamese_language import sanitize_analysis_result_language


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


@dataclass
class ReportItem:
    topic: str = ""
    detail: str = ""
    name: str = ""
    title: str = ""
    reason: str = ""
    content: str = ""
    sender: str = ""


@dataclass
class ReportStatistics:
    golden_quotes: list[ReportItem]
    chat_quality_review: dict | None = None


def test_report_boundary_removes_chinese_semantics_but_keeps_identity():
    chinese_identity = "测试用户"
    vietnamese_identity = "Nguyễn Văn A"
    analysis_result = {
        "topics": [
            ReportItem(topic="技术讨论", detail="讨论 rất sâu"),
            ReportItem(topic="Kế hoạch cuối tuần", detail="Cả nhóm sẽ đi chơi."),
        ],
        "user_titles": [
            ReportItem(name=chinese_identity, title="技术专家", reason="Rất giỏi"),
            ReportItem(
                name=chinese_identity,
                title="Chuyên gia kỹ thuật",
                reason="Thường xuyên hỗ trợ mọi người.",
            ),
        ],
        "statistics": ReportStatistics(
            golden_quotes=[
                ReportItem(content="代码能跑就不要动", sender=chinese_identity),
                ReportItem(
                    content="Chạy được thì đừng sửa.", sender=vietnamese_identity
                ),
            ]
        ),
        "chat_quality_review": {
            "title": "今日群聊",
            "subtitle": "Một ngày vui vẻ",
            "dimensions": [],
            "summary": "Mọi người trò chuyện tích cực.",
        },
    }

    removed = sanitize_analysis_result_language(analysis_result)

    assert len(analysis_result["topics"]) == 1
    assert analysis_result["topics"][0].topic == "Kế hoạch cuối tuần"
    assert len(analysis_result["user_titles"]) == 1
    assert analysis_result["user_titles"][0].name == chinese_identity
    assert len(analysis_result["statistics"].golden_quotes) == 1
    assert analysis_result["statistics"].golden_quotes[0].sender == vietnamese_identity
    assert analysis_result["chat_quality_review"] is None
    assert analysis_result["statistics"].chat_quality_review is None
    assert "topics[0].topic" in removed
    assert "user_titles[0].title" in removed
    assert "statistics.golden_quotes[0].content" in removed
    assert "chat_quality_review.title" in removed


def test_group_analysis_command_stops_event_propagation():
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    source = main_path.read_text(encoding="utf-8")
    command_start = source.index("async def analyze_group_daily(")
    command_end = source.index("async def _send_analysis_report(", command_start)
    command_source = source[command_start:command_end]

    assert "event.should_call_llm(True)" in command_source
    assert "event.stop_event()" in command_source
    assert command_source.index("event.stop_event()") < command_source.index(
        "execute_daily_analysis"
    )
