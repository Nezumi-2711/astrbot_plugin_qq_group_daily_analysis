"""Trình quản lý cấu hình plugin ở tầng infrastructure."""

from astrbot.api import AstrBotConfig
from astrbot.api.star import StarTools

from ...shared.constants import PLUGIN_NAME
from ...utils.logger import logger
from ..utils.template_utils import upgrade_str_format_template


class ConfigManager:
    """Trình quản lý cấu hình.

    Cấu hình được nhóm lồng nhau ở cấp cao nhất: ``basic``, ``qq_official``,
    ``auto_analysis``, ``llm``, ``analysis_features``, ``incremental`` và ``prompts``.
    """

    def __init__(self, config: AstrBotConfig):
        self.config = config

    def _get_group(self, group: str) -> dict:
        """Lấy dict cấu hình của nhóm hoặc dict rỗng nếu không tồn tại."""
        return self.config.get(group, {})

    def _ensure_group(self, group: str) -> dict:
        """Đảm bảo nhóm tồn tại và trả về tham chiếu dict."""
        if group not in self.config:
            self.config[group] = {}
        return self.config[group]

    def get_group_list_mode(self) -> str:
        """Lấy chế độ danh sách nhóm: whitelist, blacklist hoặc none."""
        return self._get_group("basic").get("group_list_mode", "none")

    def get_group_list(self) -> list[str]:
        """Lấy danh sách nhóm dùng cho whitelist/blacklist."""
        return self._get_group("basic").get("group_list", [])

    def is_group_allowed(self, group_id_or_umo: str) -> bool:
        """
        Kiểm tra nhóm có được phép theo whitelist/blacklist.

        Hỗ trợ group_id đơn giản hoặc UMO (Unified Message Origin).
        """
        mode = self.get_group_list_mode().lower()
        if mode not in ("whitelist", "blacklist", "none"):
            mode = "none"

        if mode == "none":
            return True

        glist = [str(g).strip() for g in self.get_group_list()]
        target = str(group_id_or_umo).strip()

        is_in_list = any(self._is_group_match(target, item) for item in glist)

        if mode == "whitelist":
            return is_in_list
        if mode == "blacklist":
            return not is_in_list

        return True

    def _is_group_match(self, target: str, item: str) -> bool:
        """
        So khớp item danh sách với UMO hoặc ID đích, hỗ trợ topic Telegram (#)
        và phiên cô lập (_) theo cả hai chiều.
        """
        if item == target:
            return True

        # Tách tiền tố UMO và phần ID, ví dụ default:GroupMessage:ID.
        if ":" in target:
            target_prefix, target_id = target.rsplit(":", 1)
        else:
            target_prefix, target_id = "", target

        # Tạo mọi ứng viên ID cho chế độ cô lập và topic.
        candidates = {target_id}
        if "#" in target_id:
            candidates.add(target_id.split("#", 1)[0])
        if "_" in target_id:
            for part in target_id.split("_"):
                candidates.add(part)

        # Kiểm tra định dạng item trong danh sách.
        if ":" in item:
            i_prefix, i_id = item.rsplit(":", 1)
            # Nếu item có tiền tố thì phải khớp, trừ target không có tiền tố.
            if target_prefix and i_prefix != target_prefix:
                return False
        else:
            i_id = item

        # ID trong danh sách có thể ở dạng ghép như UserId_GroupId.
        item_variants = {i_id}
        if "#" in i_id:
            item_variants.add(i_id.split("#", 1)[0])
        if "_" in i_id:
            for part in i_id.split("_"):
                item_variants.add(part)

        # Khớp nếu hai tập phần ID cốt lõi có giao nhau.
        return not item_variants.isdisjoint(candidates)

    def get_max_messages(self) -> int:
        """Lấy số tin nhắn tối đa."""
        return self._get_group("basic").get("max_messages", 1000)

    def get_analysis_days(self) -> int:
        """Lấy số ngày phân tích."""
        return self._get_group("basic").get("analysis_days", 1)

    def get_auto_analysis_time(self) -> list[str]:
        """Lấy danh sách thời điểm phân tích tự động."""
        group = self._get_group("auto_analysis")
        val = group.get("auto_analysis_time", ["09:00"])
        # Tương thích cấu hình chuỗi của bản cũ.
        if isinstance(val, str):
            val_list = [val]
            # Tự sửa định dạng cấu hình.
            try:
                auto_group = self._ensure_group("auto_analysis")
                auto_group["auto_analysis_time"] = val_list
                self.config.save_config()
                logger.info(f"Tự sửa định dạng auto_analysis_time: {val} -> {val_list}")
            except Exception as e:
                logger.warning(f"Sửa định dạng cấu hình thất bại: {e}")
            return val_list
        return val if isinstance(val, list) else ["09:00"]

    def get_enable_auto_analysis(self) -> bool:
        """
        Kiểm tra phân tích tự động có bật hay không, tương thích giao diện cũ.

        Bản cũ dùng boolean ``enable_auto_analysis``; bản mới suy ra từ danh sách lịch.
        """
        return self.is_auto_analysis_enabled()

    def get_output_format(self) -> list[str]:
        """Lấy định dạng output."""
        val = self._get_group("basic").get("output_format", ["image"])
        return val if isinstance(val, list) else [val]

    def get_qq_official_t2i_summary_dashboard_enabled(self) -> bool:
        """Kiểm tra dashboard tổng quan T2I QQ Official có bật hay không."""
        group = self._get_group("qq_official")
        if "enable_t2i_summary_dashboard" in group:
            return bool(group["enable_t2i_summary_dashboard"])
        return bool(group.get("enable_t2i_activity_histogram", True))

    def get_min_messages_threshold(self) -> int:
        """Lấy ngưỡng tin nhắn tối thiểu."""
        return self._get_group("basic").get("min_messages_threshold", 50)

    def get_topic_analysis_enabled(self) -> bool:
        """Kiểm tra phân tích chủ đề có bật hay không."""
        return self._get_group("analysis_features").get("topic_analysis_enabled", True)

    def get_user_title_analysis_enabled(self) -> bool:
        """Kiểm tra phân tích danh hiệu có bật hay không."""
        return self._get_group("analysis_features").get(
            "user_title_analysis_enabled", True
        )

    def get_golden_quote_analysis_enabled(self) -> bool:
        """Kiểm tra phân tích trích dẫn có bật hay không."""
        return self._get_group("analysis_features").get(
            "golden_quote_analysis_enabled", True
        )

    def get_chat_quality_analysis_enabled(self) -> bool:
        """Kiểm tra phân tích chất lượng có bật hay không."""
        return self._get_group("analysis_features").get(
            "chat_quality_analysis_enabled", False
        )

    def get_max_topics(self) -> int:
        """Lấy số chủ đề tối đa."""
        return self._get_group("analysis_features").get("max_topics", 5)

    def get_max_user_titles(self) -> int:
        """Lấy số danh hiệu tối đa."""
        return self._get_group("analysis_features").get("max_user_titles", 8)

    def get_max_golden_quotes(self) -> int:
        """Lấy số trích dẫn tối đa."""
        return self._get_group("analysis_features").get("max_golden_quotes", 5)

    def get_llm_retries(self) -> int:
        """Lấy số lần retry yêu cầu LLM."""
        return self._get_group("llm").get("llm_retries", 2)

    def get_llm_backoff(self) -> int:
        """Lấy giá trị backoff cơ sở của retry LLM, tính bằng giây."""
        return self._get_group("llm").get("llm_backoff", 2)

    def get_enable_streaming_llm_call(self) -> bool:
        """Kiểm tra lời gọi LLM streaming có bật hay không."""
        return self._get_group("llm").get("enable_streaming_llm_call", False)

    def get_debug_mode(self) -> bool:
        """Kiểm tra debug mode có bật hay không."""
        return self._get_group("basic").get("debug_mode", False)

    def get_enable_base64_image(self) -> bool:
        """Kiểm tra truyền ảnh Base64 có bật hay không."""
        return self._get_group("basic").get("enable_base64_image", False)

    def get_t2i_rendering_strategies(self) -> list[dict]:
        """Lấy hai chiến lược render T2I do người dùng cấu hình."""
        group = self._get_group("t2i_rendering")

        return [
            # Lượt đầu: ưu tiên chất lượng.
            {
                "full_page": True,
                "type": group.get("t2i_r1_type", "png"),
                "quality": group.get("t2i_r1_quality", 100),
                "device_scale_factor_level": group.get("t2i_r1_device_scale", "ultra"),
                "timeout": group.get("t2i_r1_timeout", 30000),
            },
            # Lượt hai: ưu tiên ổn định và fallback.
            {
                "full_page": True,
                "type": group.get("t2i_r2_type", "jpeg"),
                "quality": group.get("t2i_r2_quality", 80),
                "device_scale_factor_level": group.get("t2i_r2_device_scale", "normal"),
                "timeout": group.get("t2i_r2_timeout", 60000),
            },
        ]

    def get_t2i_font_source(self) -> str:
        """Lấy nguồn font T2I: Mainland hoặc Overseas."""
        return self._get_group("t2i_rendering").get("t2i_font_source", "Overseas")

    def get_t2i_google_fonts_mirror(self) -> str:
        """Lấy URL mirror Google Fonts theo môi trường."""
        source = self.get_t2i_font_source()
        group = self._get_group("t2i_rendering")
        if source == "Mainland":
            return group.get("t2i_mainland_google_fonts", "https://fonts.loli.net")
        return group.get("t2i_overseas_google_fonts", "https://fonts.googleapis.com")

    def get_t2i_gstatic_mirror(self) -> str:
        """Lấy URL mirror Gstatic theo môi trường."""
        source = self.get_t2i_font_source()
        group = self._get_group("t2i_rendering")
        if source == "Mainland":
            return group.get("t2i_mainland_gstatic", "https://gstatic.loli.net")
        return group.get("t2i_overseas_gstatic", "https://fonts.gstatic.com")

    def get_t2i_atri_font_mirror(self) -> str:
        """Lấy URL mirror font theme ATRI."""
        return self._get_group("t2i_rendering").get(
            "t2i_atri_font_mirror", "https://tc.ciallo.ccwu.cc"
        )

    def get_llm_provider_id(self) -> str:
        """Lấy Provider ID LLM chính."""
        return self._get_group("llm").get("llm_provider_id", "")

    def get_topic_provider_id(self) -> str:
        """Lấy Provider ID riêng cho phân tích chủ đề."""
        return self._get_group("llm").get("topic_provider_id", "")

    def get_user_title_provider_id(self) -> str:
        """Lấy Provider ID riêng cho phân tích danh hiệu."""
        return self._get_group("llm").get("user_title_provider_id", "")

    def get_golden_quote_provider_id(self) -> str:
        """Lấy Provider ID riêng cho phân tích trích dẫn."""
        return self._get_group("llm").get("golden_quote_provider_id", "")

    def get_keep_original_persona(self) -> bool:
        """Kiểm tra có kế thừa persona gốc của phiên hay không."""
        return self._get_group("analysis_features").get("keep_original_persona", False)

    def get_use_plugin_specific_persona(self) -> bool:
        """Kiểm tra có bắt buộc dùng persona do plugin chỉ định hay không."""
        return self._get_group("analysis_features").get(
            "use_plugin_specific_persona", False
        )

    def get_plugin_specific_persona_id(self) -> str:
        """Lấy ID persona toàn cục do plugin chỉ định."""
        return self._get_group("analysis_features").get(
            "plugin_specific_persona_id", ""
        )

    def get_bot_self_ids(self) -> list:
        """Lấy danh sách ID của bot, tương thích bot_qq_ids."""
        basic = self._get_group("basic")
        ids = basic.get("bot_self_ids", [])
        if not ids:
            ids = basic.get("bot_qq_ids", [])
        return ids

    def get_filter_bot_messages(self) -> bool:
        """Kiểm tra có lọc tin nhắn của bot hay không."""
        return self._get_group("basic").get("filter_bot_messages", True)

    def set_filter_bot_messages(self, enabled: bool):
        """Thiết lập lọc tin nhắn của bot."""
        self._ensure_group("basic")["filter_bot_messages"] = enabled
        self.config.save_config()

    def get_html_output_dir(self) -> str:
        """Lấy thư mục output HTML."""

        default_path = StarTools.get_data_dir(PLUGIN_NAME) / "self_hosted_html_reports"
        val = self._get_group("html").get("html_output_dir")
        return val if val else str(default_path)

    def get_html_base_url(self) -> str:
        """Lấy base URL liên kết HTML."""
        return self._get_group("html").get("html_base_url", "")

    def get_html_only_url(self) -> bool:
        """Kiểm tra có chỉ gửi liên kết thay vì tệp HTML hay không."""
        return self._get_group("html").get("html_only_url", False)

    def set_html_only_url(self, enabled: bool):
        """Thiết lập chỉ gửi liên kết thay vì tệp HTML."""
        self._ensure_group("html")["html_only_url"] = enabled
        self.config.save_config()

    def get_html_filename_format(self) -> str:
        """Lấy định dạng tên tệp HTML."""
        return self._get_group("html").get(
            "html_filename_format", "bao_cao_phan_tich_nhom_{group_id}_{date}.html"
        )

    def get_topic_analysis_prompt(self, style: str = "topic_prompt") -> str:
        """Lấy template prompt phân tích chủ đề."""
        prompts_config = self._get_group("prompts").get("topic_analysis_prompts", {})
        prompt = prompts_config.get(style, "")
        if prompt:
            return prompt
        return ""

    def get_user_title_analysis_prompt(self, style: str = "user_title_prompt") -> str:
        """Lấy template prompt phân tích danh hiệu."""
        prompts_config = self._get_group("prompts").get(
            "user_title_analysis_prompts", {}
        )
        prompt = prompts_config.get(style, "")
        if prompt:
            return prompt
        return ""

    def get_golden_quote_analysis_prompt(
        self, style: str = "golden_quote_v2_prompt"
    ) -> str:
        """Lấy template prompt phân tích trích dẫn."""
        prompts_config = self._get_group("prompts").get(
            "golden_quote_analysis_prompts", {}
        )
        prompt = prompts_config.get(style, "")
        if prompt:
            return prompt
        return ""

    def get_quality_analysis_prompt(self, style: str = "quality_v2_prompt") -> str:
        """Lấy template prompt phân tích chất lượng."""
        prompts_config = self._get_group("prompts").get("quality_analysis_prompts", {})
        prompt = prompts_config.get(style, "")
        if prompt:
            return prompt
        return ""

    def set_quality_analysis_prompt(self, prompt: str):
        """Thiết lập template prompt phân tích chất lượng."""
        prompts = self._ensure_group("prompts")
        if "quality_analysis_prompts" not in prompts:
            prompts["quality_analysis_prompts"] = {}
        prompts["quality_analysis_prompts"]["quality_v2_prompt"] = prompt
        self.config.save_config()

    def _upgrade_config_item(self, group: str, key: str, setter_func):
        """Nâng cấp mục cấu hình từ str.format sang string.Template và ghi lại."""
        # Với prompt, lấy nhóm prompts rồi lấy nhóm con.
        if group in (
            "quality_analysis_prompts",
            "topic_analysis_prompts",
            "user_title_analysis_prompts",
            "golden_quote_analysis_prompts",
        ):
            target_group = self._get_group("prompts").get(group, {})
        else:
            target_group = self._get_group(group)

        val = target_group.get(key, "")
        if not val or not isinstance(val, str):
            return False

        upgraded_val, upgraded = upgrade_str_format_template(val)
        if upgraded and upgraded_val != val:
            setter_func(upgraded_val)
            logger.info(
                f"Phát hiện cú pháp cũ ở {group}.{key}; đã tự nâng cấp sang string.Template"
            )
            return True
        return False

    def upgrade_prompt_templates(self):
        """Quét và nâng cấp mọi template cấu hình khi khởi động."""
        modified = False
        # 1. Nâng cấp template prompt.
        modified |= self._upgrade_config_item(
            "quality_analysis_prompts",
            "quality_v2_prompt",
            self.set_quality_analysis_prompt,
        )
        modified |= self._upgrade_config_item(
            "quality_analysis_prompts",
            "quality_summary_prompt",
            self.set_quality_summary_prompt,
        )
        modified |= self._upgrade_config_item(
            "topic_analysis_prompts",
            "topic_prompt",
            self.set_topic_analysis_prompt,
        )
        modified |= self._upgrade_config_item(
            "user_title_analysis_prompts",
            "user_title_prompt",
            self.set_user_title_analysis_prompt,
        )
        modified |= self._upgrade_config_item(
            "golden_quote_analysis_prompts",
            "golden_quote_v2_prompt",
            self.set_golden_quote_analysis_prompt,
        )

        # 2. Nâng cấp định dạng tên tệp.
        modified |= self._upgrade_config_item(
            "html",
            "html_filename_format",
            self.set_html_filename_format,
        )

        if modified:
            logger.info(
                "Đã di chuyển an toàn mọi template cấu hình từ str.format sang string.Template và ghi lại cấu hình"
            )
        return modified

    def get_quality_summary_prompt(self, style: str = "quality_summary_prompt") -> str:
        """Lấy template prompt tổng hợp chất lượng."""
        prompts_config = self._get_group("prompts").get("quality_analysis_prompts", {})
        prompt = prompts_config.get(style, "")
        if prompt:
            return prompt
        return ""

    def set_topic_analysis_prompt(self, prompt: str):
        """Thiết lập template prompt phân tích chủ đề."""
        prompts = self._ensure_group("prompts")
        if "topic_analysis_prompts" not in prompts:
            prompts["topic_analysis_prompts"] = {}
        prompts["topic_analysis_prompts"]["topic_prompt"] = prompt
        self.config.save_config()

    def set_quality_summary_prompt(self, prompt: str):
        """Thiết lập template prompt tổng hợp chất lượng."""
        prompts = self._ensure_group("prompts")
        if "quality_analysis_prompts" not in prompts:
            prompts["quality_analysis_prompts"] = {}
        prompts["quality_analysis_prompts"]["quality_summary_prompt"] = prompt
        self.config.save_config()

    def set_user_title_analysis_prompt(self, prompt: str):
        """Thiết lập template prompt phân tích danh hiệu."""
        prompts = self._ensure_group("prompts")
        if "user_title_analysis_prompts" not in prompts:
            prompts["user_title_analysis_prompts"] = {}
        prompts["user_title_analysis_prompts"]["user_title_prompt"] = prompt
        self.config.save_config()

    def set_golden_quote_analysis_prompt(self, prompt: str):
        """Thiết lập template prompt phân tích trích dẫn."""
        prompts = self._ensure_group("prompts")
        if "golden_quote_analysis_prompts" not in prompts:
            prompts["golden_quote_analysis_prompts"] = {}
        prompts["golden_quote_analysis_prompts"]["golden_quote_v2_prompt"] = prompt
        self.config.save_config()

    def set_output_format(self, format_types: str | list[str]):
        """Thiết lập định dạng output."""
        if isinstance(format_types, str):
            format_types = [
                f.strip() for f in format_types.replace("，", ",").split(",")
            ]
        for f in format_types:
            if f not in ("image", "text", "html"):
                raise ValueError(
                    f"Định dạng không hợp lệ: {f}. Hợp lệ: image, text, html"
                )

        self._ensure_group("basic")["output_format"] = format_types
        self.config.save_config()

    def set_group_list_mode(self, mode: str):
        """Thiết lập chế độ danh sách nhóm."""
        self._ensure_group("basic")["group_list_mode"] = mode
        self.config.save_config()

    def set_group_list(self, groups: list[str]):
        """Thiết lập danh sách nhóm."""
        self._ensure_group("basic")["group_list"] = groups
        self.config.save_config()

    def get_max_concurrent_tasks(self) -> int:
        """Lấy số nhóm phân tích tự động đồng thời tối đa."""
        return self._get_group("performance").get("max_concurrent_groups", 3)

    def get_llm_max_concurrent(self) -> int:
        """Lấy số yêu cầu LLM đồng thời toàn cục tối đa."""
        return self._get_group("performance").get("max_concurrent_llm", 3)

    def get_t2i_max_concurrent(self) -> int:
        """Lấy số tác vụ render ảnh T2I đồng thời toàn cục tối đa."""
        return self._get_group("performance").get("max_concurrent_t2i", 1)

    def get_stagger_seconds(self) -> int:
        """Lấy khoảng cách khởi động tác vụ nhiều nhóm, tính bằng giây."""
        return self._get_group("performance").get("stagger_seconds", 2)

    def set_max_concurrent_tasks(self, count: int):
        """Thiết lập số tác vụ phân tích tự động đồng thời tối đa."""
        self._ensure_group("performance")["max_concurrent_groups"] = count
        self.config.save_config()

    def set_max_messages(self, count: int):
        """Thiết lập số tin nhắn tối đa."""
        self._ensure_group("basic")["max_messages"] = count
        self.config.save_config()

    def set_analysis_days(self, days: int):
        """Thiết lập số ngày phân tích."""
        self._ensure_group("basic")["analysis_days"] = days
        self.config.save_config()

    def set_auto_analysis_time(self, time_val: str | list[str]):
        """Thiết lập thời điểm phân tích tự động."""
        self._ensure_group("auto_analysis")["auto_analysis_time"] = time_val
        self.config.save_config()

    def is_auto_analysis_enabled(self) -> bool:
        """
        Kiểm tra phân tích tự động có được bật theo danh sách hay không.

        Bật khi whitelist không rỗng hoặc đang ở chế độ blacklist.
        """
        mode = self.get_scheduled_group_list_mode()
        lst = self.get_scheduled_group_list()
        return (mode == "whitelist" and len(lst) > 0) or (mode == "blacklist")

    def get_scheduled_group_list_mode(self) -> str:
        """Lấy chế độ danh sách phân tích định kỳ."""
        return self._get_group("auto_analysis").get(
            "scheduled_group_list_mode", "whitelist"
        )

    def set_scheduled_group_list_mode(self, mode: str):
        """Thiết lập chế độ danh sách phân tích định kỳ."""
        self._ensure_group("auto_analysis")["scheduled_group_list_mode"] = mode
        self.config.save_config()

    def get_scheduled_group_list(self) -> list[str]:
        """Lấy danh sách nhóm đích phân tích định kỳ."""
        return self._get_group("auto_analysis").get("scheduled_group_list", [])

    def set_scheduled_group_list(self, groups: list[str]):
        """Thiết lập danh sách nhóm đích phân tích định kỳ."""
        self._ensure_group("auto_analysis")["scheduled_group_list"] = groups
        self.config.save_config()

    def is_group_in_filtered_list(
        self, group_umo_or_id: str, mode: str, group_list: list
    ) -> bool:
        """
        Logic kiểm tra danh sách dùng chung.

        Whitelist rỗng nghĩa là cấp này chưa bật; nếu không rỗng chỉ cho item
        trong danh sách. Blacklist chặn item trong danh sách; rỗng thì cho tất cả.
        """
        group_list = [str(x).strip() for x in group_list]
        target = str(group_umo_or_id).strip()

        if mode == "whitelist":
            if not group_list:
                # Whitelist rỗng: cấp này chưa bật.
                return False
            return any(self._is_group_match(target, item) for item in group_list)
        else:  # blacklist
            if not group_list:
                # Blacklist rỗng: cho tất cả.
                return True
            return not any(self._is_group_match(target, item) for item in group_list)

    def set_min_messages_threshold(self, threshold: int):
        """Thiết lập ngưỡng tin nhắn tối thiểu."""
        self._ensure_group("basic")["min_messages_threshold"] = threshold
        self.config.save_config()

    def set_topic_analysis_enabled(self, enabled: bool):
        """Bật hoặc tắt phân tích chủ đề."""
        self._ensure_group("analysis_features")["topic_analysis_enabled"] = enabled
        self.config.save_config()

    def set_user_title_analysis_enabled(self, enabled: bool):
        """Bật hoặc tắt phân tích danh hiệu."""
        self._ensure_group("analysis_features")["user_title_analysis_enabled"] = enabled
        self.config.save_config()

    def set_golden_quote_analysis_enabled(self, enabled: bool):
        """Bật hoặc tắt phân tích trích dẫn."""
        self._ensure_group("analysis_features")["golden_quote_analysis_enabled"] = (
            enabled
        )
        self.config.save_config()

    def set_chat_quality_analysis_enabled(self, enabled: bool):
        """Bật hoặc tắt phân tích chất lượng."""
        self._ensure_group("analysis_features")["chat_quality_analysis_enabled"] = (
            enabled
        )
        self.config.save_config()

    def set_max_topics(self, count: int):
        """Thiết lập số chủ đề tối đa."""
        self._ensure_group("analysis_features")["max_topics"] = count
        self.config.save_config()

    def set_max_user_titles(self, count: int):
        """Thiết lập số danh hiệu tối đa."""
        self._ensure_group("analysis_features")["max_user_titles"] = count
        self.config.save_config()

    def set_max_golden_quotes(self, count: int):
        """Thiết lập số trích dẫn tối đa."""
        self._ensure_group("analysis_features")["max_golden_quotes"] = count
        self.config.save_config()

    def set_html_filename_format(self, format_str: str):
        """Thiết lập định dạng tên tệp HTML."""
        self._ensure_group("html")["html_filename_format"] = format_str
        self.config.save_config()

    def get_report_template(self) -> str:
        """Lấy tên template báo cáo."""
        return self._get_group("basic").get("report_template", "scrapbook")

    def set_report_template(self, template_name: str):
        """Thiết lập tên template báo cáo."""
        self._ensure_group("basic")["report_template"] = template_name
        self.config.save_config()

    def get_enable_user_card(self) -> bool:
        """Kiểm tra có dùng tên thành viên trong nhóm hay không."""
        return self._get_group("basic").get("enable_user_card", False)

    def get_enable_analysis_reply(self) -> bool:
        """Kiểm tra có gửi phản hồi văn bản sau phân tích hay không."""
        return self._get_group("basic").get("enable_analysis_reply", False)

    def set_enable_analysis_reply(self, enabled: bool):
        """Thiết lập gửi phản hồi văn bản sau phân tích."""
        self._ensure_group("basic")["enable_analysis_reply"] = enabled
        self.config.save_config()

    def get_show_report_caption(self) -> bool:
        """Kiểm tra có gửi caption báo cáo hay không."""
        return self._get_group("basic").get("show_report_caption", True)

    def set_show_report_caption(self, enabled: bool):
        """Thiết lập gửi caption báo cáo."""
        self._ensure_group("basic")["show_report_caption"] = enabled
        self.config.save_config()

    def get_profile_display_mode(self) -> str:
        """Lấy chế độ hiển thị nhãn hồ sơ."""
        mode = str(self._get_group("basic").get("profile_display_mode", "mbti")).lower()
        if mode not in {"mbti", "sbti", "acgti"}:
            return "mbti"
        return mode

    def get_profile_image_opacity(self) -> float:
        """Lấy độ trong suốt ảnh nền hồ sơ."""
        value = self._get_group("basic").get("profile_image_opacity", 0.12)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.12

    def get_profile_image_size_mode(self) -> str:
        """Lấy chế độ kích thước ảnh nền hồ sơ."""
        mode = str(
            self._get_group("basic").get("profile_image_size_mode", "contain")
        ).lower()
        if mode not in {"contain", "cover"}:
            return "contain"
        return mode

    def get_profile_mapping_config(self) -> str:
        """Lấy cấu hình ánh xạ hồ sơ dưới dạng văn bản JSON."""
        return str(self._get_group("basic").get("profile_mapping_config", "")).strip()

    # ========== Cấu hình upload tệp/album nhóm ==========

    def get_enable_group_file_upload(self) -> bool:
        """Kiểm tra upload tệp nhóm có bật hay không."""
        return self._get_group("qq_group_upload").get("enable_group_file_upload", False)

    def get_group_file_folder(self) -> str:
        """Lấy tên thư mục upload tệp nhóm; chuỗi rỗng là thư mục gốc."""
        return self._get_group("qq_group_upload").get("group_file_folder", "")

    def get_enable_group_album_upload(self) -> bool:
        """Kiểm tra upload album nhóm có bật hay không, chỉ NapCat."""
        return self._get_group("qq_group_upload").get(
            "enable_group_album_upload", False
        )

    def get_group_album_name(self) -> str:
        """Lấy tên album đích; chuỗi rỗng là album mặc định."""
        return self._get_group("qq_group_upload").get("group_album_name", "")

    def get_group_album_strict_mode(self) -> bool:
        """Lấy trạng thái chế độ upload album nghiêm ngặt."""
        return bool(
            self._get_group("qq_group_upload").get("group_album_strict_mode", True)
        )

    def set_group_album_strict_mode(self, enabled: bool):
        """Thiết lập chế độ upload album nghiêm ngặt."""
        self._ensure_group("qq_group_upload")["group_album_strict_mode"] = enabled
        self.config.save_config()

    # ========== Cấu hình phân tích gia tăng ==========

    def get_incremental_enabled(self) -> bool:
        """Kiểm tra phân tích gia tăng có bật theo trạng thái danh sách hay không."""
        mode = self.get_incremental_group_list_mode()
        lst = self.get_incremental_group_list()
        # Bật khi whitelist không rỗng hoặc đang ở chế độ blacklist.
        return (mode == "whitelist" and len(lst) > 0) or (mode == "blacklist")

    def get_incremental_group_list_mode(self) -> str:
        """Lấy chế độ danh sách phân tích gia tăng."""
        return self._get_group("incremental").get(
            "incremental_group_list_mode", "whitelist"
        )

    def get_incremental_group_list(self) -> list[str]:
        """Lấy danh sách nhóm phân tích gia tăng."""
        return self._get_group("incremental").get("incremental_group_list", [])

    def get_incremental_fallback_enabled(self) -> bool:
        """Lấy trạng thái fallback sang phân tích đầy đủ khi gia tăng thất bại."""
        return self._get_group("incremental").get("incremental_fallback_enabled", True)

    def get_incremental_report_immediately(self) -> bool:
        """Kiểm tra có gửi ngay báo cáo gia tăng hay không, dùng để debug."""
        return self._get_group("incremental").get(
            "incremental_report_immediately", False
        )

    def set_incremental_report_immediately(self, enabled: bool):
        """Thiết lập gửi ngay báo cáo phân tích gia tăng."""
        self._ensure_group("incremental")["incremental_report_immediately"] = enabled
        self.config.save_config()

    def get_incremental_interval_minutes(self) -> int:
        """Lấy khoảng cách phân tích gia tăng, tính bằng phút."""
        return self._get_group("incremental").get("incremental_interval_minutes", 120)

    def get_incremental_max_daily_analyses(self) -> int:
        """Lấy số lần phân tích gia tăng tối đa mỗi ngày."""
        return self._get_group("incremental").get("incremental_max_daily_analyses", 8)

    def get_incremental_safe_limit(self) -> int:
        """Lấy giới hạn phân tích/đồng bộ an toàn mỗi batch gia tăng."""
        return self._get_group("incremental").get("incremental_safe_limit", 2000)

    def get_incremental_min_messages(self) -> int:
        """Lấy ngưỡng tin nhắn tối thiểu để kích hoạt phân tích gia tăng."""
        return self._get_group("incremental").get("incremental_min_messages", 20)

    def get_incremental_topics_per_batch(self) -> int:
        """Lấy số chủ đề tối đa được trích xuất mỗi batch gia tăng."""
        return self._get_group("incremental").get("incremental_topics_per_batch", 3)

    def get_incremental_quotes_per_batch(self) -> int:
        """Lấy số trích dẫn tối đa được trích xuất mỗi batch gia tăng."""
        return self._get_group("incremental").get("incremental_quotes_per_batch", 3)

    def get_incremental_active_start_hour(self) -> int:
        """Lấy giờ bắt đầu hoạt động gia tăng theo định dạng 24 giờ."""
        return self._get_group("incremental").get("incremental_active_start_hour", 8)

    def get_incremental_active_end_hour(self) -> int:
        """Lấy giờ kết thúc hoạt động gia tăng theo định dạng 24 giờ."""
        return self._get_group("incremental").get("incremental_active_end_hour", 23)

    def get_incremental_stagger_seconds(self) -> int:
        """Lấy khoảng cách tác vụ gia tăng nhiều nhóm để giảm tải API."""
        return self._get_group("incremental").get("incremental_stagger_seconds", 30)

    def save_config(self):
        """Lưu cấu hình vào hệ thống cấu hình AstrBot."""
        try:
            self.config.save_config()
            logger.info("Đã lưu cấu hình")
        except Exception as e:
            logger.error(f"Lưu cấu hình thất bại: {e}")

    def reload_config(self):
        """Tải lại cấu hình."""
        try:
            logger.info("Đang tải lại cấu hình...")
            logger.info("Đã tải lại cấu hình")
        except Exception as e:
            logger.error(f"Tải lại cấu hình thất bại: {e}")
