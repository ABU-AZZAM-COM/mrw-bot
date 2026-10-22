"""
طبقة الأرشفة الرسمية — قناة موحّدة + نسخ احتياطي لقاعدة البيانات عبر تلغرام.

لماذا هذه الطبقة؟
لأن الاستضافة السحابية المجانية (مثل Hugging Face Spaces) تفقد قرصها عند كل
إعادة تشغيل أو إيقاف مؤقت. لكن ملفات تلغرام لا تُفقد أبدًا. لذلك نبني عليها:

1) **القناة الرسمية الموحّدة**: كل بلاغ جديد يُنشر فيها تلقائيًا (صورة + بطاقة
   كاملة + الرقم المرجعي) — فيصبح عندك سجل زمني رسمي واحد لكل البلاغات.
2) **الاستمرارية**: بعد كل تعديل تُرفع نسخة من قاعدة البيانات في القناة نفسها،
   وعند إقلاع البوت يستعيد آخر نسخة تلقائيًا — فلا تضيع أي بلاغ.

كل شيء هنا اختياري: إن لم تُضبط ARCHIVE_CHAT_ID فلن يتغيّر سلوك البوت إطلاقًا.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from telegram import Bot
from telegram.constants import ParseMode

import config
import texts
import utils

logger = logging.getLogger(__name__)

DB_SUFFIX = ".db"


def _has_rows(db_path: Path) -> bool:
    """هل تحتوي قاعدة البيانات المحلية على أي بلاغات مسجّلة؟"""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute("SELECT COUNT(*) FROM reports")
            row = cur.fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except Exception:
        return False


# ==================================================================
# استعادة قاعدة البيانات من القناة عند الإقلاع
# ==================================================================
async def restore_database_if_needed(bot_token: str, db_path: Path) -> bool:
    """يبحث عن آخر نسخة (.db) داخل القناة الرسمية ويعيدها إلى المسار المحلي.

    تُستدعى قبل فتح قاعدة البيانات، وتعود True إذا تمت الاستعادة فعلًا.
    ملاحظة: لا نُعيد الكتابة فوق قاعدة محلية تحتوي بيانات — الاستعادة تحدث
    فقط عند فقدان الملف (وهي الحالة الطبيعية بعد إعادة تشغيل الحاوية السحابية).
    """
    if not config.ARCHIVE_CHAT_ID:
        return False

    # إذا كانت هناك قاعدة محلية فيها بيانات، لا نمسّها
    if db_path.exists() and _has_rows(db_path):
        logger.info("توجد قاعدة بيانات محلية تحتوي بيانات — تم تجاوز الاستعادة")
        return False

    try:
        bot = Bot(token=bot_token)
        try:
            messages = await bot.get_messages(config.ARCHIVE_CHAT_ID, limit=50)
            target = None
            for msg in messages:
                doc = getattr(msg, "document", None)
                if doc and (doc.file_name or "").endswith(DB_SUFFIX):
                    target = msg
                    break

            if target is None:
                logger.info("لا توجد نسخة سابقة من قاعدة البيانات في القناة — بدء بقاعدة جديدة")
                return False

            with tempfile.TemporaryDirectory() as tmpdir:
                restored = Path(tmpdir) / "restored.db"
                await target.download(custom_path=str(restored))
                db_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(restored, db_path)

            # إزالة ملفات WAL/SHM القديمة حتى لا تتعارض مع النسخة المستعادة
            for suffix in ("-wal", "-shm"):
                extra = Path(str(db_path) + suffix)
                if extra.exists():
                    extra.unlink()

            logger.info("تمت استعادة قاعدة البيانات من القناة (%s)", target.document.file_name)
            return True
        finally:
            await bot.shutdown()
    except Exception:
        logger.exception("تعذّرت استعادة قاعدة البيانات من القناة")
        return False


# ==================================================================
# إنشاء نسخة احتياطية متسقة ورفعها إلى القناة
# ==================================================================
def _snapshot(db_path: Path, out_path: Path) -> bool:
    """نسخة متسقة من القاعدة عبر واجهة SQLite الرسمية (لا ملف تالف)."""
    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(out_path))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()
        return True
    except Exception:
        logger.exception("فشل إنشاء نسخة من قاعدة البيانات")
        return False


async def backup_database(bot: Bot, db_path: Path) -> bool:
    """ترفع نسخة من قاعدة البيانات إلى القناة الرسمية."""
    if not config.ARCHIVE_CHAT_ID:
        return False
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            snap = Path(tmpdir) / f"reports{DB_SUFFIX}"
            if not _snapshot(db_path, snap):
                return False
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
            await bot.send_document(
                chat_id=config.ARCHIVE_CHAT_ID,
                document=str(snap),
                filename=f"mrw-backup-{stamp}{DB_SUFFIX}",
                caption="نسخة احتياطية من قاعدة بيانات بلاغات المخالفات العسكرية.",
            )
        logger.info("تم رفع نسخة احتياطية إلى القناة الرسمية")
        return True
    except Exception:
        logger.warning("تعذّر رفع النسخة الاحتياطية إلى القناة", exc_info=True)
        return False


# ==================================================================
# نشر البلاغ في القناة الرسمية الموحّدة
# ==================================================================
async def post_report(bot: Bot, report: dict) -> bool:
    """ينشر البلاغ في القناة الرسمية (صورة + بطاقة كاملة + الرقم المرجعي)."""
    if not config.ARCHIVE_CHAT_ID:
        return False

    caption = texts.ARCHIVE_REPORT.format(
        seq=utils.esc(str(report.get("seq", "-"))),
        name=utils.esc(report.get("full_name") or "-"),
        username=texts.username_display(report.get("username")),
        time=utils.fmt_local(report.get("created_at", "")),
        anchor=utils.link_anchor(report.get("post_link", "")),
        desc=utils.esc(utils.trunc(report.get("description", ""), 800)),
    )

    try:
        photo = report.get("photo_file_id")
        if not photo:
            local = Path(str(report.get("photo_path") or ""))
            if local.exists():
                with open(local, "rb") as fp:
                    await bot.send_photo(
                        chat_id=config.ARCHIVE_CHAT_ID,
                        photo=fp,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                return True
            await bot.send_message(
                chat_id=config.ARCHIVE_CHAT_ID,
                text=caption,
                parse_mode=ParseMode.HTML,
            )
            return True

        await bot.send_photo(
            chat_id=config.ARCHIVE_CHAT_ID,
            photo=photo,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return True
    except Exception:
        logger.warning("تعذّر نشر البلاغ في القناة الرسمية", exc_info=True)
        return False


# ==================================================================
# إبقاء الخدمة السحابية نشطة (منع إيقافها التلقائي)
# ==================================================================
async def keepalive_loop(url: str, interval_seconds: int | None = None) -> None:
    """ينادي رابط الخدمة دوريًا حتى لا تُوقَف المنصة السحابية تلقائيًا.

    المنصات المجانية تُوقف أي خدمة لا تصل إليها طلبات عبر الإنترنت، لذلك نُبقي
    الخدمة حيّة بنبضة خفيفة كل بضع دقائق (من متغير KEEPALIVE_INTERVAL_SEC).
    """
    if not url:
        return
    interval_seconds = interval_seconds or config.KEEPALIVE_INTERVAL_SEC
    if interval_seconds < 30:
        interval_seconds = 30
    import httpx

    logger.info("بدأت نبضة الإبقاء نشطة كل %d ثانية: %s", interval_seconds, url)
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        while True:
            try:
                await client.get(url)
            except Exception:
                logger.debug("فشلت نبضة الإبقاء — سيُعاد المحاولة", exc_info=True)
            await asyncio.sleep(interval_seconds)


# ==================================================================
# النسخ الاحتياطي التلقائي الدوري
# ==================================================================
async def backup_loop(bot: Bot, db_path: Path, interval_minutes: int) -> None:
    """ينسخ قاعدة البيانات إلى القناة كل فترة محددة."""
    if not config.ARCHIVE_CHAT_ID or interval_minutes <= 0:
        return
    logger.info("بدأ النسخ الاحتياطي التلقائي كل %d دقيقة", interval_minutes)
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            await backup_database(bot, db_path)
        except Exception:
            logger.warning("فشل النسخ الاحتياطي الدوري", exc_info=True)
