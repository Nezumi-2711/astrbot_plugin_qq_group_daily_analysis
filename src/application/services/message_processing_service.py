import re
from collections import Counter, OrderedDict

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ...infrastructure.persistence.platform_group_registry import PlatformGroupRegistry
from ...utils.logger import logger

_QQ_OFFICIAL_PLATFORM_NAMES = frozenset({"qq_official", "qq_official_webhook"})
_QQ_OFFICIAL_MENTION_PATTERN = re.compile(r"<@!?([A-Za-z0-9_-]+)>")


class MessageProcessingService:
    """
    Dịch vụ xử lý tin nhắn.

    Phân tích sự kiện tin nhắn nhóm, trích xuất nội dung và thông tin người gửi,
    lưu lịch sử và duy trì registry nhóm cho các nền tảng hướng sự kiện như
    Telegram và QQ Official. Dịch vụ cũng xử lý loại trùng riêng của QQ Official.

    Trách nhiệm:
    1. Phân tích nội dung tin nhắn như văn bản, hình ảnh và @mention.
    2. Xác định tên hiển thị của người gửi trên nhiều nền tảng.
    3. Lưu lịch sử tin nhắn.
    4. Duy trì registry để scheduler khám phá nhóm trên nền tảng hướng sự kiện.
    5. Loại trùng sự kiện QQ Official bằng cơ chế giữ chỗ và xác nhận message_id.
    """

    def __init__(self, context: Context, group_registry: PlatformGroupRegistry):
        self.context = context
        self.group_registry = group_registry
        self._seen_event_ids: OrderedDict[str, None] = OrderedDict()
        self._inflight_event_ids: set[str] = set()
        self._seen_event_ids_limit = 4096

    async def process_message(self, event: AstrMessageEvent) -> None:
        """
        Xử lý và lưu tin nhắn vào lịch sử.

        Được interceptor Telegram và QQ Official trong ``main.py`` cùng gọi.

        Args:
            event: Sự kiện tin nhắn AstrBot.

        Raises:
            ValueError: Khi không lấy được dữ liệu bắt buộc.
            RuntimeError: Khi nội dung tin nhắn rỗng.
        """
        # 1. Lấy ID nhóm (bắt buộc)
        group_id = self._get_group_id_from_event(event)
        if not group_id:
            raise ValueError("Không thể lấy ID nhóm; từ chối lưu tin nhắn")

        # 2. Lấy ID người gửi (bắt buộc)
        sender_id = event.get_sender_id()
        if not sender_id:
            raise ValueError(
                f"Nhóm {group_id}: không thể lấy ID người gửi; từ chối lưu tin nhắn"
            )
        sender_id = str(sender_id)

        # 3. Lấy tên người gửi, ưu tiên biệt danh và dùng fallback khi cần
        sender_name = self._resolve_sender_name(event, sender_id)

        # 4. Lấy ID nền tảng (bắt buộc)
        platform_id = event.get_platform_id()
        if not platform_id:
            raise ValueError(
                f"Nhóm {group_id}: không thể lấy ID nền tảng; từ chối lưu tin nhắn"
            )

        # 5. Trích xuất nội dung tin nhắn
        message_parts = self._extract_message_parts(event)
        if not message_parts:
            # Nội dung rỗng được coi là lỗi như hành vi ban đầu.
            raise RuntimeError(
                f"Nhóm {group_id}: nội dung tin nhắn rỗng (người gửi={sender_name}); từ chối lưu"
            )

        # 6. Trích xuất ID tin nhắn sự kiện và thời gian sự kiện
        msg_obj = getattr(event, "message_obj", None)
        event_message_id = str(getattr(msg_obj, "message_id", "") or "")

        platform_name = str(event.get_platform_name() or "").strip().lower()
        reserved_event_id = False
        if platform_name in {"qq_official", "qq_official_webhook"} and event_message_id:
            reserved_event_id = self._reserve_event_id(event_message_id)
            if not reserved_event_id:
                logger.debug(
                    "[QQOfficial] Bỏ qua sự kiện tin nhắn trùng: %s", event_message_id
                )
                return
        history_content = {
            "type": "user",
            "message": message_parts,
        }
        if platform_name in {"qq_official", "qq_official_webhook"}:
            event_timestamp = self._extract_event_timestamp(msg_obj)
            history_content["_qq_official"] = {
                "message_id": event_message_id,
                "timestamp": event_timestamp,
            }

        # 7. Lưu vào cơ sở dữ liệu
        try:
            await self.context.message_history_manager.insert(
                platform_id=platform_id,
                user_id=group_id,
                content=history_content,
                sender_id=sender_id,
                sender_name=sender_name,
            )
        except BaseException:
            if reserved_event_id:
                self._release_event_id(event_message_id)
            raise
        else:
            if reserved_event_id:
                self._commit_event_id(event_message_id)

        # Register the group so the scheduler can discover platforms that
        # do not provide a group-list API (Telegram, QQ Official, etc.).
        try:
            await self.group_registry.upsert(
                platform_id=platform_id,
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                event_message_id=event_message_id,
            )
        except Exception as e:
            logger.warning(
                "[GroupRegistry] Upsert failed: "
                f"platform_id={platform_id} group_id={group_id} error={e}"
            )

        logger.debug(
            f"[{platform_id}] Đã lưu tin nhắn của nhóm {group_id} (người gửi: {sender_name})"
        )

    def _get_group_id_from_event(self, event: AstrMessageEvent) -> str | None:
        """Lấy ID nhóm an toàn từ sự kiện tin nhắn."""
        try:
            group_id = event.get_group_id()
            return group_id if group_id else None
        except Exception:
            return None

    def _resolve_sender_name(self, event: AstrMessageEvent, sender_id: str) -> str:
        """Xác định tên hiển thị của người gửi."""
        platform_name = str(event.get_platform_name() or "").lower()
        candidates: list[str | None] = []

        msg_obj = getattr(event, "message_obj", None)
        sender_obj = getattr(msg_obj, "sender", None)
        raw_message = getattr(msg_obj, "raw_message", None)
        raw_msg_obj = getattr(raw_message, "message", raw_message)
        from_user = getattr(raw_msg_obj, "from_user", None)

        if platform_name == "telegram":
            if from_user is not None:
                candidates.extend(
                    [
                        getattr(from_user, "full_name", None),
                        getattr(from_user, "first_name", None),
                    ]
                )
            candidates.append(event.get_sender_name())
            if sender_obj is not None:
                candidates.append(getattr(sender_obj, "nickname", None))
            if from_user is not None:
                candidates.append(getattr(from_user, "username", None))
        else:
            candidates.append(event.get_sender_name())
            if sender_obj is not None:
                candidates.append(getattr(sender_obj, "nickname", None))

        if from_user is not None:
            candidates.extend(
                [
                    getattr(from_user, "full_name", None),
                    getattr(from_user, "first_name", None),
                    getattr(from_user, "username", None),
                ]
            )

        for candidate in candidates:
            name = str(candidate or "").strip()
            if not self._is_placeholder_sender_name(name, sender_id):
                return name

        return sender_id

    def _extract_message_parts(self, event: AstrMessageEvent) -> list[dict]:
        """Trích xuất nội dung tin nhắn từ sự kiện."""
        message_parts = []
        message = event.message_obj
        platform_name = str(event.get_platform_name() or "").strip().lower()
        qq_mention_replacements = (
            self._extract_qq_official_mention_replacements(event)
            if platform_name in _QQ_OFFICIAL_PLATFORM_NAMES
            else None
        )

        # Thu thập các @mention
        pending_mentions: Counter[str] = Counter()
        if message and hasattr(message, "message"):
            for seg in message.message:
                if not hasattr(seg, "type"):
                    continue
                if seg.type not in ("At", "at"):
                    continue

                target = getattr(seg, "target", None) or getattr(seg, "qq", None)
                if target is None and hasattr(seg, "data"):
                    target = seg.data.get("qq") or seg.data.get("target")

                target_str = str(target or "").strip()
                if target_str:
                    pending_mentions[target_str] += 1

                display_name = str(getattr(seg, "name", "") or "").strip()
                if display_name and display_name != target_str:
                    pending_mentions[display_name] += 1

        if message and hasattr(message, "message"):
            for seg in message.message:
                if not hasattr(seg, "type"):
                    continue

                seg_type = seg.type
                if seg_type in ("Plain", "text"):
                    text = getattr(seg, "text", None)
                    if text is None and hasattr(seg, "data"):
                        text = seg.data.get("text")
                    if text:
                        text = self._strip_known_mentions(text, pending_mentions)
                        if qq_mention_replacements is not None:
                            text = self._sanitize_qq_official_mentions(
                                text, qq_mention_replacements
                            )
                        message_parts.append({"type": "plain", "text": text})

                elif seg_type in ("Image", "image"):
                    url = getattr(seg, "url", None) or (
                        seg.data.get("url") if hasattr(seg, "data") else None
                    )
                    if url:
                        message_parts.append({"type": "image", "url": url})

                elif seg_type in ("At", "at"):
                    target = getattr(seg, "target", None) or getattr(seg, "qq", None)
                    if target is None and hasattr(seg, "data"):
                        target = seg.data.get("qq") or seg.data.get("target")
                    if target:
                        message_parts.append(
                            {
                                "type": "at",
                                "target_id": str(target),
                                "name": str(getattr(seg, "name", "") or ""),
                            }
                        )

                elif seg_type in ("File", "file"):
                    url = getattr(seg, "url", None) or getattr(seg, "file_", None)
                    message_parts.append(
                        {
                            "type": "file",
                            "url": str(url or ""),
                            "name": str(getattr(seg, "name", "") or ""),
                        }
                    )

                elif seg_type in ("Record", "record", "voice"):
                    url = getattr(seg, "url", None) or getattr(seg, "file", None)
                    message_parts.append({"type": "voice", "url": str(url or "")})

                elif seg_type in ("Video", "video"):
                    url = getattr(seg, "url", None) or getattr(seg, "file", None)
                    message_parts.append({"type": "video", "url": str(url or "")})

        if not message_parts and event.message_str:
            fallback_text = str(event.message_str)
            if qq_mention_replacements is not None:
                fallback_text = self._sanitize_qq_official_mentions(
                    fallback_text, qq_mention_replacements
                )
            message_parts.append({"type": "plain", "text": fallback_text})

        # Loại bỏ phân đoạn văn bản rỗng
        message_parts = [
            part
            for part in message_parts
            if not (
                part.get("type") == "plain" and not str(part.get("text", "")).strip()
            )
        ]

        return message_parts

    @classmethod
    def _extract_qq_official_mention_replacements(
        cls, event: AstrMessageEvent
    ) -> dict[str, str]:
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        raw_candidates = [raw_message]
        nested_message = cls._read_field(raw_message, "message")
        if nested_message is not None and nested_message is not raw_message:
            raw_candidates.insert(0, nested_message)

        mentions = None
        for candidate in raw_candidates:
            mentions = cls._read_field(candidate, "mentions")
            if mentions is not None:
                break

        replacements: dict[str, str] = {}
        if not isinstance(mentions, (list, tuple)):
            return replacements

        for mention in mentions:
            mention_id = str(
                cls._read_field(
                    mention,
                    "id",
                    "member_openid",
                    "memberopenid",
                    "user_openid",
                    "useropenid",
                )
                or ""
            ).strip()
            if not mention_id:
                continue

            if cls._read_field(mention, "is_you") is True:
                replacements[mention_id] = ""
                continue

            display_name = str(
                cls._read_field(mention, "username", "name", "nickname") or ""
            ).strip()
            display_name = display_name.lstrip("@").strip()
            if cls._is_placeholder_sender_name(display_name, mention_id):
                display_name = "Thành viên"
            replacements[mention_id] = f"@{display_name}"

        return replacements

    @staticmethod
    def _sanitize_qq_official_mentions(text: str, replacements: dict[str, str]) -> str:
        def replace_mention(match: re.Match[str]) -> str:
            mention_id = match.group(1)
            if mention_id.lower() in {"all", "everyone"}:
                return "@Tất cả thành viên"
            return replacements.get(mention_id, "@Thành viên")

        cleaned = _QQ_OFFICIAL_MENTION_PATTERN.sub(replace_mention, str(text))
        return re.sub(r"[^\S\r\n]{2,}", " ", cleaned).strip(" \t")

    @staticmethod
    def _read_field(source: object, *names: str) -> object | None:
        if isinstance(source, dict):
            for name in names:
                if name in source:
                    return source[name]
            return None

        for name in names:
            value = getattr(source, name, None)
            if value is not None:
                return value
        return None

    @staticmethod
    def _strip_known_mentions(text: str, pending_mentions: Counter[str]) -> str:
        """Xoá các @mention đã nhận diện khỏi văn bản."""
        cleaned = str(text)
        if not cleaned or not pending_mentions:
            return cleaned.strip()

        for mention, remaining in list(pending_mentions.items()):
            if not mention or remaining <= 0:
                continue

            pattern = re.compile(rf"(?<!\w)@{re.escape(mention)}(?!\w)")
            removed = 0
            while removed < remaining:
                cleaned, subn = pattern.subn("", cleaned, count=1)
                if subn == 0:
                    break
                removed += 1

            if removed > 0:
                pending_mentions[mention] -= removed
                if pending_mentions[mention] <= 0:
                    pending_mentions.pop(mention, None)

        return re.sub(r"[^\S\r\n]{2,}", " ", cleaned).strip()

    @staticmethod
    def _is_placeholder_sender_name(name: str | None, sender_id: str) -> bool:
        """Kiểm tra sender_name có phải giá trị placeholder hay không."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized.lower() in {"unknown", "none", "null", "nil", "undefined"}:
            return True
        return normalized == str(sender_id).strip()

    @staticmethod
    def _extract_event_timestamp(message_obj: object) -> int:
        """Trích xuất timestamp sự kiện nền tảng từ đối tượng tin nhắn."""
        raw_message = getattr(message_obj, "raw_message", None)
        if isinstance(raw_message, dict):
            candidate = raw_message.get("timestamp")
            if not candidate:
                raw_data = raw_message.get("raw_data")
                if isinstance(raw_data, dict):
                    candidate = raw_data.get("timestamp")
        else:
            raw_data = getattr(raw_message, "raw_data", None)
            candidate = getattr(raw_message, "timestamp", None)
            if not candidate and isinstance(raw_data, dict):
                candidate = raw_data.get("timestamp")
        if isinstance(candidate, (int, float)):
            return int(candidate)
        if candidate:
            try:
                from datetime import datetime

                return int(
                    datetime.fromisoformat(
                        str(candidate).replace("Z", "+00:00")
                    ).timestamp()
                )
            except (TypeError, ValueError, OverflowError):
                pass
        return 0

    def _reserve_event_id(self, event_message_id: str) -> bool:
        """Giữ chỗ ID sự kiện để tránh lưu trùng trong lúc ghi lịch sử."""
        if (
            event_message_id in self._inflight_event_ids
            or event_message_id in self._seen_event_ids
        ):
            if event_message_id in self._seen_event_ids:
                self._seen_event_ids.move_to_end(event_message_id)
            return False
        self._inflight_event_ids.add(event_message_id)
        return True

    def _commit_event_id(self, event_message_id: str) -> None:
        """Xác nhận ID sự kiện đã lưu để dùng cho các lần loại trùng sau."""
        self._inflight_event_ids.discard(event_message_id)
        if event_message_id in self._seen_event_ids:
            self._seen_event_ids.move_to_end(event_message_id)
        else:
            self._seen_event_ids[event_message_id] = None
        if len(self._seen_event_ids) > self._seen_event_ids_limit:
            self._seen_event_ids.popitem(last=False)

    def _release_event_id(self, event_message_id: str) -> None:
        """Giải phóng ID sự kiện khi việc lưu thất bại hoặc bị huỷ."""
        self._inflight_event_ids.discard(event_message_id)
