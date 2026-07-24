"""Adapter OneBot v11 cho NapCat, go-cqhttp, Lagrange và implementation khác."""

import asyncio
import base64
import os
import time
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from ....domain.value_objects.platform_capabilities import (
    ONEBOT_V11_CAPABILITIES,
    PlatformCapabilities,
)
from ....domain.value_objects.unified_group import UnifiedGroup, UnifiedMember
from ....domain.value_objects.unified_message import (
    MessageContent,
    MessageContentType,
    UnifiedMessage,
)
from ....utils.logger import logger
from ..base import PlatformAdapter


class OneBotAdapter(PlatformAdapter):
    """Adapter OneBot v11 hỗ trợ tin nhắn, nhóm và avatar cho bot QQ."""

    platform_name = "onebot"

    # Template URL dịch vụ avatar QQ.
    USER_AVATAR_TEMPLATE = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s={size}"
    USER_AVATAR_HD_TEMPLATE = (
        "https://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec={size}&img_type=jpg"
    )
    GROUP_AVATAR_TEMPLATE = "https://p.qlogo.cn/gh/{group_id}/{group_id}/{size}/"

    # Kích thước avatar được dịch vụ OneBot hỗ trợ.
    AVAILABLE_SIZES = (40, 100, 140, 160, 640)

    def __init__(self, bot_instance: Any, config: dict | None = None):
        """
        Khởi tạo adapter OneBot.
        """
        super().__init__(bot_instance, config)
        # Hỗ trợ lấy ID bot từ nhiều key cấu hình tiềm năng.
        self.bot_self_ids = (
            [str(id) for id in config.get("bot_self_ids", [])] if config else []
        )
        if not self.bot_self_ids and config:
            self.bot_self_ids = [str(id) for id in config.get("bot_qq_ids", [])]
        self.filter_bot_messages = (
            config.get("filter_bot_messages", True) if config else True
        )

        # Cờ phát hiện LLBot.
        self._is_llbot = False
        self._llbot_checked = False

        # Cờ phát hiện SnowLuma.
        self._is_snowluma = False
        self._snowluma_checked = False

        # Cache trạng thái mute: group_id -> timestamp.
        self._muted_groups_cache = {}
        # Cache role nhóm để fallback khi get_group_member_info timeout.
        self._group_role_cache: dict[str, tuple[str, float]] = {}

    def _init_capabilities(self) -> PlatformCapabilities:
        """Trả về capability OneBot v11 định sẵn."""
        return ONEBOT_V11_CAPABILITIES

    async def _detect_llbot(self):
        """Phát hiện LLBot."""
        if self._llbot_checked:
            return
        try:
            # Tránh treo ở bản cũ không hỗ trợ get_version_info.
            result = await self.bot.call_action("get_version_info")
            if isinstance(result, dict):
                app_name = result.get("app_name", "")
                self._is_llbot = app_name == "LLOneBot"
                if self._is_llbot:
                    logger.info("[OneBot] Phát hiện endpoint giao thức LLBot")
        except Exception:
            self._is_llbot = False
        self._llbot_checked = True

    async def _detect_snowluma(self):
        """Phát hiện SnowLuma."""
        if self._snowluma_checked:
            return
        try:
            result = await self.bot.call_action("get_version_info")
            if isinstance(result, dict):
                app_name = result.get("app_name", "")
                self._is_snowluma = app_name.lower() == "snowluma"
                if self._is_snowluma:
                    logger.info("[OneBot] Phát hiện endpoint giao thức SnowLuma")
        except Exception as exc:
            logger.debug(
                "[OneBot] Phát hiện SnowLuma thất bại, xử lý như endpoint khác: %s",
                exc,
                exc_info=True,
            )
            self._is_snowluma = False
        self._snowluma_checked = True

    def _get_nearest_size(self, requested_size: int) -> int:
        """Tìm kích thước hỗ trợ gần nhất với yêu cầu."""
        return min(self.AVAILABLE_SIZES, key=lambda x: abs(x - requested_size))

    # ==================== Triển khai IMessageRepository ====================

    async def fetch_messages(
        self,
        group_id: str,
        days: int = 1,
        max_count: int = 1000,
        before_id: str | None = None,
        since_ts: int | None = None,
    ) -> list[UnifiedMessage]:
        """
        Lấy lịch sử nhóm từ backend OneBot theo trang để giảm tải CPU và bộ nhớ.

        Args:
            group_id: ID nhóm.
            days: Số ngày lịch sử.
            max_count: Số tin nhắn tối đa.
            before_id: ID neo để phân trang ngược.
            since_ts: Unix timestamp bắt đầu, ưu tiên hơn days.

        Returns:
            Danh sách UnifiedMessage.
        """
        if not hasattr(self.bot, "call_action"):
            return []

        await self._detect_snowluma()

        try:
            chunk_size = 100  # Mỗi lần lấy 100 mục để ổn định.
            all_raw_messages = []

            # Xác định thời điểm bắt đầu truy ngược.
            if since_ts and since_ts > 0:
                start_timestamp = since_ts
            else:
                end_time_dt = datetime.now()
                start_time_dt = end_time_dt - timedelta(days=days)
                start_timestamp = int(start_time_dt.timestamp())

            # Phân trang ngược bằng message_seq hoặc message_id.
            current_anchor_id = before_id

            logger.info(
                f"OneBot bắt đầu lấy lịch sử phân trang: nhóm {group_id}, "
                f"từ {datetime.fromtimestamp(start_timestamp).strftime('%Y-%m-%d %H:%M:%S')}, "
                f"tối đa {max_count} mục"
            )

            while len(all_raw_messages) < max_count:
                fetch_count = min(chunk_size, max_count - len(all_raw_messages))

                params: dict[str, int | str | bool | None] = {
                    "group_id": int(group_id),
                    "count": fetch_count,
                }
                if self._is_snowluma:
                    if current_anchor_id:
                        params["message_id"] = current_anchor_id
                else:
                    params["reverseOrder"] = True
                    if current_anchor_id:
                        params["message_seq"] = current_anchor_id

                result = await self.bot.call_action("get_group_msg_history", **params)

                if not result or "messages" not in result:
                    logger.debug(
                        f"OneBot phân trang: API trả dữ liệu rỗng hoặc lỗi, dừng nhóm {group_id}"
                    )
                    break

                messages = result.get("messages", [])
                if not messages:
                    logger.debug(
                        f"OneBot phân trang: lấy được 0 tin nhắn, dừng nhóm {group_id}"
                    )
                    break

                # Chọn tin cũ nhất trong batch làm điểm bắt đầu tiếp theo;
                # implementation OneBot có thể xử lý reverseOrder khác nhau.
                first_msg = messages[0]
                last_msg = messages[-1]
                if first_msg.get("time", 0) <= last_msg.get("time", 0):
                    # Thứ tự xuôi: tin đầu cũ nhất.
                    chunk_earliest_msg = first_msg
                else:
                    # Thứ tự ngược: tin cuối cũ nhất.
                    chunk_earliest_msg = last_msg

                chunk_earliest_time = chunk_earliest_msg.get("time", 0)

                for raw_msg in messages:
                    msg_time = raw_msg.get("time", 0)
                    msg_id = str(raw_msg.get("message_id", ""))

                    # Lọc cơ bản: loại trùng.
                    if any(
                        str(m.get("message_id", "")) == msg_id for m in all_raw_messages
                    ):
                        continue

                    # Lọc danh tính: bỏ bot.
                    sender_id = str(raw_msg.get("sender", {}).get("user_id", ""))
                    if self.filter_bot_messages and sender_id in self.bot_self_ids:
                        continue

                    # Kiểm tra khoảng thời gian.
                    if start_timestamp <= msg_time <= int(datetime.now().timestamp()):
                        all_raw_messages.append(raw_msg)

                # SnowLuma chỉ hỗ trợ message_id làm neo; implementation khác ưu tiên
                # message_seq > real_id > seq > message_id. Không trừ 1 để tương thích
                # ID không liên tục của NapCat và sequence mode của LLBot.
                if self._is_snowluma:
                    new_anchor_id = chunk_earliest_msg.get("message_id")
                else:
                    seq_val = (
                        chunk_earliest_msg.get("message_seq")
                        or chunk_earliest_msg.get("real_id")
                        or chunk_earliest_msg.get("seq")
                    )
                    mid_val = chunk_earliest_msg.get("message_id")
                    new_anchor_id = seq_val if seq_val is not None else mid_val

                # Dừng khi tới thời điểm bắt đầu hoặc neo không thể lùi thêm.
                if chunk_earliest_time <= start_timestamp:
                    logger.debug(
                        f"OneBot phân trang: đã tới thời điểm bắt đầu ({start_timestamp}), hoàn tất"
                    )
                    break

                if current_anchor_id and str(new_anchor_id) == str(current_anchor_id):
                    logger.debug(
                        "OneBot phân trang: neo không dịch chuyển, có thể đã hết lịch sử"
                    )
                    break

                current_anchor_id = new_anchor_id
                logger.debug(
                    f"Tiến độ OneBot: đã lấy {len(all_raw_messages)} tin hợp lệ, neo tiếp: {current_anchor_id}"
                )

                # Giãn nhẹ để giảm tải server.
                await asyncio.sleep(0.05)

            # Chuyển thành UnifiedMessage, loại trùng và sắp xếp trước khi trả.
            unified_messages = []
            seen_ids = set()
            for raw_msg in all_raw_messages:
                mid = str(raw_msg.get("message_id", ""))
                if not mid or mid in seen_ids:
                    continue

                unified = self._convert_message(raw_msg, group_id)
                if unified:
                    unified_messages.append(unified)
                    seen_ids.add(mid)

            # Đảm bảo kết quả theo thứ tự thời gian.
            unified_messages.sort(key=lambda m: m.timestamp)

            logger.info(
                f"OneBot phân trang hoàn tất: xử lý {len(all_raw_messages)} tin thô, hợp lệ {len(unified_messages)}"
            )
            return unified_messages

        except Exception as e:
            logger.warning(f"OneBot lấy tin nhắn phân trang thất bại: {e}")
            return []

    def _convert_message(self, raw_msg: dict, group_id: str) -> UnifiedMessage | None:
        """Chuyển dict tin nhắn native OneBot thành UnifiedMessage."""
        try:
            sender = raw_msg.get("sender", {})
            message_chain = raw_msg.get("message", [])

            # Chuyển message dạng chuỗi thành list để tương thích.
            if isinstance(message_chain, str):
                message_chain = [{"type": "text", "data": {"text": message_chain}}]

            contents = []
            text_parts = []

            for seg in message_chain:
                seg_type = seg.get("type", "")
                seg_data = seg.get("data", {})

                if seg_type == "text":
                    text = seg_data.get("text", "")
                    text_parts.append(text)
                    contents.append(
                        MessageContent(type=MessageContentType.TEXT, text=text)
                    )

                elif seg_type == "image":
                    # QQ: subType=1 là sticker, truyền qua raw_data cho thống kê.
                    sub_type = seg_data.get("subType", seg_data.get("sub_type"))
                    # Chuyển an toàn sang int.
                    try:
                        is_sticker = int(sub_type) == 1
                    except (TypeError, ValueError):
                        is_sticker = False
                    # Chỉ thêm sub_type vào raw_data khi hợp lệ.
                    raw_data: dict[str, Any] = {"summary": seg_data.get("summary", "")}
                    if sub_type is not None:
                        raw_data["sub_type"] = int(sub_type)
                    contents.append(
                        MessageContent(
                            type=MessageContentType.EMOJI
                            if is_sticker
                            else MessageContentType.IMAGE,
                            url=seg_data.get("url", seg_data.get("file", "")),
                            raw_data=raw_data,
                        )
                    )

                elif seg_type == "at":
                    contents.append(
                        MessageContent(
                            type=MessageContentType.AT,
                            at_user_id=str(seg_data.get("qq", "")),
                        )
                    )

                elif seg_type in ("face", "mface", "bface", "sface"):
                    contents.append(
                        MessageContent(
                            type=MessageContentType.EMOJI,
                            emoji_id=str(seg_data.get("id", "")),
                            raw_data={"face_type": seg_type},
                        )
                    )

                elif seg_type == "reply":
                    contents.append(
                        MessageContent(
                            type=MessageContentType.REPLY,
                            raw_data={"reply_id": seg_data.get("id", "")},
                        )
                    )

                elif seg_type == "forward":
                    contents.append(
                        MessageContent(
                            type=MessageContentType.FORWARD, raw_data=seg_data
                        )
                    )

                elif seg_type == "record":
                    contents.append(
                        MessageContent(
                            type=MessageContentType.VOICE,
                            url=seg_data.get("url", seg_data.get("file", "")),
                        )
                    )

                elif seg_type == "video":
                    contents.append(
                        MessageContent(
                            type=MessageContentType.VIDEO,
                            url=seg_data.get("url", seg_data.get("file", "")),
                        )
                    )

                else:
                    contents.append(
                        MessageContent(type=MessageContentType.UNKNOWN, raw_data=seg)
                    )

            # Trích xuất ID trả lời.
            reply_to = None
            for c in contents:
                if c.type == MessageContentType.REPLY and c.raw_data:
                    reply_to = str(c.raw_data.get("reply_id", ""))
                    break

            return UnifiedMessage(
                message_id=str(raw_msg.get("message_id", "")),
                sender_id=str(sender.get("user_id", "")),
                sender_name=sender.get("nickname", ""),
                sender_card=sender.get("card", "") or None,
                group_id=group_id,
                text_content="".join(text_parts),
                contents=tuple(contents),
                timestamp=raw_msg.get("time", 0),
                platform="onebot",
                reply_to_id=reply_to,
            )

        except Exception as e:
            logger.debug(f"Lỗi OneBot _convert_message: {e}")
            return None

    def convert_to_raw_format(self, messages: list[UnifiedMessage]) -> list[dict]:
        """
        Chuyển định dạng thống nhất về dict native OneBot v11.

        Cho phép logic hiện tại dùng pipeline mới mà không cần refactor.

        Args:
            messages: Danh sách UnifiedMessage.

        Returns:
            Danh sách dict tin nhắn OneBot.
        """
        raw_messages = []
        for msg in messages:
            message_chain = []
            for content in msg.contents:
                if content.type == MessageContentType.TEXT:
                    message_chain.append(
                        {"type": "text", "data": {"text": content.text or ""}}
                    )
                elif content.type == MessageContentType.IMAGE:
                    message_chain.append(
                        {"type": "image", "data": {"url": content.url or ""}}
                    )
                elif content.type == MessageContentType.AT:
                    message_chain.append(
                        {"type": "at", "data": {"qq": content.at_user_id or ""}}
                    )
                elif content.type == MessageContentType.EMOJI:
                    face_type = (
                        content.raw_data.get("face_type", "face")
                        if content.raw_data
                        else "face"
                    )
                    message_chain.append(
                        {"type": face_type, "data": {"id": content.emoji_id or ""}}
                    )
                elif content.type == MessageContentType.REPLY:
                    reply_id = (
                        content.raw_data.get("reply_id", "") if content.raw_data else ""
                    )
                    message_chain.append({"type": "reply", "data": {"id": reply_id}})
                elif content.type == MessageContentType.FORWARD:
                    message_chain.append(
                        {"type": "forward", "data": content.raw_data or {}}
                    )
                elif content.type == MessageContentType.VOICE:
                    message_chain.append(
                        {"type": "record", "data": {"url": content.url or ""}}
                    )
                elif content.type == MessageContentType.VIDEO:
                    message_chain.append(
                        {"type": "video", "data": {"url": content.url or ""}}
                    )
                elif content.type == MessageContentType.UNKNOWN and content.raw_data:
                    message_chain.append(content.raw_data)

            raw_msg = {
                "message_id": msg.message_id,
                "time": msg.timestamp,
                "sender": {
                    "user_id": msg.sender_id,
                    "nickname": msg.sender_name,
                    "card": msg.sender_card or "",
                },
                "message": message_chain,
                "group_id": msg.group_id,
                "raw_message": msg.text_content,
                "user_id": msg.sender_id,
            }
            raw_messages.append(raw_msg)

        return raw_messages

    # ==================== Triển khai IMessageSender ====================

    async def send_text(
        self,
        group_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> bool:
        """
        Gửi tin nhắn văn bản tới nhóm.

        Args:
            group_id: ID nhóm đích.
            text: Nội dung tin nhắn.
            reply_to: ID tin nhắn được trả lời.

        Returns:
            Có gửi thành công hay không.
        """
        try:
            message = [{"type": "text", "data": {"text": text}}]

            if reply_to:
                message.insert(0, {"type": "reply", "data": {"id": reply_to}})

            await self.bot.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=message,
            )
            self._record_mute_status(group_id, False)  # Gửi thành công, xoá cache mute.
            return True
        except Exception as e:
            if self._is_mute_exception(e):
                self._record_mute_status(group_id, True)
            logger.error(f"OneBot gửi văn bản thất bại: {e}")
            return False

    async def _execute_transmission_strategy(
        self,
        path: str,
        worker: Any,
        label: str,
        format_path_as_url: bool = False,
    ) -> bool:
        """
        Thực thi chiến lược truyền chung: ưu tiên Base64 nếu bật, thử path và fallback Base64.

        Args:
            path: Path tệp hoặc URL.
            worker: Hàm async gọi API nhận file_val và mode_label.
            label: Nhãn nghiệp vụ dùng cho log.
            format_path_as_url: Có đổi path local thành file URL hay không.
        """
        try:
            use_base64 = self._get_use_base64()
            abs_path, is_remote, exists = self._prepare_path(path)

            # 1. Ưu tiên Base64 nếu bật.
            if use_base64 and not is_remote:
                b64 = await self._get_base64_from_file(abs_path)
                if b64:
                    try:
                        await worker(b64, "Ưu tiên Base64")
                        return True
                    except Exception:
                        pass

            # 2. Thử path vật lý hoặc URL từ xa.
            if exists:
                try:
                    file_val = abs_path
                    if not is_remote and format_path_as_url:
                        file_val = (
                            f"file://{abs_path}"
                            if abs_path.startswith("/")
                            else f"file:///{abs_path}"
                        )
                    await worker(file_val, "Chế độ path")
                    return True
                except Exception as e:
                    if not use_base64:
                        logger.error(f"[{label}] Gửi thất bại: {e}")
                        return False
                    logger.warning(
                        f"[{label}] Gửi bằng path thất bại ({e}), chuẩn bị fallback Base64..."
                    )
            else:
                if not use_base64:
                    logger.error(
                        f"[{label}] Tệp không tồn tại và Base64 chưa bật: {abs_path}"
                    )
                    return False

            # 3. Fallback cuối.
            if not is_remote:
                b64 = await self._get_base64_from_file(abs_path)
                if b64:
                    await worker(b64, "Gửi bù Base64")
                    return True

            return False
        except Exception as e:
            logger.error(f"[{label}] Lỗi gửi: {e}")
            return False

    async def send_image(
        self,
        group_id: str,
        image_path: str,
        caption: str = "",
    ) -> bool:
        """Gửi ảnh tới nhóm."""

        async def do_send(file_val: str, label: str):
            msg = []
            if caption:
                msg.append({"type": "text", "data": {"text": caption}})
            msg.append({"type": "image", "data": {"file": file_val}})
            try:
                await self.bot.call_action(
                    "send_group_msg", group_id=int(group_id), message=msg
                )
                self._record_mute_status(group_id, False)
            except Exception as e:
                if self._is_mute_exception(e):
                    self._record_mute_status(group_id, True)
                raise
            logger.debug(f"[OneBot] Gửi ảnh thành công ({label}): nhóm {group_id}")

        return await self._execute_transmission_strategy(
            image_path, do_send, "Ảnh OneBot", format_path_as_url=True
        )

    async def send_file(
        self,
        group_id: str,
        file_path: str,
        filename: str | None = None,
    ) -> bool:
        """Upload và gửi tệp qua tính năng tệp nhóm."""

        async def do_upload(content: str, label: str):
            try:
                await self.bot.call_action(
                    "upload_group_file",
                    group_id=int(group_id),
                    file=content,
                    name=filename or os.path.basename(file_path),
                )
                self._record_mute_status(group_id, False)
            except Exception as e:
                if self._is_mute_exception(e):
                    self._record_mute_status(group_id, True)
                raise
            logger.debug(
                f"[OneBot] Gửi tệp thành công ({label}): {filename or file_path}"
            )

        return await self._execute_transmission_strategy(
            file_path, do_upload, "Tệp OneBot"
        )

    async def send_forward_msg(
        self,
        group_id: str,
        nodes: list[dict],
    ) -> bool:
        """
        Gửi tin nhắn chuyển tiếp nhóm đã gộp.
        """
        if not hasattr(self.bot, "call_action"):
            return False

        try:
            # Tương thích uin và user_id trong node.
            for node in nodes:
                if "data" in node:
                    if "user_id" in node["data"] and "uin" not in node["data"]:
                        node["data"]["uin"] = node["data"]["user_id"]

            await self.bot.call_action(
                "send_group_forward_msg",
                group_id=int(group_id),
                messages=nodes,
            )
            self._record_mute_status(group_id, False)
            return True
        except Exception as e:
            if self._is_mute_exception(e):
                self._record_mute_status(group_id, True)
            logger.warning(f"[OneBot] Gửi tin chuyển tiếp đã gộp thất bại: {e}")
            return False

    # ==================== Triển khai IGroupInfoRepository ====================

    async def get_group_info(self, group_id: str) -> UnifiedGroup | None:
        """Lấy metadata cơ bản của nhóm."""
        try:
            result = await self.bot.call_action(
                "get_group_info",
                group_id=int(group_id),
            )

            if not result:
                return None

            return UnifiedGroup(
                group_id=str(result.get("group_id", group_id)),
                group_name=result.get("group_name", ""),
                member_count=result.get("member_count", 0),
                owner_id=str(result.get("owner_id", "")) or None,
                create_time=result.get("group_create_time"),
                platform="onebot",
            )
        except Exception:
            return None

    async def get_group_list(self) -> list[str]:
        """Lấy ID mọi nhóm bot hiện đã tham gia."""
        try:
            result = await self.bot.call_action("get_group_list")
            return [str(g.get("group_id", "")) for g in result or []]
        except Exception:
            return []

    async def get_member_list(self, group_id: str) -> list[UnifiedMember]:
        """Lấy toàn bộ danh sách thành viên nhóm."""
        try:
            result = await self.bot.call_action(
                "get_group_member_list",
                group_id=int(group_id),
            )

            members = []
            for m in result or []:
                members.append(
                    UnifiedMember(
                        user_id=str(m.get("user_id", "")),
                        nickname=m.get("nickname", ""),
                        card=m.get("card", "") or None,
                        role=m.get("role", "member"),
                        join_time=m.get("join_time"),
                    )
                )
            return members
        except Exception:
            return []

    async def get_member_info(
        self,
        group_id: str,
        user_id: str,
    ) -> UnifiedMember | None:
        """Lấy thông tin chi tiết và role của thành viên nhóm."""
        try:
            result = await self.bot.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
            )

            if not result:
                return None

            return UnifiedMember(
                user_id=str(result.get("user_id", user_id)),
                nickname=result.get("nickname", ""),
                card=result.get("card", "") or None,
                role=result.get("role", "member"),
                join_time=result.get("join_time"),
            )
        except Exception:
            return None

    async def _get_base64_from_file(self, file_path: str) -> str | None:
        """
        Đọc tệp local và trả chuỗi mã hoá Base64.

        Args:
            file_path: Path tuyệt đối của tệp local.

        Returns:
            Chuỗi dạng base64:// hoặc None nếu đọc thất bại.
        """
        try:
            import os

            if not os.path.exists(file_path):
                logger.error(f"Tệp không tồn tại, không thể đọc Base64: {file_path}")
                return None

            with open(file_path, "rb") as f:
                data = f.read()
                b64 = base64.b64encode(data).decode("utf-8")
                return f"base64://{b64}"
        except Exception as e:
            logger.error(f"Đọc tệp và chuyển Base64 thất bại: {e}")
            return None

    # ==================== Triển khai IAvatarRepository ====================

    async def get_user_avatar_url(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """
        Dựng URL dịch vụ QQ Official để lấy avatar người dùng.

        Args:
            user_id: ID QQ.
            size: Kích thước pixel mong muốn.

        Returns:
            URL đã định dạng.
        """
        actual_size = self._get_nearest_size(size)
        # Dùng endpoint HD cho kích thước 640.
        if actual_size >= 640:
            return self.USER_AVATAR_HD_TEMPLATE.format(user_id=user_id, size=640)
        return self.USER_AVATAR_TEMPLATE.format(user_id=user_id, size=actual_size)

    async def get_user_avatar_data(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """
        Tải avatar qua mạng và chuyển sang Base64 để render trực tiếp.
        """
        url = await self.get_user_avatar_url(user_id, size)
        if not url:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        b64 = base64.b64encode(data).decode("utf-8")
                        content_type = resp.headers.get("Content-Type", "image/png")
                        return f"data:{content_type};base64,{b64}"
        except Exception as e:
            logger.debug(f"OneBot tải avatar thất bại: {e}")
        return None

    async def get_group_avatar_url(
        self,
        group_id: str,
        size: int = 100,
    ) -> str | None:
        """Lấy URL avatar nhóm QQ."""
        actual_size = self._get_nearest_size(size)
        return self.GROUP_AVATAR_TEMPLATE.format(group_id=group_id, size=actual_size)

    async def batch_get_avatar_urls(
        self,
        user_ids: list[str],
        size: int = 100,
    ) -> dict[str, str | None]:
        """Ánh xạ hàng loạt ID QQ sang URL avatar."""
        return {
            user_id: await self.get_user_avatar_url(user_id, size)
            for user_id in user_ids
        }

    async def is_group_muted(self, group_id: str) -> bool:
        """
        Kiểm tra nhóm OneBot có mute toàn nhóm hoặc riêng bot hay không.
        """
        group_id_str = str(group_id)

        # 1. Kiểm tra trạng thái mute cache trong 5 phút.
        last_mute_time = self._muted_groups_cache.get(group_id_str)
        if last_mute_time and (time.time() - last_mute_time) < 300:
            logger.info(
                f"[OneBot] Cache cho thấy nhóm {group_id_str} vừa bị mute, bỏ qua phân tích"
            )
            return True

        if not hasattr(self.bot, "call_action"):
            return False

        # 2. Lấy ID QQ bot và lọc giá trị lỗi như functools.partial.
        bot_user_id = None
        if self.bot_self_ids:
            valid_ids = [
                str(uid)
                for uid in self.bot_self_ids
                if uid
                and isinstance(uid, (str, int))
                and not callable(uid)
                and "partial" not in str(uid)
                and str(uid).isdigit()
            ]
            if valid_ids:
                bot_user_id = valid_ids[0]

        if not bot_user_id:
            try:
                # Timeout 5 giây để tránh API treo vô hạn.
                login_info = await asyncio.wait_for(
                    self.bot.call_action("get_login_info"), timeout=5.0
                )
                if login_info and "user_id" in login_info:
                    bot_user_id = str(login_info["user_id"])
                    self.bot_self_ids = [bot_user_id]
            except Exception as e:
                if self._is_mute_exception(e):
                    self._record_mute_status(group_id, True)
                    logger.info(
                        f"[OneBot] Phát hiện bot bị mute trong nhóm {group_id} từ lỗi get_login_info"
                    )
                    return True
                logger.warning(f"[OneBot] Lấy thông tin đăng nhập bot thất bại: {e}")

        # 3. Kiểm tra bot bị mute riêng và lấy role trong nhóm.
        is_individually_muted = False
        role = "member"  # Mặc định member để an toàn.
        if bot_user_id:
            try:
                # Timeout 5 giây và không ép no_cache để tránh độ trễ Tencent.
                member_info = await asyncio.wait_for(
                    self.bot.call_action(
                        "get_group_member_info",
                        group_id=int(group_id),
                        user_id=int(bot_user_id),
                    ),
                    timeout=5.0,
                )
                if member_info:
                    role = member_info.get("role", "member")
                    # Cache role để fallback khi get_group_member_info timeout.
                    self._group_role_cache[group_id_str] = (role, time.time())
                    shut_up_time = member_info.get("shut_up_time", 0)
                    if shut_up_time > 0:
                        # shut_up_time có thể là Unix timestamp.
                        if shut_up_time > 1000000000:
                            if shut_up_time > time.time():
                                is_individually_muted = True
                        else:
                            # Nếu không thì là số giây mute còn lại.
                            is_individually_muted = True
            except asyncio.TimeoutError:
                # Fallback timeout: ưu tiên role đã cache.
                cached_role = self._group_role_cache.get(group_id_str)
                if cached_role:
                    role = cached_role[0]
                    logger.warning(
                        f"[OneBot] Lấy thông tin thành viên timeout, dùng role cache: {role} (group_id={group_id}, user_id={bot_user_id})"
                    )
                else:
                    logger.warning(
                        f"[OneBot] Lấy thông tin thành viên timeout và không có cache, dùng member (group_id={group_id}, user_id={bot_user_id})"
                    )
            except Exception as e:
                if self._is_mute_exception(e):
                    self._record_mute_status(group_id, True)
                    logger.info(
                        f"[OneBot] Phát hiện bot bị mute trong nhóm {group_id} từ lỗi get_group_member_info"
                    )
                    return True
                logger.warning(
                    f"[OneBot] Lấy thông tin thành viên thất bại (group_id={group_id}, user_id={bot_user_id}): {e}"
                )

        if is_individually_muted:
            self._record_mute_status(group_id, True)
            logger.info(f"[OneBot] Phát hiện bot bị mute riêng trong nhóm {group_id}")
            return True

        # 4. Nếu bot không phải admin/owner, kiểm tra mute toàn nhóm.
        if role not in ("admin", "owner"):
            try:
                # Timeout 5 giây, không truyền no_cache=True.
                group_info = await asyncio.wait_for(
                    self.bot.call_action(
                        "get_group_info",
                        group_id=int(group_id),
                    ),
                    timeout=5.0,
                )
                if group_info:
                    # Tương thích các trường mute toàn nhóm giữa các backend.
                    is_whole_ban = (
                        group_info.get("group_all_shut")
                        or group_info.get("shutup_all")
                        or group_info.get("is_whole_ban")
                        or group_info.get("whole_ban")
                        or group_info.get("shutup")
                        or group_info.get("shut_up")
                    )
                    if is_whole_ban:
                        self._record_mute_status(group_id, True)
                        logger.info(
                            f"[OneBot] Phát hiện nhóm {group_id} mute toàn nhóm và bot là thành viên thường"
                        )
                        return True
            except asyncio.TimeoutError:
                logger.warning(
                    f"[OneBot] Lấy thông tin nhóm timeout (group_id={group_id})"
                )
            except Exception as e:
                if self._is_mute_exception(e):
                    self._record_mute_status(group_id, True)
                    logger.info(
                        f"[OneBot] Phát hiện bot bị mute trong nhóm {group_id} từ lỗi get_group_info"
                    )
                    return True
                logger.warning(
                    f"[OneBot] Lấy thông tin nhóm thất bại (group_id={group_id}): {e}"
                )

        # Nếu không phát hiện mute thì xem là chưa mute.
        return False

    def _is_mute_exception(self, e: Exception) -> bool:
        if not e:
            return False
        err_str = str(e)

        mute_keywords = ("禁言", "操作失败", "下游群鉴权")

        # Cách 1: phát hiện theo retcode của các backend.
        if any(rc in err_str for rc in ("1200", "retcode=100", "result=120")):
            if any(kw in err_str for kw in mute_keywords):
                return True

        # Cách 2: kiểm tra thuộc tính message/wording của exception.
        for attr in ("message", "wording"):
            val = getattr(e, attr, "") or ""
            if any(kw in val for kw in mute_keywords):
                return True
            if "shut up" in val.lower():
                return True

        # Cách 3: phát hiện mẫu SnowLuma từ chối gửi tin nhắn.
        #   retcode=100, wording="send group message rejected: result=120 err="
        err_lower = err_str.lower()
        if "rejected" in err_lower and (
            "result=120" in err_lower or "muted" in err_lower
        ):
            return True

        # Cách 4: fallback khớp keyword mute trực tiếp trong err_str.
        if any(kw in err_str for kw in mute_keywords):
            return True

        return False

    def _record_mute_status(self, group_id: Any, is_muted: bool):
        group_id_str = str(group_id)
        if is_muted:
            # Prune expired cache entries if cache size grows too large (threshold of 1000)
            if len(self._muted_groups_cache) >= 1000:
                now = time.time()
                expired_keys = [
                    k for k, t in self._muted_groups_cache.items() if now - t >= 300
                ]
                for k in expired_keys:
                    self._muted_groups_cache.pop(k, None)

                # If still over threshold, evict the oldest entry to prevent unbounded growth
                if len(self._muted_groups_cache) >= 1000:
                    oldest_key = min(
                        self._muted_groups_cache,
                        key=lambda k: self._muted_groups_cache[k],
                    )
                    self._muted_groups_cache.pop(oldest_key, None)

            self._muted_groups_cache[group_id_str] = time.time()
        else:
            self._muted_groups_cache.pop(group_id_str, None)

    # ================================================================
    # Upload tệp nhóm / album nhóm.
    # ================================================================

    async def upload_group_file_to_folder(
        self,
        group_id: str,
        file_path: str,
        filename: str | None = None,
        folder_id: str | None = None,
    ) -> bool:
        """Upload tệp vào thư mục con chỉ định của tệp nhóm."""

        async def do_upload(content: str, label: str):
            params = {
                "group_id": int(group_id),
                "file": content,
                "name": filename or os.path.basename(file_path),
            }
            if folder_id:
                params["folder"] = folder_id
            await self.bot.call_action("upload_group_file", **params)
            logger.debug(
                f"[OneBot] Gửi tệp nhóm thành công ({label}): {params['name']}"
            )

        return await self._execute_transmission_strategy(
            file_path, do_upload, "Tệp nhóm OneBot"
        )

    async def create_group_file_folder(
        self,
        group_id: str,
        folder_name: str,
    ) -> str | None:
        """
        Tạo thư mục con trong thư mục gốc tệp nhóm.

        Args:
            group_id: ID nhóm đích.
            folder_name: Tên thư mục.

        Returns:
            folder_id nếu thành công, ngược lại None.
        """
        try:
            result = await self.bot.call_action(
                "create_group_file_folder",
                group_id=int(group_id),
                name=folder_name,
                parent_id="/",
            )
            # Một số implementation như go-cqhttp có thể không trả folder_id.
            folder_id = None
            if isinstance(result, dict):
                folder_id = result.get("folder_id") or result.get("id")
            logger.info(
                f"Tạo thư mục tệp nhóm OneBot thành công: {folder_name} (nhóm {group_id})"
                + (f" [ID: {folder_id}]" if folder_id else "")
            )
            return folder_id
        except Exception as e:
            error_msg = str(e).lower()
            # Thư mục đã tồn tại không được xem là lỗi.
            if "exist" in error_msg or "已存在" in error_msg:
                logger.info(
                    f"Thư mục tệp nhóm OneBot đã tồn tại: {folder_name} (nhóm {group_id})"
                )
                return None  # Cần lấy ID qua get_group_file_root_folders.
            logger.error(f"Tạo thư mục tệp nhóm OneBot thất bại: {e}")
            return None

    async def get_group_file_root_folders(
        self,
        group_id: str,
    ) -> list[dict]:
        """
        Lấy danh sách thư mục trong thư mục gốc tệp nhóm.

        Args:
            group_id: ID nhóm đích.

        Returns:
            Danh sách dict thư mục; trả list rỗng nếu API không khả dụng.
        """
        try:
            result = await self.bot.call_action(
                "get_group_root_files",
                group_id=int(group_id),
            )
            if isinstance(result, dict):
                return result.get("folders", []) or []
            return []
        except Exception as e:
            logger.debug(f"OneBot lấy danh sách thư mục nhóm thất bại: {e}")
            return []

    async def find_or_create_folder(
        self,
        group_id: str,
        folder_name: str,
    ) -> str | None:
        """
        Tìm hoặc tạo thư mục con tệp nhóm theo tên và trả folder_id.

        Trước tiên tìm trong thư mục gốc hiện có, nếu không thấy thì tạo mới.

        Args:
            group_id: ID nhóm đích.
            folder_name: Tên thư mục.

        """
        if not folder_name:
            return None

        # 1. Tìm thư mục hiện có.
        folders = await self.get_group_file_root_folders(group_id)
        for folder in folders:
            name = folder.get("folder_name") or folder.get("name", "")
            fid = folder.get("folder_id") or folder.get("id", "")
            if name == folder_name and fid:
                logger.debug(
                    f"Tìm thấy thư mục nhóm hiện có: {folder_name} [ID: {fid}]"
                )
                return fid

        # 2. Nếu chưa thấy, thử tạo.
        created_id = await self.create_group_file_folder(group_id, folder_name)
        if created_id:
            return created_id

        # 3. Tìm lại sau khi tạo vì một số backend không trả ID.
        folders = await self.get_group_file_root_folders(group_id)
        for folder in folders:
            name = folder.get("folder_name") or folder.get("name", "")
            fid = folder.get("folder_id") or folder.get("id", "")
            if name == folder_name and fid:
                logger.debug(
                    f"Tìm thấy thư mục nhóm sau khi tạo: {folder_name} [ID: {fid}]"
                )
                return fid

        logger.warning(
            f"Không thể lấy ID thư mục nhóm: {folder_name} (nhóm {group_id}); sẽ upload vào thư mục gốc"
        )
        return None

    async def upload_group_album(
        self,
        group_id: str,
        image_path: str,
        album_id: str | None = None,
        album_name: str | None = None,
        strict_mode: bool = False,
    ) -> bool:
        """Upload ảnh vào album nhóm qua API mở rộng NapCat."""
        # Strict mode: không upload khi có tên album nhưng không phân giải được ID.
        if strict_mode and album_name and not album_id:
            logger.info(
                f"[Album phân tích nhóm] Strict mode: không thấy album '{album_name}' (nhóm {group_id}), dừng upload"
            )
            return False

        # Truy vấn fallback.
        if not album_id:
            albums = await self.get_group_album_list(group_id)
            album_id = self._find_item_in_list(
                albums, album_name, ["album_id"], ["name", "album_name"]
            )
            # Nếu không chỉ định album, dùng album đầu tiên.
            if not album_id and not album_name and albums:
                album_id = albums[0].get("album_id") or albums[0].get("id")

        if not album_id:
            logger.info(
                f"[Album phân tích nhóm] Không xác định được album đích (nhóm {group_id}), bỏ qua upload"
            )
            return False

        async def do_upload(content: str, label: str):
            await self._detect_llbot()

            if self._is_llbot:
                # LLBot dùng tham số files dạng list.
                llbot_params = {
                    "group_id": int(group_id),
                    "album_id": str(album_id),
                    "files": [content],
                }
                try:
                    await self.bot.call_action("upload_group_album", **llbot_params)
                    logger.debug(
                        f"[Album phân tích nhóm] Upload thành công (LLBot, {label}): nhóm {group_id}"
                    )
                    return
                except Exception as e:
                    logger.warning(
                        f"[Album phân tích nhóm] API upload LLBot thất bại: {e}, thử chế độ NapCat..."
                    )

            params = {
                "group_id": int(group_id),
                "file": content,
                "album_id": str(album_id),
            }
            if album_name:
                params["album_name"] = album_name

            for action in [
                "upload_image_to_qun_album",
                "upload_group_album",
                "upload_qun_album",
            ]:
                try:
                    await self.bot.call_action(action, **params)
                    logger.debug(
                        f"[Album phân tích nhóm] Upload thành công ({label}, {action}): nhóm {group_id}"
                    )
                    return
                except Exception:
                    continue
            raise RuntimeError("Mọi API upload album đều thất bại")

        return await self._execute_transmission_strategy(
            image_path, do_upload, "Album OneBot"
        )

    async def get_group_album_list(
        self,
        group_id: str,
    ) -> list[dict]:
        """
        Lấy danh sách album nhóm, tương thích nhiều extension OneBot.
        """

        def extract_list(payload: Any) -> list[dict]:
            """Trích xuất album từ list trực tiếp, data lồng hoặc trường gốc."""
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
            if not isinstance(payload, dict):
                logger.debug(
                    f"[Album phân tích nhóm] Trích xuất thất bại: payload không phải dict/list ({type(payload)})"
                )
                return []

            data = payload.get("data")
            if isinstance(data, dict):
                album_list = data.get("album_list") or data.get("list")
                if isinstance(album_list, list):
                    return [item for item in album_list if isinstance(item, dict)]
                else:
                    logger.debug(
                        f"[Album phân tích nhóm] Không tìm thấy list trong trường data: {data}"
                    )

            album_list = payload.get("album_list") or payload.get("list")
            if isinstance(album_list, list):
                return [item for item in album_list if isinstance(item, dict)]

            logger.debug(
                f"[Album phân tích nhóm] Không thể trích xuất album từ phản hồi: {payload}"
            )
            return []

        # Tên API ứng viên.
        actions = [
            "get_qun_album_list",
            "get_group_album_list",
            "get_group_albums",
            "get_group_root_album_list",
        ]

        for action in actions:
            try:
                logger.debug(
                    f"[Album phân tích nhóm] Đang lấy danh sách qua {action} (nhóm: {group_id})..."
                )
                result = await self.bot.call_action(
                    action,
                    group_id=int(group_id),
                )
                logger.debug(
                    f"[Album phân tích nhóm] Phản hồi thô từ {action}: {result}"
                )
                if result:
                    albums = extract_list(result)
                    if albums:
                        logger.debug(
                            f"[Album phân tích nhóm] {action} lấy được {len(albums)} album"
                        )
                        return albums
            except Exception as e:
                logger.debug(f"[Album phân tích nhóm] Thử API {action} thất bại: {e}")

        return []

    async def find_album_id(
        self,
        group_id: str,
        album_name: str,
    ) -> str | None:
        """
        Tìm album_id theo tên, trả None để fallback album mặc định nếu không thấy.

        Args:
            group_id: ID nhóm đích.
            album_name: Tên album đích.

        Returns:
            album_id khớp hoặc None.
        """
        if not album_name:
            return None

        logger.debug(
            f"[Album phân tích nhóm] Đang tìm album '{album_name}' trong nhóm {group_id}..."
        )
        albums = await self.get_group_album_list(group_id)
        for album in albums:
            name = album.get("name") or album.get("album_name")
            logger.debug(
                f"[Album phân tích nhóm] So khớp album: đích='{album_name}', hiện tại='{name}', dữ liệu={album}"
            )
            if name == album_name:
                aid = album.get("album_id")
                if aid:
                    logger.info(
                        f"[Album phân tích nhóm] Xác định album thành công: '{album_name}' -> ID: {aid}"
                    )
                    return str(aid)
                else:
                    logger.debug(
                        f"[Album phân tích nhóm] Tên album '{name}' khớp nhưng thiếu album_id hợp lệ"
                    )

        logger.info(
            f"[Album phân tích nhóm] Không tìm thấy album '{album_name}' (nhóm {group_id})"
        )
        return None

    async def set_reaction(
        self, group_id: str, message_id: str, emoji: str | int, is_add: bool = True
    ) -> bool:
        """
        Triển khai reaction OneBot bằng set_msg_emoji_like.
        """
        try:
            reaction_key = str(emoji)
            emoji_id = {
                "analysis_started": "289",  # Đã nhận tác vụ.
                "analysis_done": "124",  # Đã xử lý xong tác vụ.
                "🔍": "289",
                "📊": "124",
            }.get(reaction_key, reaction_key)

            await self.bot.call_action(
                "set_msg_emoji_like",
                message_id=int(message_id),
                emoji_id=emoji_id,
                emoji_type="1",  # Loại emoji hệ thống ổn định nhất.
                set=is_add,
            )
            self._record_mute_status(group_id, False)
            return True
        except Exception as e:
            if self._is_mute_exception(e):
                self._record_mute_status(group_id, True)
            logger.debug(f"OneBot set_reaction thất bại, API có thể không hỗ trợ: {e}")
            return False

    def _get_use_base64(self) -> bool:
        """Lấy trạng thái bật Base64 từ cấu hình plugin."""
        plugin: Any = self.config.get("plugin_instance") if self.config else None
        if plugin and hasattr(plugin, "config_manager"):
            return plugin.config_manager.get_enable_base64_image()
        return False

    def _prepare_path(self, path: str) -> tuple[str, bool, bool]:
        """Chuẩn hoá path và trả path, trạng thái remote/encoded, trạng thái tồn tại."""
        is_remote = path.startswith(("http://", "https://", "base64://"))
        if is_remote:
            return path, True, True

        abs_path = os.path.abspath(path)
        exists = os.path.exists(abs_path)
        return abs_path, False, exists

    def _find_item_in_list(
        self,
        items: list[dict],
        target_name: str | None,
        id_keys: list[str],
        name_keys: list[str],
    ) -> str | None:
        """Tìm ID theo tên trong danh sách object."""
        if not target_name:
            return None

        for item in items:
            name = ""
            for nk in name_keys:
                if item.get(nk):
                    name = item[nk]
                    break

            if name == target_name:
                for ik in id_keys:
                    if item.get(ik):
                        return str(item[ik])
        return None
