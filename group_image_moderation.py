"""Sightengine-based moderation for images posted in Telegram groups."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SIGHTENGINE_URL = "https://api.sightengine.com/1.0/check.json"
EXPLICIT_THRESHOLD = 0.70
REQUEST_TIMEOUT_SECONDS = 25.0


class SightengineError(RuntimeError):
    """The external moderation service could not return a usable result."""


@dataclass(frozen=True)
class NudityScores:
    raw: float
    sexual_activity: float

    @property
    def is_explicit(self) -> bool:
        return self.raw > EXPLICIT_THRESHOLD or self.sexual_activity > EXPLICIT_THRESHOLD


def credentials() -> tuple[str, str] | None:
    api_user = os.getenv("SIGHTENGINE_API_USER", "").strip()
    api_secret = (
        os.getenv("SIGHTENGINE_API_SECRET", "")
        or os.getenv("SIGHTENGINE_SECRET", "")
    ).strip()
    if not api_user or not api_secret:
        return None
    return api_user, api_secret


def parse_scores(payload: Any) -> NudityScores:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise SightengineError("Sightengine returned an unsuccessful response")
    nudity = payload.get("nudity")
    if not isinstance(nudity, dict):
        raise SightengineError("Sightengine response has no nudity scores")

    values: list[float] = []
    for key in ("raw", "sexual_activity"):
        value = nudity.get(key, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SightengineError(f"Invalid Sightengine score: {key}")
        score = float(value)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise SightengineError(f"Invalid Sightengine score: {key}")
        values.append(score)
    return NudityScores(raw=values[0], sexual_activity=values[1])


async def inspect_image(path: str | Path) -> NudityScores:
    auth = credentials()
    if auth is None:
        raise SightengineError("Sightengine credentials are not configured")

    api_user, api_secret = auth
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            with Path(path).open("rb") as image:
                response = await client.post(
                    SIGHTENGINE_URL,
                    data={
                        "api_user": api_user,
                        "api_secret": api_secret,
                        "models": "nudity-2.0",
                    },
                    files={"media": (Path(path).name, image, "application/octet-stream")},
                )
            response.raise_for_status()
            return parse_scores(response.json())
    except SightengineError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise SightengineError("Sightengine request or response failed") from exc


async def _notify_admins(context, text: str) -> None:
    import config

    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify admin %s about group image moderation", admin_id)


async def moderate_group_image(update, context) -> None:
    """Check one group image, then delete and permanently ban on a positive result."""
    import config

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or user.is_bot:
        return

    if not credentials():
        await _notify_admins(
            context,
            "تعذر فحص صورة في مجموعة: بيانات Sightengine غير مضبوطة. "
            "لم يُتخذ إجراء تلقائي.",
        )
        return

    media = message.photo[-1] if message.photo else message.document
    if media is None:
        return
    if media.file_size and media.file_size > config.MAX_PHOTO_MB * 1024 * 1024:
        await _notify_admins(
            context,
            f"تجاوزت صورة المجموعة حد الفحص ({config.MAX_PHOTO_MB} ميغابايت)؛ "
            f"لم يُحظر المستخدم <code>{user.id}</code>.",
        )
        return

    temporary_path: Path | None = None
    try:
        import tempfile

        with tempfile.NamedTemporaryFile(
            prefix="mrw-group-image-", suffix=".image", delete=False
        ) as handle:
            temporary_path = Path(handle.name)

        telegram_file = await media.get_file()
        await telegram_file.download_to_drive(custom_path=str(temporary_path))
        scores = await inspect_image(temporary_path)
    except Exception:
        logger.exception(
            "Sightengine image check failed in chat %s for user %s",
            message.chat_id,
            user.id,
        )
        await _notify_admins(
            context,
            f"تعذر فحص صورة في المجموعة <code>{message.chat_id}</code> "
            f"للمستخدم <code>{user.id}</code>. لم يُحظر المستخدم.",
        )
        return
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove temporary group image")

    if not scores.is_explicit:
        return

    deleted = False
    banned = False
    try:
        await message.delete()
        deleted = True
    except Exception:
        logger.exception(
            "Could not delete explicit image message %s in chat %s",
            message.message_id,
            message.chat_id,
        )

    try:
        await context.bot.ban_chat_member(
            chat_id=message.chat_id,
            user_id=user.id,
            revoke_messages=True,
        )
        banned = True
    except Exception:
        logger.exception(
            "Could not permanently ban user %s from chat %s",
            user.id,
            message.chat_id,
        )

    if banned:
        notice = (
            f"🚫 تم حظر العضو {user.mention_html()} لإرساله محتوى غير لائق. "
            f"(raw: {scores.raw:.0%}، sexual_activity: {scores.sexual_activity:.0%})"
        )
    else:
        notice = (
            f"⚠️ رُصد محتوى غير لائق من {user.mention_html()} "
            f"(raw: {scores.raw:.0%}، sexual_activity: {scores.sexual_activity:.0%})، "
            "لكن تعذر إتمام الحظر. تحقق من صلاحيات البوت."
        )
    if not deleted:
        notice += " وتعذر حذف الرسالة."
    try:
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=notice,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception(
            "Could not post moderation notice in chat %s", message.chat_id
        )
