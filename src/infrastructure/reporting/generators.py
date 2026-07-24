"""Tạo báo cáo phân tích ở nhiều định dạng."""

import asyncio
import base64
import copy
import hashlib
import html
import json
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
import ulid
from diskcache import Cache
from markupsafe import Markup

from ...domain.repositories.report_repository import IReportGenerator
from ...utils.logger import logger
from ...shared.vietnamese_language import sanitize_analysis_result_language
from ..utils.template_utils import render_template
from ..visualization.activity_charts import ActivityVisualizer
from .qq_official_markdown import QQOfficialMarkdownReportGenerator
from .templates import HTMLTemplates

MAX_CONCURRENT_DOWNLOADS = 10
AVATAR_CACHE_EXPIRE_TIME = 259200
TRANSPARENT_IMAGE_DATA_URI = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxIiBoZWlnaHQ9IjEiPjwvc3ZnPg=="
)

DEFAULT_PROFILE_MAPPING = {
    "mbti": {
        "INTJ": {"code": "INTJ", "name_zh": "Kiến trúc sư"},
        "INTP": {"code": "INTP", "name_zh": "Nhà logic học"},
        "ENTJ": {"code": "ENTJ", "name_zh": "Nhà chỉ huy"},
        "ENTP": {"code": "ENTP", "name_zh": "Nhà tranh biện"},
        "INFJ": {"code": "INFJ", "name_zh": "Người cố vấn"},
        "INFP": {"code": "INFP", "name_zh": "Người hòa giải"},
        "ENFJ": {"code": "ENFJ", "name_zh": "Người truyền cảm hứng"},
        "ENFP": {"code": "ENFP", "name_zh": "Người vận động"},
        "ISTJ": {"code": "ISTJ", "name_zh": "Nhà hậu cần"},
        "ISFJ": {"code": "ISFJ", "name_zh": "Người bảo vệ"},
        "ESTJ": {"code": "ESTJ", "name_zh": "Nhà điều hành"},
        "ESTP": {"code": "ESTP", "name_zh": "Doanh nhân"},
        "ISTP": {"code": "ISTP", "name_zh": "Nhà kỹ thuật"},
        "ISFP": {"code": "ISFP", "name_zh": "Nhà thám hiểm"},
        "ESFJ": {"code": "ESFJ", "name_zh": "Người lãnh sự"},
        "ESFP": {"code": "ESFP", "name_zh": "Người trình diễn"},
    },
    "sbti": {
        "INTJ": {"code": "CTRL", "name_zh": "Người kiểm soát", "asset_code": "CTRL"},
        "INTP": {"code": "THIN-K", "name_zh": "Người suy tư", "asset_code": "THIN-K"},
        "ENTJ": {"code": "BOSS", "name_zh": "Thủ lĩnh", "asset_code": "BOSS"},
        "ENTP": {"code": "JOKE-R", "name_zh": "Chú hề", "asset_code": "JOKE-R"},
        "INFJ": {"code": "LOVE-R", "name_zh": "Người đa tình", "asset_code": "LOVE-R"},
        "INFP": {"code": "SOLO", "name_zh": "Kẻ cô độc", "asset_code": "SOLO"},
        "ENFJ": {"code": "THAN-K", "name_zh": "Người biết ơn", "asset_code": "THAN-K"},
        "ENFP": {"code": "GOGO", "name_zh": "Lữ khách", "asset_code": "GOGO"},
        "ISTJ": {"code": "OH-NO", "name_zh": "Người hay lo", "asset_code": "OH-NO"},
        "ISTP": {"code": "POOR", "name_zh": "Người khốn khó", "asset_code": "POOR"},
        "ESTJ": {"code": "SHIT", "name_zh": "Người bất mãn", "asset_code": "SHIT"},
        "ESTP": {"code": "WOC!", "name_zh": "Người kinh ngạc", "asset_code": "WOC"},
        "ISFJ": {"code": "MUM", "name_zh": "Người chăm sóc", "asset_code": "MUM"},
        "ISFP": {"code": "MALO", "name_zh": "Chú khỉ", "asset_code": "MALO"},
        "ESFJ": {"code": "ATM-er", "name_zh": "Nhà tài trợ", "asset_code": "ATM-er"},
        "ESFP": {"code": "SEXY", "name_zh": "Người quyến rũ", "asset_code": "SEXY"},
    },
    "acgti": {
        "INTJ": {"code": "MRTS-X", "name_zh": "Mortis"},
        "INTP": {"code": "KNAN", "name_zh": "Edogawa Conan"},
        "ENTJ": {"code": "SAKI", "name_zh": "Togawa Sakiko"},
        "ENTP": {"code": "CHKA", "name_zh": "Fujiwara Chika"},
        "INFJ": {"code": "DLRS", "name_zh": "Misumi Uika"},
        "INFP": {"code": "BCHI", "name_zh": "Gotoh Hitori"},
        "ENFJ": {"code": "YCYO", "name_zh": "Tsukimi Yachiyo"},
        "ENFP": {"code": "HTMK", "name_zh": "Hatsune Miku"},
        "ISTJ": {"code": "MRTS", "name_zh": "Wakaba Mutsumi"},
        "ISTP": {"code": "AYRE", "name_zh": "Ayanami Rei"},
        "ESTJ": {"code": "MIKT", "name_zh": "Misaka Mikoto"},
        "ESTP": {"code": "ASKA", "name_zh": "Asuka"},
        "ISFJ": {"code": "SOYO", "name_zh": "Nagasaki Soyo"},
        "ISFP": {"code": "LTYI", "name_zh": "Luo Tianyi"},
        "ESFJ": {"code": "ANON", "name_zh": "Chihaya Anon"},
        "ESFP": {"code": "FRNA", "name_zh": "Furina"},
    },
}


DEFAULT_PROFILE_NAME_TRANSLATIONS = {
    "建筑师": "Kiến trúc sư",
    "逻辑学家": "Nhà logic học",
    "指挥官": "Nhà chỉ huy",
    "辩论家": "Nhà tranh biện",
    "提倡者": "Người cố vấn",
    "调停者": "Người hòa giải",
    "主人公": "Người truyền cảm hứng",
    "竞选者": "Người vận động",
    "物流师": "Nhà hậu cần",
    "守卫者": "Người bảo vệ",
    "总经理": "Nhà điều hành",
    "企业家": "Doanh nhân",
    "鉴赏家": "Nhà kỹ thuật",
    "探险家": "Nhà thám hiểm",
    "执政官": "Người lãnh sự",
    "表演者": "Người trình diễn",
    "拿捏者": "Người kiểm soát",
    "思考者": "Người suy tư",
    "领导者": "Thủ lĩnh",
    "小丑": "Chú hề",
    "多情者": "Người đa tình",
    "孤儿": "Kẻ cô độc",
    "感恩者": "Người biết ơn",
    "行者": "Lữ khách",
    "哦不人": "Người hay lo",
    "贫困者": "Người khốn khó",
    "愤世者": "Người bất mãn",
    "握草人": "Người kinh ngạc",
    "妈妈": "Người chăm sóc",
    "吗喽": "Chú khỉ",
    "送钱者": "Nhà tài trợ",
    "尤物": "Người quyến rũ",
    "江户川柯南": "Edogawa Conan",
    "丰川祥子": "Togawa Sakiko",
    "藤原千花": "Fujiwara Chika",
    "三角初华": "Misumi Uika",
    "后藤一里": "Gotoh Hitori",
    "月见八千代": "Tsukimi Yachiyo",
    "初音未来": "Hatsune Miku",
    "若叶睦": "Wakaba Mutsumi",
    "绫波丽": "Ayanami Rei",
    "御坂美琴": "Misaka Mikoto",
    "明日香": "Asuka",
    "长崎爽世": "Nagasaki Soyo",
    "洛天依": "Luo Tianyi",
    "千早爱音": "Chihaya Anon",
    "芙宁娜": "Furina",
}


