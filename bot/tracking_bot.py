"""Telegram bot methods with private-message history tracking."""
from __future__ import annotations

import html
import logging
import re
from typing import Any

from telegram.ext import ExtBot

logger = logging.getLogger(__name__)


class TrackedBot(ExtBot):
    __slots__ = ("_tracking_db", "_tracking_admin_ids")

    def __init__(self, token: str, admin_ids: set[int] | frozenset[int]):
        super().__init__(token=token)
        self._tracking_db: Any = None
        self._tracking_admin_ids = frozenset(admin_ids)

    def set_tracking_database(self, db: Any) -> None:
        self._tracking_db = db

    async def _track(
        self,
        message: Any,
        content: str | None,
        media_type: str | None = None,
        parse_mode: Any = None,
    ) -> None:
        db = self._tracking_db
        chat = getattr(message, "chat", None)
        user_id = getattr(chat, "id", None)
        if (
            db is None
            or getattr(chat, "type", None) != "private"
            or not isinstance(user_id, int)
            or user_id in self._tracking_admin_ids
        ):
            return

        file_id = None
        filename = None
        media = getattr(message, media_type, None) if media_type else None
        if media_type == "media":
            media_type = next(
                (
                    kind for kind in ("photo", "video", "audio", "document")
                    if getattr(message, kind, None)
                ),
                "document",
            )
            media = getattr(message, media_type, None)
        if media_type == "photo":
            photos = getattr(message, "photo", None) or []
            media = photos[-1] if photos else None
        if media is not None:
            file_id = getattr(media, "file_id", None)
            filename = getattr(media, "file_name", None)
        visible_content = str(content or "")
        if getattr(parse_mode, "value", parse_mode) == "HTML":
            visible_content = html.unescape(
                re.sub(r"</?[^>]+>", "", visible_content)
            )
        visible_content = visible_content.strip()
        if media_type and not visible_content:
            visible_content = f"[وسيط: {media_type}]"
        try:
            await db.open_user_conversation(user_id)
            await db.add_user_message(
                user_id,
                "out",
                visible_content,
                media_type=media_type,
                telegram_file_id=file_id,
                telegram_message_id=getattr(message, "message_id", None),
                filename=filename,
            )
        except Exception:
            logger.exception(
                "Telegram sent a private message that could not be recorded "
                "(recipient=%s, message_id=%s)",
                user_id,
                getattr(message, "message_id", None),
            )

    async def send_message(self, chat_id: Any, text: str, *args, **kwargs):
        message = await super().send_message(chat_id, text, *args, **kwargs)
        await self._track(message, text, parse_mode=kwargs.get("parse_mode"))
        return message

    async def send_photo(self, chat_id: Any, photo: Any, *args, **kwargs):
        caption = kwargs.get("caption", args[0] if args else None)
        message = await super().send_photo(chat_id, photo, *args, **kwargs)
        await self._track(
            message, caption, "photo", kwargs.get("parse_mode")
        )
        return message

    async def send_video(self, chat_id: Any, video: Any, *args, **kwargs):
        caption = kwargs.get("caption", args[0] if args else None)
        message = await super().send_video(chat_id, video, *args, **kwargs)
        await self._track(
            message, caption, "video", kwargs.get("parse_mode")
        )
        return message

    async def send_audio(self, chat_id: Any, audio: Any, *args, **kwargs):
        caption = kwargs.get("caption", args[0] if args else None)
        message = await super().send_audio(chat_id, audio, *args, **kwargs)
        await self._track(
            message, caption, "audio", kwargs.get("parse_mode")
        )
        return message

    async def send_voice(self, chat_id: Any, voice: Any, *args, **kwargs):
        caption = kwargs.get("caption", args[0] if args else None)
        message = await super().send_voice(chat_id, voice, *args, **kwargs)
        await self._track(
            message, caption, "voice", kwargs.get("parse_mode")
        )
        return message

    async def send_document(self, chat_id: Any, document: Any, *args, **kwargs):
        caption = kwargs.get("caption", args[0] if args else None)
        message = await super().send_document(chat_id, document, *args, **kwargs)
        await self._track(
            message, caption, "document", kwargs.get("parse_mode")
        )
        return message

    async def send_animation(self, chat_id: Any, animation: Any, *args, **kwargs):
        caption = kwargs.get("caption", args[0] if args else None)
        message = await super().send_animation(chat_id, animation, *args, **kwargs)
        await self._track(
            message, caption, "animation", kwargs.get("parse_mode")
        )
        return message

    async def send_sticker(self, chat_id: Any, sticker: Any, *args, **kwargs):
        message = await super().send_sticker(chat_id, sticker, *args, **kwargs)
        await self._track(message, None, "sticker")
        return message

    async def send_video_note(self, chat_id: Any, video_note: Any, *args, **kwargs):
        message = await super().send_video_note(
            chat_id, video_note, *args, **kwargs
        )
        await self._track(message, None, "video_note")
        return message

    async def send_media_group(self, chat_id: Any, media: Any, *args, **kwargs):
        messages = await super().send_media_group(
            chat_id, media, *args, **kwargs
        )
        for message in messages:
            await self._track(message, getattr(message, "caption", None), "media")
        return messages
