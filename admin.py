"""
لوحة تحكم المشرف داخل البوت — آمنة بمعرف المشرف (Admin ID).

الأوامر: /admin أو /reports

الإمكانات:
  • استعراض البلاغات الجديدة / الكل (مع ترقيم صفحات).
  • عرض بلاغ مفرد مع صورته وتغيير حالته (جديد / قيد المراجعة / معتمد / مرفوض).
  • تصدير جميع البلاغات إلى Excel أو ملف نصي منظم.
  • إحصائيات سريعة.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler

import config
import exporters
import guards
import hub_backup
import texts
import utils
from database import (
    Database,
    STATUS_APPROVED,
    STATUS_CODES,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
)
from keyboards import (
    admin_item_buttons,
    admin_list_buttons,
    admin_menu_buttons,
    admin_view_buttons,
    filter_label,
)

logger = logging.getLogger(__name__)
PAGE_SIZE = 5  # عدد البلاغات في صفحة واحدة داخل القائمة

# آخر حالة أُرسل إشعار عنها لكل بلاغ (لتجنب تكرار الإشعار على نفس الحالة)
_notified_status: dict[int, str] = {}


# ==================================================================
# أوامر المشرف: الإيقاف الطارئ + سجل التدقيق
# ==================================================================
def _is_admin_update(update: Update) -> bool:
    """هل مرسل الأمر من المشرفين المعتمدين؟ (تستقبل كائن التحديث)"""
    user = update.effective_user
    return bool(user and _is_admin(user.id))


async def cmd_panic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/panic — إيقاف طارئ لاستقبال البلاغات (حماية من الإغراق أو الاختراق)."""
    if not _is_admin_update(update):
        await update.effective_message.reply_text(texts.ADMIN_ONLY, parse_mode=ParseMode.HTML)
        return

    db = context.bot_data["db"]
    await guards.set_emergency(db, True, update.effective_user.id, "أمر يدوي من المشرف")
    await update.effective_message.reply_text(
        texts.PANIC_ON, parse_mode=ParseMode.HTML
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/resume — إعادة تشغيل استقبال البلاغات بعد الإيقاف الطارئ."""
    if not _is_admin_update(update):
        await update.effective_message.reply_text(texts.ADMIN_ONLY, parse_mode=ParseMode.HTML)
        return

    db = context.bot_data["db"]
    await guards.set_emergency(db, False, update.effective_user.id, "أمر يدوي من المشرف")
    await update.effective_message.reply_text(
        texts.PANIC_OFF, parse_mode=ParseMode.HTML
    )


async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/audit — عرض آخر عمليات التدقيق (من فعل ماذا ومتى)."""
    if not _is_admin_update(update):
        await update.effective_message.reply_text(texts.ADMIN_ONLY, parse_mode=ParseMode.HTML)
        return

    db = context.bot_data["db"]
    rows = await db.recent_audit(limit=20)
    if not rows:
        await update.effective_message.reply_text(
            texts.AUDIT_EMPTY, parse_mode=ParseMode.HTML
        )
        return

    lines = ["🧾 <b>آخر عمليات التدقيق</b>", "━━━━━━━━━━━━━━━"]
    for row in rows:
        actor = row.get("actor_id") or "النظام"
        target = f" → {row['target']}" if row.get("target") else ""
        detail = f" — {utils.esc(row['details'])}" if row.get("details") else ""
        lines.append(
            f"• <code>{utils.fmt_local(row['ts'])}</code> · "
            f"<b>{utils.esc(row['action'])}</b> · {actor}{target}{detail}"
        )
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/backup — رفع نسخة فورية من قاعدة البيانات إلى المستودع السحابي."""
    if not _is_admin_update(update):
        await update.effective_message.reply_text(texts.ADMIN_ONLY, parse_mode=ParseMode.HTML)
        return

    if not hub_backup.is_configured():
        await update.effective_message.reply_text(
            texts.BACKUP_OFF, parse_mode=ParseMode.HTML
        )
        return

    try:
        await update.effective_message.chat.send_action("typing")
    except Exception:  # noqa: BLE001 — تفصيل تجميلي فقط
        pass

    ok = await asyncio.to_thread(hub_backup.upload_backup, config.DB_PATH)
    await update.effective_message.reply_text(
        texts.BACKUP_DONE if ok else texts.BACKUP_FAIL,
        parse_mode=ParseMode.HTML,
    )
    if ok:
        await db_log_backup(context, update.effective_user.id)


async def db_log_backup(context: ContextTypes.DEFAULT_TYPE, admin_id: int) -> None:
    """إدخال عملية النسخ في سجل التدقيق."""
    try:
        await context.bot_data["db"].log_event(admin_id, "backup_upload")
    except Exception:  # noqa: BLE001 — التدقيق لا يعطّل النسخ
        logger.debug("تعذر تسجيل عملية النسخ في سجل التدقيق", exc_info=True)


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def _stats(db: Database) -> dict[str, int]:
    counts = await db.count_by_status()
    return {
        "new": counts.get(STATUS_NEW, 0),
        "review": counts.get(STATUS_REVIEW, 0),
        "approved": counts.get(STATUS_APPROVED, 0),
        "rejected": counts.get(STATUS_REJECTED, 0),
        "total": await db.total(),
    }


# ==================================================================
# أمر الدخول للوحة: /admin أو /reports
# ==================================================================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_admin(user.id):
        await update.effective_message.reply_text(texts.ADMIN_DENIED, parse_mode=ParseMode.HTML)
        return
    logger.info("المشرف %s فتح لوحة الإدارة", user.id)
    s = await _stats(_db(context))
    await update.effective_message.reply_text(
        texts.ADMIN_MENU.format(**s),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_buttons(s["new"]),
    )


# ==================================================================
# تجهيز نص ولوحة أزرار قائمة البلاغات
# ==================================================================
async def _render_list(db: Database, code: str, page: int):
    status = STATUS_CODES.get(code)  # None تعني "الكل"
    rows, total = await db.list_reports(status, page, PAGE_SIZE)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)  # حماية من أزرار قديمة بعد تغير البيانات

    label = filter_label(code)
    if not rows:
        return texts.ADMIN_LIST_EMPTY.format(label=label), admin_list_buttons(code, page, pages)

    items = []
    for r in rows:
        items.append(
            f"▣ {texts.badge(r['status'])} — <b>{utils.esc(r['seq'])}</b> — "
            f"{utils.esc(r['full_name'] or '-')} — {utils.fmt_local(r['created_at'])}"
        )
    text = texts.ADMIN_LIST_HEADER.format(
        label=label, total=total, items="\n".join(items)
    )
    # ملاحظة: inline_keyboard في مكتبة تيليغرام أصبح tuple، لذا نحوّله إلى قائمة
    nav_rows = list(admin_list_buttons(code, page, pages).inline_keyboard)
    markup_rows = admin_item_buttons(rows) + nav_rows
    return text, InlineKeyboardMarkup(markup_rows)


async def _send_list(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database,
                     code: str, page: int) -> None:
    text, markup = await _render_list(db, code, page)
    context.chat_data["adm_list"] = (code, page)
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ==================================================================
# عرض بلاغ مفرد
# ==================================================================
def _report_caption(r: dict) -> str:
    review_line = ""
    if r.get("reviewed_by"):
        review_line = texts.REVIEW_LINE.format(
            admin=r["reviewed_by"], time=utils.fmt_local(r.get("reviewed_at"))
        )
    return texts.ADMIN_VIEW.format(
        seq=r["seq"],
        status=texts.badge(r["status"]),
        name=utils.esc(r["full_name"] or "-"),
        username=texts.username_display(r["username"]),
        uid=r["user_id"],
        time=utils.fmt_local(r["created_at"]),
        anchor=utils.link_anchor(r["post_link"]),
        desc=utils.esc(utils.trunc(r["description"], 600)),
        review_line=review_line,
    )


async def _show_report(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database,
                       rid: int) -> None:
    r = await db.get_by_id(rid)
    if not r:
        await update.effective_chat.send_message(
            texts.REPORT_NOT_FOUND, parse_mode=ParseMode.HTML
        )
        return

    caption = _report_caption(r)
    markup = admin_view_buttons(rid)
    photo = Path(r["photo_path"]) if r["photo_path"] else None
    file_id = r.get("photo_file_id")

    if photo is not None and photo.exists():
        context.chat_data["adm_view_text_only"] = False
        with open(photo, "rb") as fp:
            await update.effective_chat.send_photo(
                photo=fp, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup
            )
    elif file_id:
        # لا نسخة محلية على القرص (استضافة سحابية) — نعرض الصورة من تلغرام
        context.chat_data["adm_view_text_only"] = False
        try:
            await update.effective_chat.send_photo(
                photo=file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup
            )
        except Exception:
            context.chat_data["adm_view_text_only"] = True
            await update.effective_chat.send_message(
                caption, parse_mode=ParseMode.HTML, reply_markup=markup
            )
    else:
        context.chat_data["adm_view_text_only"] = True
        await update.effective_chat.send_message(
            caption, parse_mode=ParseMode.HTML, reply_markup=markup
        )


# ==================================================================
# إشعار المبلّغ بتغيّر حالة بلاغه
# ==================================================================
async def _notify_reporter(context: ContextTypes.DEFAULT_TYPE, rid: int,
                           new_status: str) -> None:
    """يخبر صاحب البلاغ بتغيّر حالته من لوحة المشرف.

    • الإشعار يُرسل مرة واحدة لكل حالة مختلفة (لا يُعاد لنفس الحالة).
    • إذا حجب المستخدم البوت أو حذف المحادثة، يفشل الإرسال بهدوء
      (لا يؤثر على تغيير الحالة ولا يصل الخطأ إلى معالج الأخطاء).
    """
    r = await _db(context).get_by_id(rid)
    if not r or not r.get("user_id"):
        return

    uid = int(r["user_id"])
    previous = _notified_status.get(rid)
    if previous == new_status:
        logger.debug("إشعار البلاغ %s مكرر لنفس الحالة (%s) — تخطٍ", rid, new_status)
        return

    CODE_TO_KEY = {
        STATUS_NEW: "new",
        STATUS_REVIEW: "review",
        STATUS_APPROVED: "approved",
        STATUS_REJECTED: "rejected",
    }
    key = CODE_TO_KEY.get(new_status)
    if key is None:
        logger.debug("حالة غير معروفة في الإشعار: %s", new_status)
        return

    reviewed_at = r.get("reviewed_at") or utils.utc_now_iso()
    text = texts.STATUS_UPDATE_NOTIF.format(
        seq=r["seq"],
        status=texts.badge(new_status),
        time=utils.fmt_local(reviewed_at),
        note=texts.STATUS_FINAL_LINES.get(key, ""),
    )
    try:
        await context.bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML)
    except Exception as exc:  # noqa: BLE001 — خطأ إرسال لا يوقف العمل
        logger.warning("لم يُرسل إشعار الحالة للمستخدم %s (ربما حجب البوت): %s", uid, exc)
        return
    # نجاح الإرسال فقط يُسجّل لتجنب إعادة الإشعار لنفس الحالة
    _notified_status[rid] = new_status
    logger.info("🔔 إشعار الحالة (%s) أُرسل للبلاغ %s", new_status, rid)


# ==================================================================
# تحديث شاشة العرض بعد تغيير الحالة
# ==================================================================
async def _refresh_view(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database,
                        rid: int) -> None:
    query = update.callback_query
    r = await db.get_by_id(rid)
    if not r:
        return
    caption = _report_caption(r)
    markup = admin_view_buttons(rid)
    try:
        if context.chat_data.get("adm_view_text_only"):
            await query.edit_message_text(
                caption, parse_mode=ParseMode.HTML, reply_markup=markup
            )
        else:
            await query.edit_message_caption(
                caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup
            )
    except Exception:
        # تعديل الرسالة القديمة قد يفشل (جمود رسالة/حدود تلغرام) — لا يؤثر على الحفظ
        logger.debug("تعذر تحديث شاشة عرض البلاغ %s", rid)


# ==================================================================
# التصدير
# ==================================================================
async def _export(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database,
                  fmt: str) -> None:
    query = update.callback_query
    rows = await db.all_reports()
    if not rows:
        await update.effective_chat.send_message(texts.EXPORT_EMPTY, parse_mode=ParseMode.HTML)
        return

    if fmt == "xlsx":
        path = await asyncio.to_thread(exporters.export_excel, rows)
    else:
        path = await asyncio.to_thread(exporters.export_text, rows)

    with open(path, "rb") as fp:
        await update.effective_chat.send_document(
            document=fp,
            filename=path.name,
            caption=texts.EXPORT_DONE.format(name=path.name, count=len(rows)),
            parse_mode=ParseMode.HTML,
        )

    logger.info("المشرف %s صدّر %s بلاغًا إلى %s",
                update.effective_user.id, len(rows), Path(path).name)


# ==================================================================
# موزّع الضغطات على أزرار اللوحة
# ==================================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db = _db(context)

    # حماية: أزرار اللوحة للمشرفين فقط
    if not _is_admin(update.effective_user.id):
        await query.answer(texts.ADMIN_DENIED, show_alert=True)
        return

    data = query.data or ""
    if data == "adm:noop":
        await query.answer()
        return
    await query.answer()

    parts = data.split(":")  # adm:<operation>:<...>
    op = parts[1] if len(parts) > 1 else ""

    if op == "menu":
        s = await _stats(db)
        await query.edit_message_text(
            texts.ADMIN_MENU.format(**s),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_buttons(s["new"]),
        )

    elif op == "list":
        code = parts[2]
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, markup = await _render_list(db, code, page)
        context.chat_data["adm_list"] = (code, page)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

    elif op == "back":
        code, page = context.chat_data.get("adm_list", ("all", 0))
        await _send_list(update, context, db, code, page)

    elif op == "v":
        if len(parts) < 3 or not parts[2].isdigit():
            return
        await _show_report(update, context, db, int(parts[2]))

    elif op == "s":
        if len(parts) < 4 or not parts[2].isdigit() or parts[3] not in STATUS_CODES:
            await query.answer(texts.RULE_NOT_KNOWN, show_alert=True)
            return
        rid, code = int(parts[2]), parts[3]
        new_status = STATUS_CODES[code]
        ok = await db.update_status(rid, new_status, update.effective_user.id)
        if not ok:
            await query.answer(texts.REPORT_NOT_FOUND, show_alert=True)
            return
        logger.info("المشرف %s غيّر حالة البلاغ %s إلى %s",
                    update.effective_user.id, rid, new_status)
        await _notify_reporter(context, rid, new_status)
        await query.answer("✅ تم تحديث الحالة", show_alert=False)
        await _refresh_view(update, context, db, rid)

    elif op == "stats":
        s = await _stats(db)
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        s["last24"] = await db.count_since(since)
        await query.edit_message_text(
            texts.ADMIN_STATS.format(**s), parse_mode=ParseMode.HTML
        )

    elif op == "exp":
        if len(parts) < 3:
            return
        await _export(update, context, db, parts[2])

    else:
        logger.warning("زر لوحة غير معروف: %s", data)
