"""
النسخ الاحتياطي السحابي إلى Hugging Face Hub — الطبقة الأساسية لحماية البلاغات.

لماذا؟ لأن قرص حاوية السبيس مؤقّت: كل إعادة تشغيل أو بناء يمسح `data/` بالكامل.
المخزن الجديد مستودع Dataset **خاص** في حسابنا على HF (مساحة مجانية 100GB)،
والعمل عليه عبر huggingface_hub نفسها المستخدمة للنشر — حل مجاني ودائم
بلا أي حدود تلغرام.

ما تفعله الوحدة:
  - `upload_backup()`        : لقطة SQLite متسقة → رفعها للمستودع:
        latest/reports.db       (النسخة الحالية — يُستبدل دائمًا بالاسم نفسه)
        daily/YYYY-MM-DD/…      (لقطة مؤرَّخة واحدة لكل يوم)
    مع تصفير تاريخ المستودع تلقائيًا كل 30 يومًا (super_squash_history)
    حفاظًا على حدود المنصة من عدد الكوميتات.
  - `restore_latest_if_needed()`: عند الإقلاع، إذا كانت القاعدة المحلية
    فارغة (حاوية جديدة) تُجلب `latest/reports.db` وتُستعاد. **لا نستبدل
    أبدًا** قاعدة محلية تحتوي بيانات — الحماية من فقدان بلاغات جديدة.
  - `backup_loop()`            : مهمة دورية (حسب BACKUP_INTERVAL_MIN)
    مع رفعة أولى سريعة بعد الإقلاع بـ 20 ثانية، في أمكن التحقق فورًا.

كل شيء اختياري: بلا سرّ HF_TOKEN لا يتغيّر سلوك البوت إطلاقًا.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from archive import _snapshot as _sqlite_snapshot
from archive import _has_rows

logger = logging.getLogger(__name__)

_upload_lock = threading.Lock()
_repo_ready = False


# ==================================================================
# أدوات الحالة (آخر يوم مترفع / آخر تصفير تاريخ) — ملف JSON صغير
# ==================================================================
def _state_file() -> Path:
    import config  # noqa: PLC0415 — ضمن الدالة لتفادي الدوران المبكر

    return config.DATA_DIR / "hub_backup_state.json"


def _read_state() -> dict[str, str]:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict[str, str]) -> None:
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except Exception:
        logger.debug("تعذر حفظ ملف حالة النسخ الاحتياطي", exc_info=True)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_since(date_iso: str) -> float:
    try:
        past = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 1e9
    return (datetime.now(timezone.utc) - past).total_seconds() / 86400


def is_configured() -> bool:
    """هل النسخ إلى Hub مهيأ؟ (توكن + اسم مستودع)"""
    import config  # noqa: PLC0415

    return bool(config.HF_BACKUP_TOKEN and config.HF_BACKUP_REPO)


# ==================================================================
# الرفع: لقطة متسقة → مستودع Dataset خاص
# ==================================================================
def upload_backup(db_path: Path) -> bool:
    """ترفع لقطة متسقة من قاعدة البيانات إلى مستودع النسخ الخاص.

    تعيد True إذا نجح الرفع. لا تلمس العرض (fire-and-forget هو المسار الطبيعي).
    """
    import config  # noqa: PLC0415
    from huggingface_hub import CommitOperationAdd, HfApi  # noqa: PLC0415

    if not is_configured():
        return False
    db_path = Path(db_path)
    if not db_path.exists():
        return False

    global _repo_ready
    with _upload_lock:
        try:
            api = HfApi(token=config.HF_BACKUP_TOKEN)
            if not _repo_ready:
                api.create_repo(
                    repo_id=config.HF_BACKUP_REPO,
                    repo_type="dataset",
                    private=True,
                    exist_ok=True,
                )
                _repo_ready = True

            state = _read_state()
            today = _today_utc()
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            with tempfile.TemporaryDirectory() as tmpdir:
                snap = Path(tmpdir) / "reports.db"
                if not _sqlite_snapshot(db_path, snap):
                    return False
                operations = [
                    CommitOperationAdd(
                        path_in_repo="latest/reports.db",
                        path_or_fileobj=str(snap),
                    )
                ]
                commit_message = f"نسخة احتياطية تلقائية — {stamp}"
                # لقطة يومية مؤرخة: أول رفع في كل يوم حقيقي (بعد إقلاع جديد أيضًا)
                if state.get("last_daily") != today:
                    operations.append(
                        CommitOperationAdd(
                            path_in_repo=f"daily/{today}/reports.db",
                            path_or_fileobj=str(snap),
                        )
                    )
                    commit_message = f"نسخة احتياطية يومية — {stamp}"
                api.create_commit(
                    repo_id=config.HF_BACKUP_REPO,
                    repo_type="dataset",
                    operations=operations,
                    commit_message=commit_message,
                )
            _write_state({**state, "last_daily": today})

            # تصفير التاريخ كل 30 يومًا بعد رفع نسخة حديثة = لا نضير والحواف آمنة
            last_squash = state.get("last_squash")
            if not last_squash or _days_since(last_squash) >= 30:
                try:
                    api.super_squash_history(
                        repo_id=config.HF_BACKUP_REPO,
                        repo_type="dataset",
                        commit_message="تصفير تاريخ النسخ الاحتياطي (وفق حدود المنصة)",
                    )
                    _write_state({**state, "last_daily": today, "last_squash": today})
                    logger.info("تم تصفير تاريخ مستودع النسخ الاحتياطي")
                except Exception:
                    logger.debug("تعذر تصفير تاريخ المستودع — مؤجل للدورة القادمة")

            logger.info("📤 تم رفع نسخة احتياطية إلى مستودع النسخ (%s)", config.HF_BACKUP_REPO)
            return True
        except Exception:
            logger.warning("تعذّر رفع النسخة الاحتياطية إلى Hub", exc_info=True)
            return False


# ==================================================================
# الاستعادة عند الإقلاع: فقط إذا كانت القاعدة المحلية فارغة
# ==================================================================
def restore_latest_if_needed(db_path: Path) -> bool:
    """تجلب «latest/reports.db» من المستودع إذا كانت القاعدة المحلية فارغة.

    قواعد الاستعادة الصارمة:
      - لا تعمل بلا توكن/مستودع (غير مهيأة).
      - لا تسبق أي عملية: قاعدة محلية فيها بلاغات لا تُمسّ أبدًا.
      - الملف المستعاد يُفحص كقاعدة SQLite سليمة قبل الاستبدال.
    """
    import config  # noqa: PLC0415
    from huggingface_hub import hf_hub_download  # noqa: PLC0415

    if not is_configured():
        return False
    db_path = Path(db_path)

    if db_path.exists() and _has_rows(db_path):
        logger.info("قاعدة بيانات محلية تحتوي بيانات — لا استعادة من Hub")
        return False

    # إزالة بقايا WAL/SHM القديمة قبل الاستبدال
    for suffix in ("-wal", "-shm"):
        extra = Path(str(db_path) + suffix)
        if extra.exists():
            try:
                extra.unlink()
            except Exception:
                pass

    try:
        downloaded = Path(
            hf_hub_download(
                repo_id=config.HF_BACKUP_REPO,
                repo_type="dataset",
                filename="latest/reports.db",
                token=config.HF_BACKUP_TOKEN,
            )
        )
    except Exception as exc:
        name = type(exc).__name__
        if "EntryNotFound" in name or "not found" in str(exc).lower():
            logger.info("لا توجد نسخة سابقة في مستودع النسخ — بدء بقاعدة جديدة")
        else:
            logger.warning("تعذر جلب آخر نسخة من مستودع النسخ", exc_info=True)
        return False

    # فحس سلامة الملف قبل الاستبدال
    try:
        conn = sqlite3.connect(str(downloaded))
        try:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        finally:
            conn.close()
    except Exception:
        logger.warning("ملف النسخة ليس قاعدة SQLite سليمة — تجاهُل الاستعادة")
        return False

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(downloaded, db_path)
        for suffix in ("-wal", "-shm"):
            extra = Path(str(db_path) + suffix)
            if extra.exists():
                extra.unlink()
        logger.info("♻️ تمت استعادة قاعدة البيانات من مستودع النسخ الاحتياطي")
        return True
    except Exception:
        logger.exception("فشل نسخ النسخة المستعادة إلى مسار القاعدة")
        return False


# ==================================================================
# المهمة الدورية: رفعة أولى سريعة ثم لكل فترة
# ==================================================================
async def backup_loop(db_path: Path, interval_minutes: int) -> None:
    """يبقي النسخ الاحتياطي شغّالًا كل فترة (بيرفعة أولى بعد 20 ثانية من الإقلاع)."""
    if not is_configured() or interval_minutes <= 0:
        return
    logger.info("بدأ النسخ الاحتياطي الدوري إلى مستودع النسخ كل %d دقيقة", interval_minutes)
    first = True
    while True:
        await asyncio.sleep(20 if first else interval_minutes * 60)
        first = False
        try:
            await asyncio.to_thread(upload_backup, db_path)
        except Exception:
            logger.warning("فشل النسخ الدوري إلى المستودع", exc_info=True)