class ReportGenerator(IReportGenerator):
    """Trình tạo báo cáo phân tích."""

    def __init__(self, config_manager, data_dir):
        self._avatar_session = None
        self.config_manager = config_manager
        self.data_dir = data_dir
        self.activity_visualizer = ActivityVisualizer()
        self.html_templates = HTMLTemplates(config_manager)
        # Semaphore render T2I toàn cục bảo vệ tài nguyên local.
        max_concurrent = self.config_manager.get_t2i_max_concurrent()
        self._render_semaphore = asyncio.Semaphore(max_concurrent)
        self._qq_official_markdown_generator = QQOfficialMarkdownReportGenerator(
            config_manager,
            self.html_templates,
            self._render_semaphore,
        )

        # Cache runtime để tránh tải lặp avatar trong một tác vụ phân tích.
        self._avatar_cache = Cache(
            str(self.data_dir / "avatar")
        )  # user_id -> base64_uri
        self._avatar_session_concurrent_semaphore = asyncio.Semaphore(
            MAX_CONCURRENT_DOWNLOADS
        )
        self._avatar_session = None
        self._profile_asset_manifest = self._load_profile_asset_manifest()

    @staticmethod
    def _enforce_vietnamese_report_content(analysis_result: dict) -> None:
        """Loại nội dung sinh còn chữ Hán trước mọi đường kết xuất báo cáo."""
        removed_fields = sanitize_analysis_result_language(analysis_result)
        if removed_fields:
            logger.warning(
                "Đã loại %s trường không phải tiếng Việt trước khi tạo báo cáo: %s",
                len(removed_fields),
                ", ".join(removed_fields),
            )

    def _load_profile_asset_manifest(self) -> dict[str, dict]:
        """Tải manifest tài nguyên hồ sơ."""
        manifest_path = (
            Path(__file__).resolve().parents[3]
            / "assets"
            / "profile_assets"
            / "manifest.json"
        )
        if not manifest_path.exists():
            logger.warning(f"Manifest tài nguyên hồ sơ không tồn tại: {manifest_path}")
            return {"sbti": {}, "acgti": {}}

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.warning(f"Tải manifest tài nguyên hồ sơ thất bại: {e}")
            return {"sbti": {}, "acgti": {}}

        manifest: dict[str, dict] = {"sbti": {}, "acgti": {}}
        for item in raw.get("sbti", []):
            code = str(item.get("code", "")).strip()
            if code:
                manifest["sbti"][code] = item
        for item in raw.get("acgti", []):
            code = str(item.get("code", "")).strip()
            if code:
                manifest["acgti"][code] = item
        return manifest

    def _get_profile_mapping_overrides(self) -> dict[str, dict]:
        """Parse cấu hình override ánh xạ hồ sơ của người dùng."""
        raw = self.config_manager.get_profile_mapping_config()
        if not raw:
            return {}

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for profiles in data.values():
                    if not isinstance(profiles, dict):
                        continue
                    for profile in profiles.values():
                        if not isinstance(profile, dict):
                            continue
                        current_name = str(profile.get("name_zh", "")).strip()
                        translated_name = DEFAULT_PROFILE_NAME_TRANSLATIONS.get(
                            current_name
                        )
                        if translated_name:
                            profile["name_zh"] = translated_name
                return data
        except Exception as e:
            logger.warning(
                f"Parse JSON ánh xạ hồ sơ thất bại, dùng ánh xạ mặc định: {e}"
            )
        return {}

    def _build_profile_image_from_manifest_pattern(
        self, profile_mode: str, asset_code: str
    ) -> str:
        """Suy ra URL ảnh theo mẫu path khi manifest thiếu code cụ thể."""
        system_manifest = self._profile_asset_manifest.get(profile_mode, {})
        for item in system_manifest.values():
            if not isinstance(item, dict):
                continue
            sample_code = str(item.get("code", "")).strip()
            sample_file = str(item.get("file", "")).strip()
            if not sample_code or not sample_file:
                continue
            code_token = f"/{sample_code}."
            if code_token not in sample_file:
                continue
            return sample_file.replace(code_token, f"/{asset_code}.", 1)
        return ""

    def _get_manifest_profile_item_by_mbti(
        self, profile_mode: str, mbti: str
    ) -> dict | None:
        """Tìm tài nguyên khả dụng trong manifest theo MBTI."""
        normalized_mbti = str(mbti or "").strip().upper()
        system_manifest = self._profile_asset_manifest.get(profile_mode, {})
        for item in system_manifest.values():
            if not isinstance(item, dict):
                continue
            item_mbti = str(item.get("mbti", "")).strip().upper()
            if item_mbti == normalized_mbti:
                return item
        return None

    def _resolve_profile_info(
        self,
        mbti: str,
        profile_mode: str,
        overrides: dict[str, dict],
    ) -> dict[str, str | float]:
        """Phân giải thông tin nhãn hồ sơ theo chế độ hiển thị hiện tại."""
        normalized_mbti = str(mbti or "").strip().upper()

        # 1. Lấy thuộc tính cốt lõi từ mặc định hoặc override.
        profile_defaults = DEFAULT_PROFILE_MAPPING.get(profile_mode, {})
        base_info = dict(profile_defaults.get(normalized_mbti, {}))

        # Override người dùng có ưu tiên cao nhất.
        user_override = overrides.get(profile_mode, {}).get(normalized_mbti, {})
        if isinstance(user_override, dict):
            base_info.update(user_override)

        code = str(base_info.get("code", normalized_mbti)).strip() or normalized_mbti
        name_zh = str(base_info.get("name_zh", "")).strip()
        asset_code = str(base_info.get("asset_code", code)).strip() or code
        image = str(base_info.get("image", "")).strip()

        # 2. Bổ sung ảnh và thuộc tính từ manifest.json.
        if not image:
            system_manifest = self._profile_asset_manifest.get(profile_mode, {})
            # A. Ưu tiên index theo asset_code.
            asset_item = system_manifest.get(asset_code)
            if isinstance(asset_item, dict):
                image = str(asset_item.get("file", "")).strip()
                if not name_zh:
                    name_zh = str(asset_item.get("name", "")).strip()

            # B. Suy ra URL ảnh theo mẫu tài nguyên asset_code.
            if not image:
                image = self._build_profile_image_from_manifest_pattern(
                    profile_mode, asset_code
                )

            # C. Với acgti, fallback về tài nguyên đầu tiên cùng MBTI.
            if not image and profile_mode == "acgti":
                fallback_item = self._get_manifest_profile_item_by_mbti(
                    profile_mode, normalized_mbti
                )
                if isinstance(fallback_item, dict):
                    image = str(fallback_item.get("file", "")).strip()
                    if not name_zh:
                        name_zh = str(fallback_item.get("name", "")).strip()
                    if not code or code == normalized_mbti:
                        code = str(fallback_item.get("code", code)).strip()

        # 3. Dựng văn bản hiển thị gồm code và tên.
        display = str(base_info.get("display", "")).strip()
        if not display:
            display = f"{code}（{name_zh}）" if name_zh else code

        return {
            "profile_mode": profile_mode,
            "profile_code": code,
            "profile_name_zh": name_zh,
            "profile_display": display,
            "profile_image": image,
            "profile_image_opacity": self.config_manager.get_profile_image_opacity(),
            "profile_image_size_mode": self.config_manager.get_profile_image_size_mode(),
        }

    @staticmethod
    def _sanitize_path_component(name: str) -> str:
        """Làm sạch một thành phần path/tên tệp, chặn traversal và ký tự lỗi."""
        # Chặn thành phần rỗng và ký hiệu path tương đối.
        if not name or name in {".", ".."}:
            raise ValueError(f"Thành phần path không hợp lệ: {name!r}")

        # Không cho phép ký tự phân cách path.
        name = name.replace("/", "_")
        name = name.replace("\\", "_")

        # Loại ký tự không in được và ký tự tên tệp không hợp lệ.
        name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)

        # Giữ nội dung hợp lệ sau khi làm sạch.
        name = name.strip()
        if not name:
            raise ValueError("Thành phần path rỗng sau khi làm sạch")

        return name

    def _build_safe_report_path(
        self,
        output_dir: Path,
        filename_format: str,
        group_id: str,
        date: str,
    ) -> Path:
        """Dựng path output an toàn theo format, hỗ trợ thư mục con và ulid."""
        generated_ulid = str(ulid.new())
        safe_context = {
            "group_id": group_id,
            "date": date,
            "ulid": generated_ulid,
        }

        try:
            formatted = render_template(filename_format, strict=True, **safe_context)
        except Exception as e:
            raise ValueError(f"Render template tên tệp thất bại: {e}") from e

        if os.path.isabs(formatted):
            raise ValueError("Định dạng tên tệp không được là path tuyệt đối")

        relative_path = Path(formatted)
        sanitized_parts = []
        for part in relative_path.parts:
            if part in {".", ".."}:
                raise ValueError("Path không được chứa '.' hoặc '..'")
            sanitized_parts.append(self._sanitize_path_component(part))

        safe_relative = Path(*sanitized_parts)

        output_dir_resolved = output_dir.resolve(strict=False)
        target_path = (output_dir_resolved / safe_relative).resolve(strict=False)

        # Chặn traversal lên thư mục cha bằng Path.relative_to.
        try:
            target_path.relative_to(output_dir_resolved)
        except ValueError:
            raise ValueError(
                "Path tệp nằm ngoài thư mục output, có thể chứa path traversal"
            )

        # Thêm hậu tố ULID để tránh ghi đè khi format không có định danh duy nhất.
        if target_path.exists():
            suffix = target_path.suffix
            stem = target_path.stem
            target_path = target_path.with_name(f"{stem}_{generated_ulid}{suffix}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        return target_path

    async def generate_image_report(
        self,
        analysis_result: dict,
        group_id: str,
        html_render_func,
        avatar_url_getter=None,
        nickname_getter=None,
        avatar_cache_namespace: str | None = None,
        hide_user_names: bool = False,
        # Đồng thời kiểm soát chuẩn hoá ID và tên hiển thị fallback.
        allow_alphanumeric_user_ids: bool = False,
    ) -> tuple[str | None, str | None]:
        """
        Tạo báo cáo phân tích dạng ảnh.

        Args:
            analysis_result: Dict kết quả phân tích.
            group_id: ID nhóm.
            html_render_func: Hàm render HTML.
            avatar_url_getter: Callback async lấy avatar theo user_id.
            nickname_getter: Hàm lấy nickname.

        Returns:
            tuple[str | None, str | None]: (image_url, html_content)
        """
        html_content = None
        try:
            self._enforce_vietnamese_report_content(analysis_result)
            # Chuẩn bị dữ liệu render.
            render_payload = await self._prepare_render_data(
                analysis_result,
                chart_template="activity_chart.html",
                avatar_url_getter=avatar_url_getter,
                nickname_getter=nickname_getter,
                avatar_cache_namespace=avatar_cache_namespace,
                hide_user_names=hide_user_names,
                allow_alphanumeric_user_ids=allow_alphanumeric_user_ids,
            )

            # Render template HTML bằng Jinja2.
            html_content = self.html_templates.render_template(
                "image_template.html", **render_payload
            )
            html_content = self._reuse_avatars_in_final_html(
                html_content,
                render_payload.get("avatar_reuse_registry", {}),
                render_payload.get("avatar_reuse_aliases", {}),
            )

            # Kiểm tra nội dung HTML hợp lệ.
            if not html_content:
                logger.error("Render HTML báo cáo ảnh thất bại: nội dung rỗng")
                return None, None

            logger.info(
                f"Render HTML báo cáo ảnh hoàn tất, độ dài: {len(html_content)} ký tự"
            )

            # Lấy hai chiến lược render từ cấu hình.
            render_strategies = self.config_manager.get_t2i_rendering_strategies()

            # Dùng semaphore kiểm soát concurrency render.
            async with self._render_semaphore:
                logger.debug(f"[T2I] Đã vào hàng đợi render (nhóm: {group_id})")

                last_exception = None

                for attempt, image_options in enumerate(render_strategies, 1):
                    try:
                        # Cleanse options
                        if image_options.get("type") == "png":
                            image_options.pop("quality", None)

                        logger.info(
                            f"Đang thử chiến lược render lượt {attempt}: {image_options}"
                        )

                        # Lấy bytes để tránh OneBot không truy cập được URL nội bộ.
                        image_data = await html_render_func(
                            html_content,
                            {},
                            False,
                            image_options,
                        )

                        if image_data:
                            # Xác thực ảnh để tránh T2I trả trang lỗi HTML.
                            is_valid = False
                            actual_data_head = None

                            if isinstance(image_data, bytes):
                                actual_data_head = image_data[:10]
                            elif isinstance(image_data, str) and os.path.exists(
                                image_data
                            ):
                                try:
                                    with open(image_data, "rb") as f:
                                        actual_data_head = f.read(10)
                                except Exception as e:
                                    logger.warning(f"Đọc tệp ảnh tạm thất bại: {e}")

                            if actual_data_head:
                                # Kiểm tra magic number JPEG/PNG.
                                if actual_data_head.startswith(
                                    b"\xff\xd8"
                                ) or actual_data_head.startswith(b"\x89PNG"):
                                    is_valid = True
                                else:
                                    # Thử parse lỗi HTML như 502 Bad Gateway.
                                    html_error = None
                                    if isinstance(image_data, bytes):
                                        html_error = self._extract_html_error_summary(
                                            image_data
                                        )
                                    elif isinstance(image_data, str) and os.path.exists(
                                        image_data
                                    ):
                                        try:
                                            with open(image_data, "rb") as f:
                                                # 4 KB đầu đủ để nhận diện lỗi HTML.
                                                html_error = (
                                                    self._extract_html_error_summary(
                                                        f.read(4096)
                                                    )
                                                )
                                        except Exception:
                                            pass

                                    if html_error:
                                        logger.warning(
                                            f"[T2I] Engine render trả trang lỗi thay vì ảnh: {html_error}"
                                        )
                                    else:
                                        logger.warning(
                                            f"Kết quả render có vẻ không phải ảnh hợp lệ (header: {actual_data_head.hex()})"
                                        )

                            if is_valid:
                                if isinstance(image_data, bytes):
                                    b64 = base64.b64encode(image_data).decode("utf-8")
                                    image_url = f"base64://{b64}"
                                    logger.info(
                                        f"Tạo ảnh thành công (lượt {attempt}): [Base64 Data {len(image_data)} bytes]"
                                    )
                                    return image_url, html_content
                                elif isinstance(image_data, str):
                                    logger.info(
                                        f"Tạo ảnh thành công (lượt {attempt}): {image_data}"
                                    )
                                    return image_data, html_content

                        logger.warning(
                            f"Lượt render {attempt} ({image_options['type']}) trả dữ liệu rỗng hoặc không hợp lệ"
                        )

                    except Exception as e:
                        logger.warning(f"Lượt render {attempt} thất bại: {e}")
                        last_exception = e
                        if attempt < len(render_strategies):
                            logger.info("Chuẩn bị thử chiến lược fallback tiếp theo")
                        continue

                # Mọi chiến lược đều thất bại.
                logger.error(f"Mọi lần render đều thất bại. Lỗi cuối: {last_exception}")
                return None, html_content

        except Exception as e:
            logger.error(f"Lỗi nghiêm trọng khi tạo báo cáo ảnh: {e}", exc_info=True)
            return None, html_content
        finally:
            # Dọn session và cache của lần chạy này.
            if self._avatar_session:
                await self._avatar_session.close()
                self._avatar_session = None

    async def generate_html_report(
        self,
        analysis_result: dict,
        group_id: str,
        avatar_url_getter=None,
        nickname_getter=None,
        avatar_cache_namespace: str | None = None,
        hide_user_names: bool = False,
        allow_alphanumeric_user_ids: bool = False,
    ) -> tuple[str | None, str | None]:
        """
        Tạo báo cáo HTML và lưu vào thư mục chỉ định.

        Args:
            analysis_result: Dict kết quả phân tích.
            group_id: ID nhóm.
            avatar_url_getter: Callback async lấy avatar theo user_id.
            nickname_getter: Hàm lấy nickname.

        Returns:
            Tuple path tệp HTML và JSON.
        """
        try:
            self._enforce_vietnamese_report_content(analysis_result)
            import json

            # Đảm bảo thư mục output tồn tại mà không block event loop.
            output_dir = Path(self.config_manager.get_html_output_dir())
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

            # Tạo path tệp.
            current_date = datetime.now().strftime("%Y%m%d")
            base_html_path = self._build_safe_report_path(
                output_dir,
                self.config_manager.get_html_filename_format(),
                group_id=group_id,
                date=current_date,
            )

            html_path = base_html_path
            if not html_path.suffix:
                html_path = html_path.with_suffix(".html")

            json_path = html_path.with_suffix(".json")

            html_path.parent.mkdir(parents=True, exist_ok=True)

            # Chuẩn bị dữ liệu render.
            render_data = await self._prepare_render_data(
                analysis_result,
                chart_template="activity_chart.html",
                avatar_url_getter=avatar_url_getter,
                nickname_getter=nickname_getter,
                avatar_cache_namespace=avatar_cache_namespace,
                hide_user_names=hide_user_names,
                allow_alphanumeric_user_ids=allow_alphanumeric_user_ids,
            )
            logger.info(
                f"Chuẩn bị dữ liệu render HTML hoàn tất, gồm {len(render_data)} trường"
            )

            # Render bằng html_template.html, fallback sang image_template.html.
            html_content = None
            try:
                html_content = self.html_templates.render_template(
                    "html_template.html", **render_data
                )
                html_content = self._reuse_avatars_in_final_html(
                    html_content,
                    render_data.get("avatar_reuse_registry", {}),
                    render_data.get("avatar_reuse_aliases", {}),
                )
                logger.info("Render bằng html_template.html thành công")
            except Exception as e:
                logger.warning(
                    f"html_template.html không tồn tại hoặc render lỗi, fallback sang image_template.html: {e}"
                )
                html_content = self.html_templates.render_template(
                    "image_template.html", **render_data
                )
                html_content = self._reuse_avatars_in_final_html(
                    html_content,
                    render_data.get("avatar_reuse_registry", {}),
                    render_data.get("avatar_reuse_aliases", {}),
                )
                logger.info("Render bằng image_template.html thành công")

            # Kiểm tra nội dung HTML hợp lệ.
            if not html_content:
                logger.error("Render báo cáo HTML thất bại: nội dung rỗng")
                return None, None

            logger.info(
                f"Tạo nội dung HTML hoàn tất, độ dài: {len(html_content)} ký tự"
            )

            # Lưu tệp HTML.
            await asyncio.to_thread(
                html_path.write_text, html_content, encoding="utf-8"
            )
            logger.info(f"Đã lưu báo cáo HTML: {html_path}")

            def json_default_encoder(obj):
                if hasattr(obj, "to_dict") and callable(obj.to_dict):
                    return obj.to_dict()
                if is_dataclass(obj) and not isinstance(obj, type):
                    return asdict(obj)
                if isinstance(obj, (datetime, date)):
                    return obj.isoformat()
                if isinstance(obj, Enum):
                    return obj.value
                if isinstance(obj, (set, tuple)):
                    return list(obj)
                raise TypeError(
                    f"Object of type {type(obj).__name__} is not JSON serializable"
                )

            # Lưu dữ liệu JSON gốc.
            json_data = {
                "analysis_result": (
                    self._sanitize_analysis_result_for_export(analysis_result)
                    if hide_user_names or allow_alphanumeric_user_ids
                    else analysis_result
                ),
                "group_id": group_id,
                "generated_at": datetime.now().isoformat(),
            }
            await asyncio.to_thread(
                json_path.write_text,
                json.dumps(
                    json_data,
                    ensure_ascii=False,
                    indent=2,
                    default=json_default_encoder,
                ),
                encoding="utf-8",
            )
            logger.info(f"Đã lưu dữ liệu JSON: {json_path}")

            return str(html_path.absolute()), str(json_path.absolute())

        except Exception as e:
            logger.error(f"Tạo báo cáo HTML thất bại: {e}", exc_info=True)
            return None, None

    def build_html_caption(self, html_path: str) -> str:
        """Tạo caption liên kết báo cáo từ html_base_url."""

        caption = "📊 Đã tạo báo cáo phân tích nhóm hàng ngày"
        base_url = self.config_manager.get_html_base_url()
        if not base_url or not html_path:
            return caption

        # Giữ path tương đối để hỗ trợ thư mục con trong format tên tệp.
        output_dir = Path(self.config_manager.get_html_output_dir()).resolve(
            strict=False
        )
        try:
            relative_path = (
                Path(html_path).resolve(strict=False).relative_to(output_dir)
            )
            relative_url = str(relative_path).replace(os.sep, "/")
        except Exception:
            relative_url = Path(html_path).name

        encoded_relative_url = quote(relative_url, safe="/")
        return caption + f"\n{base_url.rstrip('/')}/{encoded_relative_url}"

    def generate_text_report(self, analysis_result: dict) -> str:
        """Tạo báo cáo phân tích dạng văn bản."""
        self._enforce_vietnamese_report_content(analysis_result)
        stats = analysis_result["statistics"]
        topics = analysis_result["topics"]
        user_titles = analysis_result["user_titles"]

        report = f"""
🎯 Báo cáo phân tích nhóm hàng ngày
📅 {datetime.now().strftime("%d/%m/%Y")}

📊 Thống kê cơ bản
• Tổng số tin nhắn: {stats.message_count}
• Số người tham gia: {stats.participant_count}
• Tổng số ký tự: {stats.total_characters}
• Số biểu cảm: {stats.emoji_count}
• Khung giờ sôi nổi nhất: {stats.most_active_period}

💬 Chủ đề nổi bật
"""

        max_topics = self.config_manager.get_max_topics()
        for i, topic in enumerate(topics[:max_topics], 1):
            contributors_str = ", ".join(topic.contributors)
            report += f"{i}. {topic.topic}\n"
            report += f"   Người tham gia: {contributors_str}\n"
            report += f"   {topic.detail}\n\n"

        report += "🏆 Danh hiệu thành viên\n"
        max_user_titles = self.config_manager.get_max_user_titles()
        for title in user_titles[:max_user_titles]:
            report += f"• {title.name} - {title.title} ({title.mbti})\n"
            report += f"  {title.reason}\n\n"

        report += "💬 Trích dẫn nổi bật\n"
        max_golden_quotes = self.config_manager.get_max_golden_quotes()
        for i, golden_quote in enumerate(stats.golden_quotes[:max_golden_quotes], 1):
            report += f'{i}. "{golden_quote.content}" —— {golden_quote.sender}\n'
            report += f"   {golden_quote.reason}\n\n"

        return report

    async def generate_qq_official_markdown_report(
        self, analysis_result: dict, html_render_func=None
    ) -> tuple[str, str]:
        """Delegate QQ-only text generation to the platform-specific module."""
        self._enforce_vietnamese_report_content(analysis_result)
        generator = getattr(self, "_qq_official_markdown_generator", None)
        if generator is None:
            generator = QQOfficialMarkdownReportGenerator(
                self.config_manager,
                getattr(self, "html_templates", None),
                getattr(self, "_render_semaphore", None),
            )
            self._qq_official_markdown_generator = generator
        return await generator.generate(
            analysis_result,
            html_render_func,
        )

    def _sanitize_analysis_result_for_export(
        self, analysis_result: dict
    ) -> dict[str, Any]:
        """Remove platform identities from the HTML sidecar JSON export."""
        sanitized = self._to_plain_export_data(copy.deepcopy(analysis_result))
        sanitized["user_analysis"] = {}
        for topic in sanitized.get("topics", []):
            if not isinstance(topic, dict):
                continue
            topic["contributors"] = []
            topic["contributor_ids"] = []
        for title in sanitized.get("user_titles", []):
            if not isinstance(title, dict):
                continue
            title["name"] = ""
            title["user_id"] = ""
        stats = sanitized.get("statistics")
        if isinstance(stats, dict):
            for golden_quote in stats.get("golden_quotes", []) or []:
                if not isinstance(golden_quote, dict):
                    continue
                golden_quote["sender"] = ""
                golden_quote["user_id"] = ""

            activity_visualization = stats.get("activity_visualization")
            if isinstance(activity_visualization, dict):
                activity_visualization["user_activity_ranking"] = []

        for golden_quote in sanitized.get("golden_quotes", []) or []:
            if not isinstance(golden_quote, dict):
                continue
            golden_quote["sender"] = ""
            golden_quote["user_id"] = ""

        return self._sanitize_export_identity_text(sanitized, analysis_result)  # type: ignore[return-type]

    @classmethod
    def _to_plain_export_data(cls, value):
        """Convert report models into plain containers before privacy filtering."""
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return cls._to_plain_export_data(value.to_dict())
        if is_dataclass(value) and not isinstance(value, type):
            return cls._to_plain_export_data(asdict(value))
        if isinstance(value, dict):
            return {key: cls._to_plain_export_data(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._to_plain_export_data(item) for item in value]
        return value

    def _sanitize_export_identity_text(self, value, analysis_result: dict):
        """Remove known IDs and display names from every exported text field."""
        if isinstance(value, str):
            return self._sanitize_identity_text(value, analysis_result, True)
        if isinstance(value, dict):
            return {
                key: self._sanitize_export_identity_text(item, analysis_result)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._sanitize_export_identity_text(item, analysis_result)
                for item in value
            ]
        return value

    async def _prepare_render_data(
        self,
        analysis_result: dict,
        chart_template: str = "activity_chart.html",
        avatar_url_getter=None,
        nickname_getter=None,
        avatar_cache_namespace: str | None = None,
        hide_user_names: bool = False,
        allow_alphanumeric_user_ids: bool = False,
    ) -> dict:
        """Chuẩn bị dữ liệu render."""
        stats = analysis_result["statistics"]
        topics = analysis_result["topics"]
        user_titles = analysis_result["user_titles"]
        activity_viz = stats.activity_visualization

        # Dựng HTML chủ đề hàng loạt bằng Jinja2.
        max_topics = self.config_manager.get_max_topics()
        topics_list = []
        user_analysis = analysis_result.get("user_analysis")
        avatar_reuse_registry: dict[str, str] = {}
        avatar_reuse_aliases: dict[str, str] = {}

        for i, topic in enumerate(topics[:max_topics], 1):
            # Xử lý avatar trong tham chiếu người dùng của chi tiết chủ đề.
            processed_detail = await self._render_mentions(
                topic.detail,
                avatar_url_getter,
                nickname_getter,
                user_analysis,
                avatar_cache_namespace,
                avatar_reuse_registry,
                avatar_reuse_aliases,
                hide_user_names=hide_user_names,
                allow_alphanumeric_user_ids=allow_alphanumeric_user_ids,
            )
            if hide_user_names:
                contributors = await self._render_avatar_only_ids(
                    getattr(topic, "contributor_ids", []) or [],
                    avatar_url_getter,
                    avatar_cache_namespace,
                    avatar_reuse_registry,
                    avatar_reuse_aliases,
                )
            else:
                contributors = ", ".join(topic.contributors)
            topics_list.append(
                {
                    "index": i,
                    "topic": {
                        "topic": self._sanitize_identity_text(
                            topic.topic, analysis_result, hide_user_names
                        )
                    },
                    "contributors": contributors,
                    "detail": processed_detail,
                }
            )

        # Context chung gồm cấu hình toàn cục dùng bởi template con.
        common_context = {
            "hide_user_names": hide_user_names,
            "t2i_font_source": self.config_manager.get_t2i_font_source(),
            "t2i_google_fonts_mirror": self.config_manager.get_t2i_google_fonts_mirror(),
            "t2i_gstatic_mirror": self.config_manager.get_t2i_gstatic_mirror(),
            "t2i_atri_font_mirror": self.config_manager.get_t2i_atri_font_mirror(),
        }

        topics_html = self.html_templates.render_template(
            "topic_item.html", topics=topics_list, **common_context
        )
        logger.info(f"Tạo HTML chủ đề hoàn tất, độ dài: {len(topics_html)}")

        # Dựng HTML danh hiệu hàng loạt bằng Jinja2, gồm avatar.
        max_user_titles = self.config_manager.get_max_user_titles()
        titles_list = []
        profile_mode = self.config_manager.get_profile_display_mode()
        profile_mapping_overrides = self._get_profile_mapping_overrides()
        for title in user_titles[:max_user_titles]:
            user_id = str(title.user_id)
            # Lấy avatar người dùng.
            avatar_data = await self._get_user_avatar(
                user_id, avatar_url_getter, avatar_cache_namespace
            )
            self._register_reusable_avatar(
                avatar_data,
                avatar_reuse_registry,
                avatar_reuse_aliases,
                avatar_key=self._get_avatar_cache_key(user_id, avatar_cache_namespace),
            )
            profile_info = self._resolve_profile_info(
                title.mbti, profile_mode, profile_mapping_overrides
            )
            title_reason = title.reason
            if hide_user_names:
                title_reason = await self._render_mentions(
                    title.reason,
                    avatar_url_getter,
                    nickname_getter,
                    user_analysis,
                    avatar_cache_namespace,
                    avatar_reuse_registry,
                    avatar_reuse_aliases,
                    hide_user_names=True,
                    allow_alphanumeric_user_ids=allow_alphanumeric_user_ids,
                )
            title_data = {
                "name": "" if hide_user_names else title.name,
                "title": title.title,
                "mbti": title.mbti,
                "reason": title_reason,
                "avatar_data": avatar_data,
            }
            title_data.update(profile_info)
            titles_list.append(title_data)

        titles_html = self.html_templates.render_template(
            "user_title_item.html", titles=titles_list, **common_context
        )
        logger.info(f"Tạo HTML danh hiệu hoàn tất, độ dài: {len(titles_html)}")

        # Dựng HTML trích dẫn hàng loạt bằng Jinja2.
        max_golden_quotes = self.config_manager.get_max_golden_quotes()
        quotes_list = []
        for golden_quote in stats.golden_quotes[:max_golden_quotes]:
            quote_user_id = str(golden_quote.user_id) if golden_quote.user_id else None
            avatar_url = (
                await self._get_user_avatar(
                    quote_user_id,
                    avatar_url_getter,
                    avatar_cache_namespace,
                )
                if quote_user_id
                else None
            )
            if quote_user_id:
                self._register_reusable_avatar(
                    avatar_url,
                    avatar_reuse_registry,
                    avatar_reuse_aliases,
                    avatar_key=self._get_avatar_cache_key(
                        quote_user_id, avatar_cache_namespace
                    ),
                )
            # Xử lý avatar trong tham chiếu người dùng của nhận xét.
            processed_reason = await self._render_mentions(
                golden_quote.reason,
                avatar_url_getter,
                nickname_getter,
                user_analysis,
                avatar_cache_namespace,
                avatar_reuse_registry,
                avatar_reuse_aliases,
                hide_user_names=hide_user_names,
                allow_alphanumeric_user_ids=allow_alphanumeric_user_ids,
            )
            quotes_list.append(
                {
                    "content": self._sanitize_identity_text(
                        golden_quote.content, analysis_result, hide_user_names
                    ),
                    "sender": "" if hide_user_names else golden_quote.sender,
                    "reason": processed_reason,
                    "avatar_url": avatar_url,
                }
            )

        quotes_html = self.html_templates.render_template(
            "quote_item.html", quotes=quotes_list, **common_context
        )
        logger.info(f"Tạo HTML trích dẫn hoàn tất, độ dài: {len(quotes_html)}")

        # Tạo HTML biểu đồ hoạt động.
        chart_data = self.activity_visualizer.get_hourly_chart_data(
            activity_viz.hourly_activity
        )
        hourly_chart_html = self.html_templates.render_template(
            chart_template, chart_data=chart_data, **common_context
        )
        logger.info(
            f"Tạo HTML biểu đồ hoạt động hoàn tất, độ dài: {len(hourly_chart_html)}"
        )

        # Tạo HTML đánh giá chất lượng trò chuyện.
        chat_quality_html = ""
        chat_quality_review = analysis_result.get("chat_quality_review")
        if not chat_quality_review and hasattr(stats, "chat_quality_review"):
            chat_quality_review = stats.chat_quality_review

        if chat_quality_review:
            # Chuyển object thành dict để render thống nhất.
            if hasattr(chat_quality_review, "dimensions"):
                review_data = {
                    "title": chat_quality_review.title,
                    "subtitle": chat_quality_review.subtitle,
                    "dimensions": [
                        {
                            "name": d.name,
                            "percentage": d.percentage,
                            "comment": d.comment,
                            "color": d.color,
                        }
                        for d in chat_quality_review.dimensions
                    ],
                    "summary": chat_quality_review.summary,
                }
            else:
                review_data = chat_quality_review

            if hide_user_names and isinstance(review_data, dict):
                review_data = {
                    **review_data,
                    "title": self._sanitize_identity_text(
                        review_data.get("title", ""), analysis_result, True
                    ),
                    "subtitle": self._sanitize_identity_text(
                        review_data.get("subtitle", ""), analysis_result, True
                    ),
                    "summary": self._sanitize_identity_text(
                        review_data.get("summary", ""), analysis_result, True
                    ),
                    "dimensions": [
                        {
                            **dimension,
                            "name": self._sanitize_identity_text(
                                dimension.get("name", ""), analysis_result, True
                            ),
                            "comment": self._sanitize_identity_text(
                                dimension.get("comment", ""), analysis_result, True
                            ),
                        }
                        for dimension in review_data.get("dimensions", [])
                        if isinstance(dimension, dict)
                    ],
                }

            chat_quality_html = self.html_templates.render_template(
                "chat_quality_item.html", **review_data, **common_context
            )
            logger.info(
                f"Tạo HTML chất lượng trò chuyện hoàn tất, độ dài: {len(chat_quality_html)}"
            )

        # Chuẩn bị dữ liệu render cuối.
        render_data = {
            "t2i_font_source": self.config_manager.get_t2i_font_source(),
            "t2i_google_fonts_mirror": self.config_manager.get_t2i_google_fonts_mirror(),
            "t2i_gstatic_mirror": self.config_manager.get_t2i_gstatic_mirror(),
            "t2i_atri_font_mirror": self.config_manager.get_t2i_atri_font_mirror(),
            "current_date": datetime.now().strftime("%d/%m/%Y"),
            "current_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": stats.message_count,
            "participant_count": stats.participant_count,
            "total_characters": stats.total_characters,
            "emoji_count": stats.emoji_count,
            "most_active_period": stats.most_active_period,
            "topics_html": topics_html,
            "titles_html": titles_html,
            "quotes_html": quotes_html,
            "hourly_chart_html": hourly_chart_html,
            "chat_quality_html": chat_quality_html,
            "total_tokens": stats.token_usage.total_tokens
            if stats.token_usage.total_tokens
            else 0,
            "prompt_tokens": stats.token_usage.prompt_tokens
            if stats.token_usage.prompt_tokens
            else 0,
            "completion_tokens": stats.token_usage.completion_tokens
            if stats.token_usage.completion_tokens
            else 0,
            "avatar_reuse_registry": avatar_reuse_registry,
            "avatar_reuse_aliases": avatar_reuse_aliases,
        }

        logger.info(f"Chuẩn bị dữ liệu render hoàn tất, gồm {len(render_data)} trường")
        return render_data

    async def _render_avatar_only_ids(
        self,
        user_ids: list[str],
        avatar_url_getter,
        avatar_cache_namespace: str | None,
        avatar_reuse_registry: dict[str, str] | None,
        avatar_reuse_aliases: dict[str, str] | None,
    ) -> Markup:
        avatars: list[Markup] = []
        for raw_user_id in user_ids:
            user_id = str(raw_user_id or "").strip()
            if not user_id:
                continue
            avatar_url = await self._get_user_avatar(
                user_id, avatar_url_getter, avatar_cache_namespace
            )
            avatar_ref = self._register_reusable_avatar(
                avatar_url,
                avatar_reuse_registry,
                avatar_reuse_aliases,
                avatar_key=self._get_avatar_cache_key(user_id, avatar_cache_namespace),
            )
            style = (
                "width:24px;height:24px;border-radius:50%;display:inline-block;"
                "vertical-align:middle;margin:0 2px;background-size:cover;"
                "background-position:center;background-repeat:no-repeat;"
            )
            if avatar_ref:
                avatars.append(
                    Markup(
                        f'<span class="user-capsule-avatar" '
                        f'data-avatar-ref="{html.escape(avatar_ref, quote=True)}" '
                        f'style="{style}"></span>'
                    )
                )
            else:
                avatars.append(
                    Markup(
                        f'<img src="{html.escape(avatar_url, quote=True)}" '
                        f'style="{style}">'
                    )
                )
        return Markup("").join(avatars)

    async def _render_mentions(
        self,
        text: str,
        avatar_url_getter,
        nickname_getter=None,
        user_analysis: dict | None = None,
        avatar_cache_namespace: str | None = None,
        avatar_reuse_registry: dict[str, str] | None = None,
        avatar_reuse_aliases: dict[str, str] | None = None,
        hide_user_names: bool = False,
        allow_alphanumeric_user_ids: bool = False,
    ) -> Markup:
        """
        Thay tham chiếu dạng ``[user ID]`` trong văn bản bằng capsule avatar.
        """
        if not text:
            return Markup("")

        known_ids = {
            str(user_id).strip()
            for user_id in (user_analysis or {})
            if str(user_id).strip()
        }
        source_text = str(text)
        supports_extended_ids = hide_user_names or allow_alphanumeric_user_ids
        if supports_extended_ids:
            # Chuẩn hoá ID do LLM trả trực tiếp thành tham chiếu để ẩn OpenID.
            for user_id in sorted(known_ids, key=len, reverse=True):
                source_text = re.sub(
                    rf"(?<!\[)(?<![A-Za-z0-9_-]){re.escape(user_id)}"
                    rf"(?![A-Za-z0-9_-])(?!\])",
                    f"[{user_id}]",
                    source_text,
                )

        pattern = (
            r"\[([A-Za-z0-9_-]{1,128})\]" if supports_extended_ids else r"\[(\d+)\]"
        )

        matches = list(re.finditer(pattern, source_text))
        if not matches:
            return self._escape_text_segment(source_text)

        async def render_capsule(match: re.Match[str]) -> Markup:
            uid = match.group(1)
            if supports_extended_ids and uid not in known_ids:
                return Markup(html.escape(f"[{uid}]", quote=True))
            url = await self._get_user_avatar(
                uid, avatar_url_getter, avatar_cache_namespace
            )  # Đã có cache nội bộ, không cần concurrency cấp trên.

            name = None
            # 1. Thử lấy từ kết quả phân tích LLM.
            if user_analysis and uid in user_analysis:
                stats = user_analysis[uid]
                name = stats.get("nickname") or stats.get("name")
                if self._is_placeholder_display_name(name, uid):
                    name = None

            # 2. Thử lấy nickname thời gian thực qua callback.
            if not name and nickname_getter:
                try:
                    name = await nickname_getter(uid)
                    if self._is_placeholder_display_name(name, uid):
                        name = None
                except Exception as e:
                    logger.warning(f"Lấy nickname thất bại {uid}: {e}")

            # Dùng thống nhất kiểu capsule.
            capsule_style = (
                "display:inline-flex;align-items:center;background:rgba(0,0,0,0.05);"
                "padding:2px 6px 2px 2px;border-radius:12px;margin:0 2px;"
                "vertical-align:middle;border:1px solid rgba(0,0,0,0.1);text-decoration:none;"
            )
            img_style = (
                "width:18px;height:18px;border-radius:50%;"
                f"margin-right:{'0' if hide_user_names else '4px'};display:block;"
            )
            name_style = "font-size:0.85em;color:inherit;font-weight:500;line-height:1;"

            # 3. Fallback cuối: đảm bảo có avatar và tên.
            final_url = url if url else self._get_default_avatar_base64()
            final_name = (
                name
                if (name and not self._is_placeholder_display_name(name, uid))
                else ("Thành viên nhóm" if allow_alphanumeric_user_ids else str(uid))
            )

            avatar_ref = self._register_reusable_avatar(
                final_url,
                avatar_reuse_registry,
                avatar_reuse_aliases,
                avatar_key=self._get_avatar_cache_key(uid, avatar_cache_namespace),
            )
            if avatar_ref:
                avatar_html = (
                    f'<span class="user-capsule-avatar" '
                    f'data-avatar-ref="{html.escape(avatar_ref, quote=True)}" '
                    f'style="{img_style}background-size:cover;background-position:center;'
                    'background-repeat:no-repeat;flex-shrink:0;"></span>'
                )
            else:
                avatar_html = (
                    f'<img src="{html.escape(final_url, quote=True)}" '
                    f'style="{img_style}">'
                )

            name_html = (
                ""
                if hide_user_names
                else f'<span style="{name_style}">{html.escape(final_name)}</span>'
            )
            return Markup(
                f'<span class="user-capsule" style="{capsule_style}">'
                f"{avatar_html}{name_html}</span>"
            )

        result: list[Markup | str] = []
        last_end = 0
        for match in matches:
            result.append(
                self._escape_text_segment(source_text[last_end : match.start()])
            )
            result.append(await render_capsule(match))
            last_end = match.end()

        result.append(self._escape_text_segment(source_text[last_end:]))
        return Markup("").join(result)

    @staticmethod
    def _sanitize_identity_text(
        text: str, analysis_result: dict, hide_user_names: bool
    ) -> str:
        if not hide_user_names:
            return str(text)
        sanitized = str(text)
        user_analysis = analysis_result.get("user_analysis") or {}
        known_ids = {
            str(user_id).strip() for user_id in user_analysis if str(user_id).strip()
        }
        known_names = set()
        for stats in user_analysis.values():
            if not isinstance(stats, dict):
                continue
            for key in ("nickname", "name"):
                value = str(stats.get(key, "") or "").strip()
                if value:
                    known_names.add(value)
        for identity in sorted(known_ids | known_names, key=len, reverse=True):
            sanitized = sanitized.replace(identity, "")
        return re.sub(r"\[\s*\]", "", sanitized)

    @staticmethod
    def _escape_text_segment(text: str) -> Markup:
        return Markup(html.escape(text, quote=False).replace("\n", "<br>"))

    @staticmethod
    def _is_placeholder_display_name(name: str | None, user_id: str) -> bool:
        """Kiểm tra tên hiển thị có phải placeholder hay không."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized.lower() in {"unknown", "none", "null", "nil", "undefined"}:
            return True
        return normalized == str(user_id).strip()

    @staticmethod
    def _safe_url_for_log(url: str | None) -> str:
        """Che token trong URL ghi log."""
        if not url:
            return ""
        # Telegram file URL: .../file/bot<token>/<file_path>
        return re.sub(r"/bot[^/]+/", "/bot<redacted>/", url)

    @staticmethod
    def _build_avatar_ref(avatar_key: str | None, avatar_url: str) -> str:
        """Tạo tham chiếu avatar ổn định mà không lộ platform hay user ID."""
        if avatar_key:
            digest = hashlib.sha256(avatar_key.encode("utf-8")).hexdigest()[:24]
            return f"avatar-{digest}"

        digest = hashlib.sha256(avatar_url.encode("utf-8")).hexdigest()[:24]
        return f"avatar-{digest}"

    @staticmethod
    def _register_reusable_avatar(
        avatar_url: str | None,
        avatar_reuse_registry: dict[str, str] | None,
        avatar_reuse_aliases: dict[str, str] | None = None,
        avatar_key: str | None = None,
    ) -> str | None:
        """Đăng ký avatar Data URI làm tài nguyên tái sử dụng và trả ID ngắn."""
        if not avatar_url or avatar_reuse_registry is None:
            return None
        if not avatar_url.startswith("data:image/"):
            return None

        if avatar_reuse_aliases and avatar_url in avatar_reuse_aliases:
            return avatar_reuse_aliases[avatar_url]

        ref = ReportGenerator._build_avatar_ref(avatar_key, avatar_url)
        avatar_reuse_registry.setdefault(ref, avatar_url)
        if avatar_reuse_aliases is not None:
            avatar_reuse_aliases[avatar_url] = ref
        return ref

    @staticmethod
    def _build_avatar_reuse_styles(avatar_reuse_registry: dict[str, str]) -> str:
        """Tạo style tái sử dụng một lần cho avatar."""
        if not avatar_reuse_registry:
            return ""

        rules = [
            '<style id="avatar-reuse-styles">',
            ".user-capsule-avatar,img[data-avatar-ref]{background-color:#ddd;background-size:cover;background-position:center;background-repeat:no-repeat;}",
        ]
        for ref, data_uri in avatar_reuse_registry.items():
            escaped_ref = html.escape(ref, quote=True)
            escaped_uri = data_uri.replace("\\", "\\\\").replace('"', '\\"')
            rules.append(
                f'[data-avatar-ref="{escaped_ref}"]'
                f'{{background-image:url("{escaped_uri}");}}'
            )
        rules.append("</style>")
        return "\n".join(rules)

    @staticmethod
    def _reuse_inline_avatar_img_sources(
        html_content: str,
        avatar_reuse_registry: dict[str, str],
        avatar_reuse_aliases: dict[str, str] | None = None,
    ) -> str:
        """Đổi avatar Data URI inline trong HTML cuối thành tham chiếu ngắn."""
        if not html_content:
            return html_content

        img_src_pattern = re.compile(
            r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(data:image/[^"\']+)(\2)([^>]*>)',
            re.IGNORECASE | re.DOTALL,
        )

        def replace(match: re.Match[str]) -> str:
            prefix, quote_char, data_uri, _, suffix = match.groups()
            if data_uri == TRANSPARENT_IMAGE_DATA_URI:
                return match.group(0)

            avatar_ref = (
                avatar_reuse_aliases.get(data_uri) if avatar_reuse_aliases else None
            )
            if not avatar_ref:
                return match.group(0)

            escaped_ref = html.escape(avatar_ref, quote=True)
            return (
                f"{prefix}{quote_char}{TRANSPARENT_IMAGE_DATA_URI}{quote_char}"
                f' data-avatar-ref="{escaped_ref}"{suffix}'
            )

        return img_src_pattern.sub(replace, html_content)

    @staticmethod
    def _reuse_avatars_in_final_html(
        html_content: str,
        avatar_reuse_registry: dict[str, str] | None,
        avatar_reuse_aliases: dict[str, str] | None = None,
    ) -> str:
        """Tái sử dụng avatar inline trong HTML cuối và inject style."""
        if not html_content:
            return html_content

        registry = avatar_reuse_registry if avatar_reuse_registry is not None else {}
        aliases = avatar_reuse_aliases if avatar_reuse_aliases is not None else {}
        html_content = ReportGenerator._reuse_inline_avatar_img_sources(
            html_content, registry, aliases
        )
        return ReportGenerator._inject_avatar_reuse_styles(
            html_content, ReportGenerator._build_avatar_reuse_styles(registry)
        )

    @staticmethod
    def _inject_avatar_reuse_styles(html_content: str, avatar_reuse_styles: str) -> str:
        """Inject style tái sử dụng avatar vào HTML cuối."""
        if not html_content or not avatar_reuse_styles:
            return html_content

        head_close = re.search(r"</head\s*>", html_content, re.IGNORECASE)
        if head_close:
            return (
                html_content[: head_close.start()]
                + avatar_reuse_styles
                + "\n"
                + html_content[head_close.start() :]
            )
        return avatar_reuse_styles + "\n" + html_content

    def _get_avatar_cache_key(
        self, avatar_id: str, avatar_cache_namespace: str | None = None
    ) -> str:
        """Tạo cache key avatar để tránh xung đột ID giữa các nền tảng."""
        namespace = str(avatar_cache_namespace or "legacy").strip() or "legacy"
        return f"{namespace}:{avatar_id}"

    async def _get_user_avatar(
        self,
        avatar_id: str,
        avatar_url_getter=None,
        avatar_cache_namespace: str | None = None,
    ) -> str:
        """
        Lấy Data URI Base64 của avatar người dùng.

        Dùng disk cache giữa các tác vụ; không cache thất bại để có thể retry.
        """
        cache_key = self._get_avatar_cache_key(avatar_id, avatar_cache_namespace)
        # 1. Kiểm tra cache, chỉ chứa avatar thành công.
        if cache_key in self._avatar_cache:
            data = self._avatar_cache[cache_key]
            if isinstance(data, str):
                return data
            return str(data)

        # 2. Thử lấy bytes avatar.
        avatar_bytes = await self._get_user_avatar_bytes(avatar_id, avatar_url_getter)

        if not avatar_bytes:
            # Trả avatar mặc định nhưng không cache để lần sau có thể retry.
            logger.warning(
                f"Lấy avatar người dùng thất bại {avatar_id}; dùng avatar fallback"
            )
            return self._get_default_avatar_base64()

        # 3. Chuyển đổi và cache khi thành công.
        avatar = self._b64_with_mime(avatar_bytes)
        if avatar:
            self._avatar_cache.set(cache_key, avatar, expire=AVATAR_CACHE_EXPIRE_TIME)
            return avatar

        # Fallback cuối.
        return self._get_default_avatar_base64()

    def _b64_with_mime(self, _bytes: bytes) -> str | None:
        """Chuyển bytes thành Data URI Base64 và tự nhận diện MIME type."""
        try:
            b64 = base64.b64encode(_bytes).decode("utf-8")
            # Nhận diện MIME type đơn giản.
            mime = "image/jpeg"
            if _bytes.startswith(b"\x89PNG"):
                mime = "image/png"
            elif _bytes.startswith(b"GIF8"):
                mime = "image/gif"
            elif _bytes.startswith(b"RIFF") and b"WEBP" in _bytes[8:16]:
                mime = "image/webp"
            elif _bytes.startswith(b"\xff\xd8"):
                mime = "image/jpeg"

            return f"data:{mime};base64,{b64}"
        except Exception as e:
            logger.error(f"Chuyển Base64 thất bại: {e}", exc_info=True)
        return None

    async def _get_user_avatar_bytes(
        self, user_id: str, avatar_url_getter=None
    ) -> bytes | None:
        """Logic lõi lấy avatar."""
        file_content = None
        if not self._avatar_session:
            self._avatar_session = aiohttp.ClientSession(
                trust_env=True, timeout=aiohttp.ClientTimeout(total=15)
            )
        async with self._avatar_session_concurrent_semaphore:
            avatar_url = None
            if avatar_url_getter:
                try:
                    # avatar_url_getter dự kiến trả URL.
                    result = await avatar_url_getter(user_id)
                    if result:
                        if result.startswith("http"):
                            avatar_url = result
                        elif result.startswith("base64://"):
                            return base64.b64decode(result[len("base64://") :])
                        elif result.startswith("data:"):
                            parts = result.split(",", 1)
                            if len(parts) == 2:
                                return base64.b64decode(parts[1])
                        else:
                            logger.warning(
                                f"avatar_url_getter tuỳ chỉnh trả URL không phải HTTP: {result[:50]}..."
                            )
                except Exception as e:
                    logger.warning(
                        f"Lấy avatar bằng avatar_url_getter tuỳ chỉnh thất bại: {e}"
                    )

            if not avatar_url:
                if (
                    avatar_url_getter is None
                    and user_id.isdigit()
                    and 5 <= len(user_id) <= 12
                ):
                    # Buộc dùng spec=40.
                    avatar_url = (
                        f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=40"
                    )
                else:
                    # Nền tảng khác không thể lấy avatar nếu thiếu URL.
                    return None

            # 5. Tải và lưu.
            safe_avatar_url = self._safe_url_for_log(avatar_url)
            try:
                async with self._avatar_session.get(avatar_url) as response:
                    if response.status == 200:
                        content = await response.read()
                        if content:
                            # Xác thực header tệp.
                            is_valid_image = False
                            if content.startswith(b"\xff\xd8"):  # JPEG
                                is_valid_image = True
                            elif content.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
                                is_valid_image = True
                            elif content.startswith(b"GIF8"):  # GIF
                                is_valid_image = True
                            elif (
                                content.startswith(b"RIFF") and b"WEBP" in content[:16]
                            ):  # WebP
                                is_valid_image = True

                            if is_valid_image:
                                file_content = content
                            else:
                                logger.warning(
                                    f"Dữ liệu avatar tải về không hợp lệ ({safe_avatar_url})"
                                )
                    else:
                        logger.warning(
                            f"Tải avatar thất bại {safe_avatar_url}: {response.status}"
                        )
            except Exception as e:
                logger.warning(f"Lỗi mạng khi tải avatar {safe_avatar_url}: {e}")

            return file_content

    def _get_default_avatar_base64(self) -> str:
        """Trả avatar mặc định là placeholder hình tròn màu xám."""
        # SVG hình tròn xám đơn giản dưới dạng Base64.
        svg = '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="50" fill="#ddd"/></svg>'
        b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
        return f"data:image/svg+xml;base64,{b64}"

    async def close(self):
        """Giải phóng tài nguyên, đóng cache và session."""
        if self._avatar_session:
            await self._avatar_session.close()
            self._avatar_session = None

        try:
            if self._avatar_cache:
                self._avatar_cache.close()
                logger.debug("Đã đóng cache avatar")
        except Exception as e:
            logger.warning(f"Đóng cache avatar thất bại: {e}")

    def _extract_html_error_summary(self, data: bytes) -> str | None:
        """Thử trích xuất lỗi HTML như title từ bytes phản hồi."""
        try:
            content = data.decode("utf-8", errors="ignore")
            content_lower = content.lower()
            if "<html" in content_lower or "<!doctype html" in content_lower:
                # Thử trích xuất title.
                title_match = re.search(
                    r"<title>(.*?)</title>", content, re.IGNORECASE | re.DOTALL
                )
                if title_match:
                    return f"Trang lỗi HTML: {title_match.group(1).strip()}"

                # Thử trích xuất h1.
                h1_match = re.search(
                    r"<h1>(.*?)</h1>", content, re.IGNORECASE | re.DOTALL
                )
                if h1_match:
                    return f"Trang lỗi HTML: {h1_match.group(1).strip()}"

                return f"Phản hồi HTML (100 ký tự đầu): {content[:100].strip()}..."
        except Exception:
            pass
        return None
