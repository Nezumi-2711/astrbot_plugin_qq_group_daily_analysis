"""
Adapter nền tảng Discord.

Cung cấp khả năng lấy và gửi tin nhắn cùng quản lý nhóm. Việc lấy tin nhắn
phụ thuộc cách AstrBot tích hợp Discord API.
"""

from datetime import datetime, timedelta
from typing import Any

from ....utils.logger import logger

try:
    import discord
except ImportError:
    discord = None

from ....domain.value_objects.platform_capabilities import (
    DISCORD_CAPABILITIES,
    PlatformCapabilities,
)
from ....domain.value_objects.unified_group import UnifiedGroup, UnifiedMember
from ....domain.value_objects.unified_message import (
    MessageContent,
    MessageContentType,
    UnifiedMessage,
)
from ..base import PlatformAdapter


class DiscordAdapter(PlatformAdapter):
    """
    Triển khai adapter nền tảng Discord.

    Dùng Discord API để lấy/gửi tin nhắn và truy vấn metadata cơ bản cho kênh.
    Adapter tích hợp lazy loading client và cơ chế truy vấn kênh nhiều cấp.

    Attributes:
        bot_user_id: ID Discord của bot.
    """

    def __init__(self, bot_instance: Any, config: dict | None = None):
        """
        Khởi tạo adapter Discord.

        Args:
            bot_instance: Instance bot chủ quản.
            config: Cấu hình dùng để lấy ID Discord của bot.
        """
        super().__init__(bot_instance, config)
        # ID của bot dùng để lọc tin nhắn do bot gửi.
        self.bot_user_id = str(config.get("bot_user_id", "")) if config else ""

        # Cache Discord client theo lazy loading.
        self._cached_client = None

    @property
    def _discord_client(self) -> Any:
        """
        Lấy instance Discord client thực tế.

        Hỗ trợ lazy loading và tự nhận diện client.

        Returns:
            Đối tượng Discord Client.
        """
        if self._cached_client:
            return self._cached_client

        # Dò đường dẫn để tương thích nhiều cấu trúc AstrBot.
        self._cached_client = self._get_discord_client()

        # Fallback: lấy ID bot từ trạng thái kết nối client.
        if not self.bot_user_id and self._cached_client:
            if hasattr(self._cached_client, "user") and self._cached_client.user:
                self.bot_user_id = str(self._cached_client.user.id)

        return self._cached_client

    def _get_discord_client(self) -> Any:
        """Dò nhiều cấp để lấy Discord SDK client từ bot_instance."""
        # Đường A: bot chính là Client.
        if hasattr(self.bot, "get_channel"):
            return self.bot
        # Đường B: bot là wrapper và client nằm trong thuộc tính chuẩn.
        if hasattr(self.bot, "client"):
            return self.bot.client
        # Đường C: các tên thuộc tính riêng phổ biến khác.
        for attr in ("_client", "discord_client", "_discord_client"):
            if hasattr(self.bot, attr):
                client = getattr(self.bot, attr)
                if hasattr(client, "get_channel"):
                    return client
        logger.warning(f"Không thể lấy Discord client từ {type(self.bot).__name__}")
        return None

    def _init_capabilities(self) -> PlatformCapabilities:
        """Trả về bộ năng lực Discord được định nghĩa sẵn."""
        return DISCORD_CAPABILITIES

    # ==================== Triển khai IMessageRepository ====================

    async def fetch_messages(
        self,
        group_id: str,
        days: int = 1,
        max_count: int = 100,
        before_id: str | None = None,
        since_ts: int | None = None,
    ) -> list[UnifiedMessage]:
        """
        Lấy bất đồng bộ lịch sử tin nhắn từ kênh Discord.

        Args:
            group_id: ID kênh Discord.
            days: Phạm vi số ngày truy vấn.
            max_count: Số tin nhắn tối đa.
            before_id: ID tin nhắn mốc; lấy các tin trước đó.

        Returns:
            Danh sách tin nhắn định dạng thống nhất.
        """
        if not discord:
            logger.error(
                "Không tìm thấy module Discord (py-cord), không thể lấy lịch sử"
            )
            return []

        try:
            channel_id = int(group_id)
            # Thử lấy kênh từ cache trước.
            channel = self._discord_client.get_channel(channel_id)
            if not channel:
                # Nếu cache miss thì fetch qua mạng.
                try:
                    channel = await self._discord_client.fetch_channel(channel_id)
                except Exception as e:
                    logger.debug(f"Lấy kênh Discord {group_id} thất bại: {e}")
                    return []

            # Xác thực hỗ trợ truy cập lịch sử.
            if not hasattr(channel, "history"):
                logger.warning(f"Kênh {group_id} không hỗ trợ truy cập lịch sử")
                return []

            if since_ts and since_ts > 0:
                start_time = datetime.fromtimestamp(since_ts)
            else:
                end_time = datetime.now()
                start_time = end_time - timedelta(days=days)

            messages = []

            # Xây dựng tham số truy vấn history cho Discord SDK.
            history_kwargs = {"limit": max_count, "after": start_time}
            if before_id:
                try:
                    # Dùng Snowflake ID để trỏ tới tin nhắn cụ thể.
                    history_kwargs["before"] = discord.Object(id=int(before_id))
                except (ValueError, TypeError):
                    pass

            # Duyệt và xử lý tin nhắn.
            async for msg in channel.history(**history_kwargs):
                # Loại tin nhắn do chính bot gửi.
                if self.bot_user_id and str(msg.author.id) == self.bot_user_id:
                    continue

                unified = self._convert_message(msg, group_id)
                if unified:
                    messages.append(unified)

            # Sắp xếp tăng dần vì SDK thường trả về giảm dần.
            messages.sort(key=lambda m: m.timestamp)
            return messages

        except Exception as e:
            logger.error(f"Discord fetch_messages failed: {e}", exc_info=True)
            return []

    def _convert_message(self, raw_msg: Any, group_id: str) -> UnifiedMessage | None:
        """Chuyển ``discord.Message`` thành ``UnifiedMessage``."""
        try:
            contents = []

            # 1. Văn bản cơ bản.
            if raw_msg.content:
                contents.append(
                    MessageContent(type=MessageContentType.TEXT, text=raw_msg.content)
                )

            # 2. Xử lý tệp đính kèm: ảnh, video, âm thanh và tệp thường.
            for attachment in raw_msg.attachments:
                content_type = attachment.content_type or ""
                if content_type.startswith("image/"):
                    contents.append(
                        MessageContent(
                            type=MessageContentType.IMAGE, url=attachment.url
                        )
                    )
                elif content_type.startswith("video/"):
                    contents.append(
                        MessageContent(
                            type=MessageContentType.VIDEO, url=attachment.url
                        )
                    )
                elif content_type.startswith("audio/"):
                    contents.append(
                        MessageContent(
                            type=MessageContentType.VOICE, url=attachment.url
                        )
                    )
                else:
                    contents.append(
                        MessageContent(
                            type=MessageContentType.FILE,
                            url=attachment.url,
                            raw_data={
                                "filename": attachment.filename,
                                "size": attachment.size,
                            },
                        )
                    )

            # 3. Xử lý nội dung embed có mô tả rich text.
            for embed in raw_msg.embeds:
                if embed.image:
                    contents.append(
                        MessageContent(
                            type=MessageContentType.IMAGE, url=embed.image.url
                        )
                    )
                if embed.description:
                    contents.append(
                        MessageContent(
                            type=MessageContentType.TEXT,
                            text=f"\n[Embed] {embed.description}",
                        )
                    )

            # 4. Xử lý sticker.
            if raw_msg.stickers:
                for sticker in raw_msg.stickers:
                    contents.append(
                        MessageContent(
                            type=MessageContentType.IMAGE,  # Xử lý sticker như ảnh.
                            url=sticker.url,
                            raw_data={
                                "sticker_id": str(sticker.id),
                                "sticker_name": sticker.name,
                            },
                        )
                    )

            # Tên hiển thị: biệt danh server > tên toàn cục > username.
            sender_card = None
            if hasattr(raw_msg.author, "nick") and raw_msg.author.nick:
                sender_card = raw_msg.author.nick
            elif hasattr(raw_msg.author, "global_name") and raw_msg.author.global_name:
                sender_card = raw_msg.author.global_name

            return UnifiedMessage(
                message_id=str(raw_msg.id),
                sender_id=str(raw_msg.author.id),
                sender_name=raw_msg.author.name,
                sender_card=sender_card,
                group_id=group_id,
                text_content=raw_msg.content,
                contents=tuple(contents),
                timestamp=int(raw_msg.created_at.timestamp()),
                platform="discord",
                reply_to_id=str(raw_msg.reference.message_id)
                if raw_msg.reference
                else None,
            )
        except Exception as e:
            logger.debug(f"Lỗi chuyển đổi tin nhắn Discord: {e}")
            return None

    def convert_to_raw_format(self, messages: list[UnifiedMessage]) -> list[dict]:
        """Chuyển định dạng thống nhất thành dict kiểu OneBot cho tầng sau."""
        raw_messages = []
        for msg in messages:
            raw_msg = {
                "message_id": msg.message_id,
                "group_id": msg.group_id,
                "time": msg.timestamp,
                "sender": {
                    "user_id": msg.sender_id,
                    "nickname": msg.sender_name,
                    "card": msg.sender_card,
                },
                "message": [],
                "user_id": msg.sender_id,  # Tương thích ngược.
            }

            for content in msg.contents:
                if content.type == MessageContentType.TEXT:
                    raw_msg["message"].append(
                        {"type": "text", "data": {"text": content.text or ""}}
                    )
                elif content.type == MessageContentType.IMAGE:
                    raw_msg["message"].append(
                        {
                            "type": "image",
                            "data": {"url": content.url, "file": content.url},
                        }
                    )
                elif content.type == MessageContentType.AT:
                    raw_msg["message"].append(
                        {"type": "at", "data": {"qq": content.at_user_id}}
                    )
                elif content.type == MessageContentType.REPLY:
                    if content.raw_data and "reply_id" in content.raw_data:
                        raw_msg["message"].append(
                            {
                                "type": "reply",
                                "data": {"id": content.raw_data["reply_id"]},
                            }
                        )

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
        Gửi tin nhắn văn bản tới kênh Discord.

        Args:
            group_id: ID kênh.
            text: Nội dung văn bản.
            reply_to: ID tin nhắn được trả lời.

        Returns:
            True nếu gửi thành công.
        """
        if not discord:
            return False

        try:
            channel_id = int(group_id)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)

            if not hasattr(channel, "send"):
                return False

            reference = None
            if reply_to:
                try:
                    reference = discord.MessageReference(
                        message_id=int(reply_to), channel_id=channel_id
                    )
                except (ValueError, TypeError):
                    pass

            await channel.send(content=text, reference=reference)
            return True
        except Exception as e:
            logger.error(f"Gửi văn bản Discord thất bại: {e}")
            return False

    async def send_image(
        self,
        group_id: str,
        image_path: str,
        caption: str = "",
    ) -> bool:
        """
        Gửi ảnh bất đồng bộ tới kênh Discord.

        URL từ xa được tải vào bộ nhớ trước khi gửi qua Discord API.

        Args:
            group_id: ID kênh.
            image_path: Đường dẫn cục bộ hoặc URL HTTP.
            caption: Chú thích tuỳ chọn.

        Returns:
            True nếu gửi thành công.
        """
        if not discord:
            return False

        try:
            channel_id = int(group_id)
            channel = self._discord_client.get_channel(channel_id)
            if not channel:
                channel = await self._discord_client.fetch_channel(channel_id)

            if not hasattr(channel, "send"):
                return False

            file_to_send = None
            if image_path.startswith("base64://"):
                # Ảnh Base64: decode -> object trong bộ nhớ -> Discord.
                import base64  # Fix: Ensure base64 is imported
                from io import BytesIO

                try:
                    base64_data = image_path.split("base64://")[1]
                    image_bytes = base64.b64decode(base64_data)
                    file_to_send = discord.File(
                        BytesIO(image_bytes), filename="daily_report_image.png"
                    )
                except Exception as e:
                    logger.error(f"Giải mã ảnh Base64 Discord thất bại: {e}")
                    return False

            elif image_path.startswith(("http://", "https://")):
                # Ảnh từ xa: tải -> object trong bộ nhớ -> Discord.
                from io import BytesIO

                import aiohttp

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            image_path, timeout=aiohttp.ClientTimeout(total=30)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                # Cố gắng giữ phần mở rộng gốc.
                                filename = image_path.split("/")[-1].split("?")[0]
                                if not filename.lower().endswith(
                                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                                ):
                                    filename = "daily_report_image.png"

                                file_to_send = discord.File(
                                    BytesIO(data), filename=filename
                                )
                            else:
                                # Fallback: gửi URL để Discord tự phân tích.
                                content = (
                                    f"{caption}\n{image_path}"
                                    if caption
                                    else image_path
                                )
                                await channel.send(content=content)
                                return True
                except Exception as de:
                    logger.warning(
                        f"Tải ảnh Discord từ xa thất bại: {de}; chuyển sang gửi URL."
                    )
                    content = f"{caption}\n{image_path}" if caption else image_path
                    await channel.send(content=content)
                    return True
            else:
                # Ảnh cục bộ.
                file_to_send = discord.File(image_path)

            if file_to_send:
                await channel.send(content=caption or None, file=file_to_send)
            return True

        except Exception as e:
            logger.error(f"Gửi ảnh Discord thất bại: {e}")
            return False

    async def send_file(
        self,
        group_id: str,
        file_path: str,
        filename: str | None = None,
    ) -> bool:
        """Tải tệp bất kỳ lên kênh Discord."""
        if not discord:
            return False

        try:
            channel_id = int(group_id)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)

            if not hasattr(channel, "send"):
                return False

            file_to_send = discord.File(file_path, filename=filename)
            await channel.send(file=file_to_send)
            return True
        except Exception as e:
            logger.error(f"Gửi tệp Discord thất bại: {e}")
            return False

    async def send_forward_msg(
        self,
        group_id: str,
        nodes: list[dict],
    ) -> bool:
        """
        Mô phỏng chuyển tiếp gộp trên Discord.

        Discord không có API chuyển tiếp node gốc nên chuyển thành nhóm tin văn bản.
        """
        if not discord:
            return False

        try:
            channel_id = int(group_id)
            channel = self._discord_client.get_channel(channel_id)
            if not channel:
                channel = await self._discord_client.fetch_channel(channel_id)

            if not hasattr(channel, "send"):
                return False

            # Tổng hợp node thành khối văn bản có định dạng.
            lines = ["📊 **Tóm tắt báo cáo có cấu trúc (Structured Report)**\n"]
            for node in nodes:
                data = node.get("data", node)  # Tương thích nhiều định dạng.
                name = data.get("name", "AstrBot")
                content = data.get("content", "")
                lines.append(f"**[{name}]**:\n{content}\n")

            full_text = "\n".join(lines)

            # Chia nhỏ tin nhắn dài.
            if len(full_text) > 1900:
                parts = [
                    full_text[i : i + 1900] for i in range(0, len(full_text), 1900)
                ]
                for part in parts:
                    await channel.send(content=part)
            else:
                await channel.send(content=full_text)

            return True
        except Exception as e:
            logger.error(f"Mô phỏng chuyển tiếp Discord thất bại: {e}")
            return False

    # ==================== Triển khai IGroupInfoRepository ====================

    async def get_group_info(self, group_id: str) -> UnifiedGroup | None:
        """Phân tích thông tin cơ bản của kênh và server Discord."""
        if not discord:
            return None

        try:
            channel_id = int(group_id)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)

            guild = getattr(channel, "guild", None)
            group_name = getattr(channel, "name", str(channel.id))

            if guild:
                # Kênh server.
                member_count = guild.member_count
                owner_id = str(guild.owner_id)
            else:
                # Tin nhắn riêng.
                member_count = len(getattr(channel, "recipients", [])) + 1
                owner_id = str(getattr(channel, "owner_id", ""))

            return UnifiedGroup(
                group_id=str(channel.id),
                group_name=group_name,
                member_count=member_count,
                owner_id=owner_id or None,
                create_time=int(channel.created_at.timestamp()),
                platform="discord",
            )
        except Exception as e:
            logger.debug(f"Lỗi lấy thông tin nhóm Discord: {e}")
            return None

    async def get_group_list(self) -> list[str]:
        """Liệt kê ID kênh văn bản bot có thể truy cập trên các server."""
        if not discord:
            return []

        try:
            channel_ids = []
            for guild in self._discord_client.guilds:
                for channel in guild.text_channels:
                    channel_ids.append(str(channel.id))
            return channel_ids
        except Exception:
            return []

    async def get_member_list(self, group_id: str) -> list[UnifiedMember]:
        """
        Lấy danh sách thành viên của kênh.

        Nên bật intent GUILD_MEMBERS trên server lớn để đảm bảo đầy đủ.
        """
        if not discord:
            return []

        try:
            channel_id = int(group_id)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)

            guild = getattr(channel, "guild", None)
            if not guild:
                # Người nhận tin nhắn riêng.
                return [
                    UnifiedMember(
                        user_id=str(u.id),
                        nickname=u.name,
                        card=u.display_name,
                        role="member",
                    )
                    for u in getattr(channel, "recipients", [])
                ]

            members = []
            for member in guild.members:
                role = "member"
                if member.id == guild.owner_id:
                    role = "owner"
                elif member.guild_permissions.administrator:
                    role = "admin"

                members.append(
                    UnifiedMember(
                        user_id=str(member.id),
                        nickname=member.name,
                        card=member.nick or member.global_name,
                        role=role,
                        join_time=int(member.joined_at.timestamp())
                        if member.joined_at
                        else None,
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
        """Lấy và phân tích thông tin định danh của thành viên Discord."""
        if not discord:
            return None

        try:
            uid = int(user_id)
            channel_id = int(group_id)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)

            guild = getattr(channel, "guild", None)
            if not guild:
                # Dò xuyên kênh hoặc tin nhắn riêng.
                user = await self.bot.fetch_user(uid)
                return UnifiedMember(
                    user_id=str(user.id), nickname=user.name, card=user.display_name
                )

            member = guild.get_member(uid) or await guild.fetch_member(uid)
            if not member:
                return None

            role = (
                "owner"
                if member.id == guild.owner_id
                else ("admin" if member.guild_permissions.administrator else "member")
            )

            return UnifiedMember(
                user_id=str(member.id),
                nickname=member.name,
                card=member.nick or member.global_name,
                role=role,
                join_time=int(member.joined_at.timestamp())
                if member.joined_at
                else None,
            )
        except Exception:
            return None

    # ==================== Triển khai IAvatarRepository ====================

    async def get_user_avatar_url(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """Phân giải động URL CDN avatar theo ID thành viên Discord."""
        if not discord or not self._discord_client:
            return None

        try:
            uid = int(user_id)
            user = self._discord_client.get_user(
                uid
            ) or await self._discord_client.fetch_user(uid)

            if user:
                # Chọn kích thước gần nhất được Discord hỗ trợ.
                allowed_sizes = (16, 32, 64, 128, 256, 512, 1024, 2048, 4096)
                target_size = min(allowed_sizes, key=lambda x: abs(x - size))
                return user.display_avatar.with_size(target_size).url

            return None
        except Exception as e:
            logger.debug(f"Lỗi lấy URL avatar Discord: {e}")
            return None

    async def get_user_avatar_data(
        self,
        user_id: str,
        size: int = 100,
    ) -> str | None:
        """Chưa hỗ trợ Base64; ưu tiên URL CDN."""
        return None

    async def get_group_avatar_url(
        self,
        group_id: str,
        size: int = 100,
    ) -> str | None:
        """Lấy URL biểu tượng server Discord."""
        if not discord:
            return None

        try:
            channel = self.bot.get_channel(
                int(group_id)
            ) or await self.bot.fetch_channel(int(group_id))
            guild = getattr(channel, "guild", None)
            if guild and guild.icon:
                allowed_sizes = (16, 32, 64, 128, 256, 512, 1024, 2048, 4096)
                target_size = min(allowed_sizes, key=lambda x: abs(x - size))
                return guild.icon.with_size(target_size).url
            return None
        except Exception:
            return None

    async def batch_get_avatar_urls(
        self,
        user_ids: list[str],
        size: int = 100,
    ) -> dict[str, str | None]:
        """Lấy hàng loạt URL avatar."""
        return {uid: await self.get_user_avatar_url(uid, size) for uid in user_ids}

    async def set_reaction(
        self, group_id: str, message_id: str, emoji: str | int, is_add: bool = True
    ) -> bool:
        """
        Triển khai reaction tin nhắn Discord.
        """
        if not discord:
            return False

        try:
            reaction_key = str(emoji)
            emoji_to_use = {
                "analysis_started": "🔍",
                "analysis_done": "📊",
                "289": "🔍",
                "124": "📊",
                "424": "📊",
            }.get(reaction_key, reaction_key)

            channel_id = int(group_id)
            channel = self._discord_client.get_channel(channel_id)
            if not channel:
                channel = await self._discord_client.fetch_channel(channel_id)

            if not hasattr(channel, "get_partial_message"):
                # Fetch trực tiếp nếu SDK cũ không có phương thức này.
                msg = await channel.fetch_message(int(message_id))
            else:
                msg = channel.get_partial_message(int(message_id))

            if is_add:
                await msg.add_reaction(emoji_to_use)
            else:
                await msg.remove_reaction(emoji_to_use, self._discord_client.user)
            return True
        except Exception as e:
            logger.debug(f"Discord set_reaction thất bại: {e}")
            return False
