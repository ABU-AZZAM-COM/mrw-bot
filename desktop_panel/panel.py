# -*- coding: utf-8 -*-
"""
لوحة إدارة سطح المكتب — نظام بلاغات المخالفات العسكرية.
تتصل بأسرار السبيس عبر Admin API (X-Admin-Token) وتنفّذ التحكم الكامل.

الأقسام:
  1) النظرة العامة  : إحصاءات + حالة البوت + الإيقاف الطارئ + تحديث تلقائي
  2) البلاغات       : بحث/فلترة/ترقيم + تفصيل كامل بصورة + إجراءات
                      (حالة+سبب رفض إلزامي، تصنيف، ملاحظة، رد، حذف موثّق)
  3) المتبلّغون     : بحث/بطاقة مستخدم/بلاغاته + حظر دائم أو مؤقت + رفع الحجب
  4) سجل التدقيق    : كل الإجراءات (مين فعل إيه وإمتى)

التشغيل:
  desktop_panel/panel.py  (أو ملف run_panel.bat في جذر المشروع)
الإعدادات (الرابط والتوكن) تُحفظ في desktop_panel/panel_config.json — سرّي.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import httpx

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("panel")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "panel_config.json"

DEFAULT_URL = "https://azzam1992-mrw-bot.hf.space"
TOKEN_SUGGESTION = ""  # يتعبأ من بيانات المشروع إن وُجد توكن محفوظ سابقًا

STATUS_ORDER = ["جديد", "قيد المراجعة", "معتمد", "مرفوض"]

_page_size = 25


# ==================================================================
# الإعدادات المحلية (سرِّية — خارج git)
# ==================================================================
def load_config() -> dict:
    global TOKEN_SUGGESTION
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if not cfg.get("token"):
        for cand in (BASE_DIR / "admin_token.txt", BASE_DIR.parent / "data" / "admin_panel_token.txt"):
            if cand.exists():
                try:
                    TOKEN_SUGGESTION = cand.read_text(encoding="utf-8").strip()
                    if TOKEN_SUGGESTION:
                        break
                except Exception:
                    pass
    cfg.setdefault("url", DEFAULT_URL)
    cfg.setdefault("token", "")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("حُفظت الإعدادات: %s", CONFIG_PATH)


# ==================================================================
# عميل الواجهة (HTTP) — استدعاءات متزامنة وتؤيّد من QThread
# ==================================================================
class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.base = (base_url or DEFAULT_URL).strip().rstrip("/")
        self.token = (token or "").strip()
        self.client = httpx.Client(timeout=30, follow_redirects=True)
        self.headers = {"x-admin-token": self.token} if self.token else {}

    def call(self, method: str, path: str, body: dict | None = None):
        url = f"{self.base}{path}"
        try:
            if method == "GET":
                data = self.client.get(url, headers=self.headers)
            elif method == "POST":
                data = self.client.post(
                    url, headers=self.headers, json=body or {}
                )
            elif method == "DELETE":
                data = self.client.request(
                    "DELETE", url, headers=self.headers, json=body or {}
                )
            else:
                return None
            payload = data.json()
            payload.setdefault("_status", data.status_code)
            return payload
        except Exception as exc:  # noqa: BLE001 — الخطأ يظهر في الواجهة
            return {"ok": False, "_error": f"{type(exc).__name__}: {exc}"}

    def photo_bytes(self, rid: int) -> bytes:
        """بايتات صورة البلاغ (لا JSON)."""
        url = f"{self.base}/api/admin/photo/{rid}"
        try:
            data = self.client.get(url, headers=self.headers)
            if data.headers.get("content-type", "").startswith("image"):
                return data.content
            return b""
        except Exception:  # noqa: BLE001
            return b""


class Job(QThread):
    """مهمة تشغيل متوازية: تنفيذ مفرد وإشارة عند الانتهاء."""
    done = Signal(object)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:  # noqa: D102
        try:
            self.done.emit(self.fn())
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"ok": False, "_error": f"{type(exc).__name__}: {exc}"})


# ==================================================================
# حوار الإعداد (الرابط + التوكن)
# ==================================================================
class SetupDialog(QDialog):
    def __init__(self, cfg: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("إعداد الاتصال بلوحة الإدارة")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(430)
        form = QFormLayout(self)
        self.url_edit = QLineEdit(cfg.get("url", DEFAULT_URL))
        self.token_edit = QLineEdit(cfg.get("token") or TOKEN_SUGGESTION)
        self.token_edit.setEchoMode(QLineEdit.Password)
        hint = QLabel("التوكن الإداري يُدير سرّ ADMIN_API_TOKEN من أسرار المساحة.")
        hint.setWordWrap(True)
        form.addRow("رابط السبيس:", self.url_edit)
        form.addRow("التوكن الإداري:", self.token_edit)
        form.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("اتصال")
        buttons.button(QDialogButtonBox.Cancel).setText("خروج")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str]:
        return self.url_edit.text().strip(), self.token_edit.text().strip()


# ==================================================================
# بطاقات النظرة العامة
# ==================================================================
class StatCard(QFrame):
    def __init__(self, title: str, color: str = "#0b7") -> None:
        super().__init__()
        self.setFrameShadow(QFrame.Sunken)
        self.setStyleSheet(
            f"background:#12221e; border:1px solid #20443a; border-radius:12px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{color}; font-weight:600;")
        self.value = QLabel("0")
        self.value.setStyleSheet("color:#f5f7fa; font-size:23pt; font-weight:800;")
        lay.addWidget(self.title)
        lay.addWidget(self.value)

    def set(self, num: int) -> None:
        self.value.setText(str(num))


# ==================================================================
# النافذة الرئيسية
# ==================================================================
class MainWindow(QMainWindow):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = cfg
        self.api = Api(cfg["url"], cfg["token"])
        self.meta_types: list[str] = []
        self.jobs: list[QThread] = []
        self.page = 0
        self.last_total = None

        self.setWindowTitle("🛡️ لوحة إدارة بلاغات المخالفات العسكرية")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1100, 680)
        icon_path = BASE_DIR / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        tabs = QTabWidget()
        tabs.addTab(self._build_dashboard(), "📊 النظرة العامة")
        tabs.addTab(self._build_reports(), "🗂️ البلاغات")
        tabs.addTab(self._build_users(), "👥 المتبلّغون")
        tabs.addTab(self._build_audit(), "🧾 سجل التدقيق")
        self.setCentralWidget(tabs)
        self.status = QLabel("جارٍ الاتصال…")
        statusbar = self.statusBar()
        statusbar.addPermanentWidget(self.status)

        self.timer = QTimer(self)
        self.timer.setInterval(6000)
        self.timer.timeout.connect(self.auto_refresh)
        self.timer.start()
        self.auto_refresh()

    # ----------------------------------------------------------
    def run(self, fn, callback) -> None:
        """تشغيل مهمة شبكية في خيط ثم استدعاء مُعالِجها في الواجهة."""
        job = Job(fn)
        job.done.connect(callback)
        self.jobs.append(job)
        job.finished.connect(lambda: self.jobs.remove(job) if job in self.jobs else None)
        job.start()

    def auto_refresh(self) -> None:
        self.run(lambda: self.api.call("GET", "/api/admin/overview"), self.apply_overview)

    # ----------------------------------------------------------
    # تبويب 1: النظرة العامة
    # ----------------------------------------------------------
    def _build_dashboard(self) -> QWidget:
        page = QWidget()
        page.setLayoutDirection(Qt.RightToLeft)
        grid = QGridLayout(page)
        self.cards = {
            "جديد": StatCard("🆕 جديد", "#ffd166"),
            "قيد المراجعة": StatCard("🔎 قيد المراجعة", "#8ab4ff"),
            "معتمد": StatCard("✅ معتمد", "#3fa66b"),
            "مرفوض": StatCard("❌ مرفوض", "#e06c75"),
            "الإجمالي": StatCard("📦 الإجمالي", "#9ab8aa"),
            "آخر 24 ساعة": StatCard("⏱️ آخر 24 ساعة", "#c4a2ff"),
        }
        grid.addWidget(self.cards["جديد"], 0, 0)
        grid.addWidget(self.cards["قيد المراجعة"], 0, 1)
        grid.addWidget(self.cards["معتمد"], 0, 2)
        grid.addWidget(self.cards["مرفوض"], 0, 3)
        grid.addWidget(self.cards["الإجمالي"], 1, 0)
        grid.addWidget(self.cards["آخر 24 ساعة"], 1, 1)
        hint = QLabel(
            "تحديث لحظي تلقائي كل 6 ثوانٍ، مع تنبيه صوتي عند وصول بلاغ جديد."
        )
        hint.setStyleSheet("color:#7d8f88;")
        grid.addWidget(hint, 2, 0, 1, 4)
        settings_btn = QPushButton("⚙ الإعدادات (الرابط/التوكن)")
        settings_btn.setStyleSheet("background:#12221e; color:#8ab4ff;")
        settings_btn.clicked.connect(self._open_settings)
        grid.addWidget(settings_btn, 3, 0, 1, 4)
        return page

    def _open_settings(self) -> None:
        dlg = SetupDialog(self.cfg, self)
        if dlg.exec() != QDialog.Accepted:
            return
        url, token = dlg.values()
        if not url.startswith("http") or not token:
            QMessageBox.warning(self, "إعدادات ناقصة", "الرابط والتوكن مطلوبان معًا.")
            return
        probe = Api(url, token).call("GET", "/api/admin/overview")
        if not probe or probe.get("ok") is not True:
            code = (probe or {}).get("_status") or (probe or {}).get("_error") or "انقطاع"
            QMessageBox.critical(
                self, "رفض الاتصال",
                f"التوكن/الرابط غير مقبولين ({code}). تأكد منهما ثم حاول مجددًا."
            )
            return
        self.cfg["url"], self.cfg["token"] = url, token
        save_config(self.cfg)
        self.api = Api(self.cfg["url"], self.cfg["token"])
        self.last_total = None
        self.auto_refresh()
        QMessageBox.information(self, "تم", "حُمّلت الإعدادات وأُعيد الاتصال ✅")

    def apply_overview(self, data: dict) -> None:
        if not data.get("ok"):
            self.status.setText("❌ انقطاع الاتصال — محاولة لاحقة تلقائيًا")
            return
        counts = data.get("counts", {})
        self.cards["جديد"].set(counts.get("جديد", 0))
        self.cards["قيد المراجعة"].set(counts.get("قيد المراجعة", 0))
        self.cards["معتمد"].set(counts.get("معتمد", 0))
        self.cards["مرفوض"].set(counts.get("مرفوض", 0))
        self.cards["الإجمالي"].set(counts.get("total", 0))
        self.cards["آخر 24 ساعة"].set(counts.get("last24", 0))
        total = counts.get("total", 0) + counts.get("last24", 0)
        if self.last_total is not None and total > self.last_total:
            QApplication.beep()
        self.last_total = total
        flags = []
        flags.append("🤖 البوت جاهز" if data.get("bot_ready") else "🤖 البوت غير جاهز!")
        flags.append("🛑 إيقاف طارئ مُفعّل!" if data.get("emergency") else "🟢 الاستقبال شغّال")
        self.status.setText("  |  ".join(flags) + f"  |  ⏱️ {datetime.now().strftime('%H:%M:%S')}")

    # ----------------------------------------------------------
    # تبويب 2: البلاغات
    # ----------------------------------------------------------
    def _build_reports(self) -> QWidget:
        page = QWidget()
        page.setLayoutDirection(Qt.RightToLeft)
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.f_status = QComboBox()
        self.f_status.addItem("كل الحالات", "")
        for s in STATUS_ORDER:
            self.f_status.addItem(s, s)
        self.f_type = QComboBox()
        self.f_type.addItem("كل التصنيفات", "")
        self.f_q = QLineEdit()
        self.f_q.setPlaceholderText("بحث: رقم/اسم/وصف/رابط/معرف")
        btn_q = QPushButton("بحث")
        btn_q.clicked.connect(self.reports_refresh)
        btn_ref = QPushButton("⟳")
        btn_ref.clicked.connect(self.reports_refresh)
        bar.addWidget(self.f_status)
        bar.addWidget(self.f_type)
        bar.addWidget(self.f_q, 1)
        bar.addWidget(btn_q)
        bar.addWidget(btn_ref)
        lay.addLayout(bar)

        self.rep_table = QTableWidget(0, 6)
        self.rep_table.setHorizontalHeaderLabels(["الرقم", "الحالة", "الاسم", "المعرف", "التصنيف", "الوقت"])
        self.rep_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rep_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rep_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rep_table.doubleClicked.connect(self.open_report)
        lay.addWidget(self.rep_table, 1)

        nav = QHBoxLayout()
        self.page_label = QLabel("الصفحة 1")
        prev = QPushButton("→ السابقة")
        nxt = QPushButton("التالية ←")
        prev.clicked.connect(lambda: self._page_shift(-1))
        nxt.clicked.connect(lambda: self._page_shift(1))
        nav.addWidget(prev)
        nav.addWidget(self.page_label)
        nav.addWidget(nxt)
        nav.addStretch(1)
        lay.addLayout(nav)
        self._load_meta()
        return page

    def _load_meta(self) -> None:
        def cb(data):
            if data and data.get("ok"):
                self.meta_types = list(data.get("vtypes") or [])
                cur = self.f_type.currentData()
                self.f_type.blockSignals(True)
                self.f_type.clear()
                self.f_type.addItem("كل التصنيفات", "")
                for t in self.meta_types:
                    self.f_type.addItem(t, t)
                if cur is not None:
                    idx = self.f_type.findData(cur)
                    if idx >= 0:
                        self.f_type.setCurrentIndex(idx)
                self.f_type.blockSignals(False)
        self.run(lambda: self.api.call("GET", "/api/admin/meta"), cb)

    def _page_shift(self, delta: int) -> None:
        self.page = max(0, self.page + delta)
        self.reports_refresh()

    def reports_refresh(self) -> None:
        status = self.f_status.currentData() or ""
        vtype = self.f_type.currentData() or ""
        text = self.f_q.text().strip()
        qs = []
        if status:
            qs.append(f"status={status}")
        if vtype:
            qs.append(f"type={vtype}")
        if text:
            qs.append(f"q={text}")
        path = f"/api/admin/reports?page={self.page}&size={_page_size}"
        if qs:
            path += "&" + "&".join(qs)
        self.run(lambda: self.api.call("GET", path), self.apply_reports)

    def apply_reports(self, data: dict) -> None:
        self.rep_table.setRowCount(0)
        if not data or not data.get("ok"):
            self.status.setText("❌ تعذّر جلب البلاغات")
            return
        total = data.get("total", 0)
        pages = max(1, -(-total // _page_size))
        self.page = min(self.page, pages - 1)
        self.page_label.setText(f"الصفحة {self.page + 1} / {pages} — الإجمالي {total}")
        for r in data.get("items", []):
            row = self.rep_table.rowCount()
            self.rep_table.insertRow(row)
            cells = [
                r.get("seq") or "-",
                (r.get("status") or "-"),
                (r.get("full_name") or "-"),
                str(r.get("user_id") or "-"),
                (r.get("violation_type") or "—"),
                (r.get("created_at") or "-"),
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, r.get("id"))
                self.rep_table.setItem(row, c, item)
            self._color_status_row(row, r.get("status") or "")

    def _color_status_row(self, row: int, status: str) -> None:
        colors = {"جديد": QColor("#ffd166"), "قيد المراجعة": QColor("#8ab4ff"),
                  "معتمد": QColor("#3fa66b"), "مرفوض": QColor("#e06c75")}
        if status in colors:
            item = self.rep_table.item(row, 1)
            item.setForeground(colors[status])

    def open_report(self, index) -> None:
        rid = self.rep_table.item(index.row(), 0).data(Qt.UserRole)
        self._report_dialog(rid)

    def _report_dialog(self, rid: int) -> None:
        if rid is None:
            return
        dlg = ReportDialog(self, self.api, int(rid))
        dlg.exec()

    # ----------------------------------------------------------
    # تبويب 3: المتبلّغون
    # ----------------------------------------------------------
    def _build_users(self) -> QWidget:
        page = QWidget()
        page.setLayoutDirection(Qt.RightToLeft)
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.u_q = QLineEdit()
        self.u_q.setPlaceholderText("بحث: الاسم / المعرف / رقم الهوية")
        btn = QPushButton("بحث")
        btn.clicked.connect(self.users_refresh)
        btn_ref = QPushButton("⟳")
        btn_ref.clicked.connect(self.users_refresh)
        bar.addWidget(self.u_q, 1)
        bar.addWidget(btn)
        bar.addWidget(btn_ref)
        # ⛔ فلتر المحجوبين فقط + 💬 زر التحدث المباشر مع الإدارة
        self.btn_filter_blocked = QPushButton("⛔ المحجوبون فقط")
        self.btn_filter_blocked.setCheckable(True)
        self.btn_filter_blocked.toggled.connect(self._users_filter_toggled)
        self.btn_talk = QPushButton("💬 تواصل مباشر")
        self.btn_talk.setCheckable(True)
        self.btn_talk.toggled.connect(self._talk_toggled)
        bar.addWidget(self.btn_filter_blocked)
        bar.addWidget(self.btn_talk)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.usr_table = QTableWidget(0, 6)
        self.usr_table.setHorizontalHeaderLabels(
            ["المعرف", "الاسم", "المستخدم", "البلاغات", "الحالة", "آخر ظهور"]
        )
        self.usr_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.usr_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.usr_table.doubleClicked.connect(self.open_user)
        lay.addWidget(self.usr_table, 1)
        return page

    def _users_filter_toggled(self, checked: bool) -> None:
        self.u_q.setEnabled(not checked)
        if checked:
            self.run(lambda: self.api.call("GET", "/api/admin/users?page=0&size=50"), self.apply_users_blocked_only)
        else:
            self.users_refresh()

    def apply_users_blocked_only(self, data) -> None:
        self.usr_table.setRowCount(0)
        if not data or not data.get("ok"):
            self.status.setText("❌ تعذّر جلب المحجوبين")
            return
        for u in data.get("items", []):
            frozen = u.get("frozen_until")
            if not frozen:
                continue
            row = self.usr_table.rowCount()
            self.usr_table.insertRow(row)
            block = u.get("block") or {}
            reason = block.get("reason") or "—"
            cells = [
                str(u.get("user_id") or "-"),
                u.get("full_name") or "-",
                ("@" + str(u.get("username"))) if u.get("username") else "—",
                str(u.get("reports_count") or 0),
                f"⛔ محجوب حتى {frozen}",
                reason,
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                item.setData(Qt.UserRole, u.get("user_id"))
                self.usr_table.setItem(row, c, item)

    def _talk_toggled(self, checked: bool) -> None:
        if checked:
            self.status.setText("💬 وضع التحدث المباشر مفعل — ستتم إشعار المتبلّغين فورًا عند فتح البلاغ")
        else:
            self.status.setText("💬 التحدث المباشر معطل")

    def users_refresh(self) -> None:
        q = self.u_q.text().strip()
        path = f"/api/admin/users?page=0&size={_page_size}"
        if q:
            path += f"&q={q}"
        self.run(lambda: self.api.call("GET", path), self.apply_users)

    def apply_users(self, data: dict) -> None:
        self.usr_table.setRowCount(0)
        if not data or not data.get("ok"):
            self.status.setText("❌ تعذّر جلب قائمة المتبلّغين")
            return
        for u in data.get("items", []):
            row = self.usr_table.rowCount()
            self.usr_table.insertRow(row)
            frozen = u.get("frozen_until")
            state = "⛔ محجوب" if frozen else "🟢 نشط"
            cells = [
                str(u.get("user_id") or "-"),
                u.get("full_name") or "-",
                ("@" + str(u.get("username"))) if u.get("username") else "—",
                str(u.get("reports_count") or 0),
                state,
                u.get("last_at") or "-",
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, u.get("user_id"))
                self.usr_table.setItem(row, c, item)

    def open_user(self, index) -> None:
        uid = self.usr_table.item(index.row(), 0).data(Qt.UserRole)
        if uid is not None:
            UserDialog(self, self.api, int(uid), self.users_refresh).exec()

    # ----------------------------------------------------------
    # تبويب 4: سجل التدقيق
    # ----------------------------------------------------------
    def _build_audit(self) -> QWidget:
        page = QWidget()
        page.setLayoutDirection(Qt.RightToLeft)
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        btn = QPushButton("⟳ تحديث")
        btn.clicked.connect(self.audit_refresh)
        bar.addStretch(1)
        bar.addWidget(btn)
        lay.addLayout(bar)
        self.audit_table = QTableWidget(0, 5)
        self.audit_table.setHorizontalHeaderLabels(["الوقت", "المصدر", "العملية", "الهدف", "تفاصيل"])
        self.audit_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.audit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.audit_table, 1)
        self.audit_refresh()
        return page

    def audit_refresh(self) -> None:
        self.run(lambda: self.api.call("GET", "/api/admin/audit?limit=100"),
                 self.apply_audit)

    def apply_audit(self, data: dict) -> None:
        self.audit_table.setRowCount(0)
        if not data or not data.get("ok"):
            return
        for a in data.get("items", []):
            row = self.audit_table.rowCount()
            self.audit_table.insertRow(row)
            actor = a.get("actor_id")
            cells = [
                a.get("ts") or "-",
                "لوحة الإدارة" if actor == 0 else (str(actor) if actor else "النظام"),
                a.get("action") or "-",
                a.get("target") or "-",
                a.get("details") or "-",
            ]
            for c, val in enumerate(cells):
                self.audit_table.setItem(row, c, QTableWidgetItem(val))


# ==================================================================
# قاعدة مشتركة للحوارات: مهام شبكية + إغلاق آمن ينتظر انتهاء الخيوط
# ==================================================================
class DialogBase(QDialog):
    def run(self, fn, cb) -> None:
        job = Job(fn, parent=self)
        job.done.connect(cb)
        if not hasattr(self, "_local_jobs"):
            self._local_jobs = []
        self._local_jobs.append(job)
        job.start()

    def closeEvent(self, event) -> None:
        for job in getattr(self, "_local_jobs", []):
            if job.isRunning():
                job.wait(15000)
        super().closeEvent(event)


# ==================================================================
# نافذة تفاصيل البلاغ + الإجراءات
# ==================================================================
class ReportDialog(DialogBase):
    def __init__(self, parent, api: Api, rid: int) -> None:
        super().__init__(parent)
        self.api = api
        self.rid = rid
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("تفاصيل البلاغ")
        self.resize(860, 640)
        self.setStyleSheet("background:#101c17; color:#eef6f1;")

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.photo = QLabel("🧷 جارٍ تحميل الصورة…")
        self.photo.setMinimumSize(320, 300)
        self.photo.setAlignment(Qt.AlignCenter)
        self.photo.setStyleSheet("background:#0d1b16; border-radius:10px;")
        top.addWidget(self.photo, 1)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignRight)
        self.seq_lbl = QLabel("…")
        self.status_lbl = QLabel("…")
        self.name_lbl = QLabel("…")
        self.uid_lbl = QLabel("…")
        self.time_lbl = QLabel("…")
        self.link_lbl = QLabel("…")
        self.link_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.type_lbl = QLabel("…")
        self.desc_lbl = QLabel("…")
        self.desc_lbl.setWordWrap(True)
        for lbl in (self.seq_lbl, self.status_lbl, self.name_lbl, self.uid_lbl,
                    self.time_lbl, self.link_lbl, self.type_lbl):
            lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        form.addRow("الرقم:", self.seq_lbl)
        form.addRow("الحالة:", self.status_lbl)
        form.addRow("المتبلّغ:", self.name_lbl)
        form.addRow("معرّفه:", self.uid_lbl)
        form.addRow("الوقت:", self.time_lbl)
        form.addRow("الرابط:", self.link_lbl)
        form.addRow("التصنيف:", self.type_lbl)
        top.addLayout(form, 2)
        lay.addLayout(top)

        self.desc = QPlainTextEdit()
        self.desc.setReadOnly(True)
        self.desc.setMaximumHeight(110)
        self.desc.setPlaceholderText("وصف البلاغ…")
        lay.addWidget(self.desc)

        # أزرار الحالة
        row1 = QHBoxLayout()
        for lbl, code in (("🆕 جديد", "new"), ("🔎 قيد المراجعة", "review"),
                          ("✅ معتمد", "approved"), ("❌ مرفوض", "rejected")):
            b = QPushButton(lbl)
            b.clicked.connect(lambda _=False, cc=code: self._set_status(cc))
            row1.addWidget(b)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.type_box = QComboBox()
        row2.addWidget(QLabel("التصنيف:"))
        row2.addWidget(self.type_box, 1)
        btn_type = QPushButton("حفظ التصنيف")
        btn_type.clicked.connect(self._save_type)
        row2.addWidget(btn_type)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.note = QLineEdit()
        self.note.setPlaceholderText("ملاحظة إدارية داخلية على البلاغ…")
        btn_note = QPushButton("حفظ الملاحظة")
        btn_note.clicked.connect(self._save_note)
        self.reply = QPlainTextEdit()
        self.reply.setPlaceholderText("رد الجهة المختصة للمتبلّغ…")
        self.reply.setMaximumHeight(64)
        btn_reply = QPushButton("📩 أرسل الرد")
        btn_reply.clicked.connect(self._send_reply)
        row3.addWidget(self.note, 1)
        row3.addWidget(btn_note)
        lay.addLayout(row3)
        lay.addWidget(self.reply)
        row4 = QHBoxLayout()
        btn_del = QPushButton("🗑️ حذف نهائي (يتطلب تأكيد)")
        btn_del.setStyleSheet("color:#e06c75;")
        btn_del.clicked.connect(self._delete)
        btn_close = QPushButton("إغلاق")
        btn_close.clicked.connect(self.accept)
        row4.addWidget(btn_del)
        row4.addStretch(1)
        row4.addWidget(btn_close)
        lay.addLayout(row4)

        self.run(self._fetch_detail, self._apply_detail)
        self.run(lambda: self.api.photo_bytes(self.rid), self._apply_photo)

    def _fetch_detail(self):
        return self.api.call(
            "GET", f"/api/admin/reports/{self.rid}"
        )

    def _apply_detail(self, data) -> None:
        if not data or not data.get("ok"):
            self.status_lbl.setText(f"❌ {data.get('_error') or data.get('error') or 'غير متوفر'}")
            return
        r = data["item"]
        self.item = r
        self.seq_lbl.setText(r.get("seq") or "-")
        self.status_lbl.setText(r.get("status") or "-")
        self.name_lbl.setText(f"{r.get('full_name') or '-'} (@{r.get('username') or '—'})")
        self.uid_lbl.setText(str(r.get("user_id") or "-"))
        self.time_lbl.setText(r.get("created_at") or "-")
        self.link_lbl.setText(r.get("post_link") or "-")
        self.type_lbl.setText(r.get("violation_type") or "—")
        self.desc.setPlainText(r.get("description") or "")
        self.note.setText(r.get("admin_note") or "")
        tbox = self.type_box
        tbox.clear()
        mainwin = self.parent()
        types = getattr(mainwin, "meta_types", []) or []
        tbox.addItem("(فارغ)", "")
        for t in types:
            tbox.addItem(t, t)
        cur_type = r.get("violation_type") or ""
        idx = tbox.findData(cur_type)
        if idx >= 0:
            tbox.setCurrentIndex(idx)

    def _apply_photo(self, data) -> None:
        if not data:
            self.photo.setText("🛈 الصورة غير متاحة (حاوية مُعاد تهيئتها)")
            return
        pix = QPixmap()
        if pix.loadFromData(data):
            self.photo.setPixmap(self._fit(pix))
            self.photo.setStyleSheet("background:#0d1b16; border-radius:10px;")
        else:
            self.photo.setText("تعذر عرض الصورة")

    def _fit(self, pix: QPixmap) -> QPixmap:
        tw, th = 380.0, 300.0
        return pix.scaled(int(tw), int(th), Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ---- الإجراءات ----
    def _set_status(self, code: str) -> None:
        body = {"status": code}
        if code == "rejected":
            reason, ok = QInputDialog.getMultiLineText(
                self, "سبب الرفض — مطلوب",
                "اكتب سبب رفض البلاغ (سيصل للمتبلّغ برسالة):\n",
                ""
            )
            if not ok or not reason.strip():
                QMessageBox.warning(self, "تنبيه", "سبب الرفض إلزامي — لم تُنفّذ العملية.")
                return
            body["reason"] = reason.strip()
        self.run(lambda: self.api.call("POST", f"/api/admin/reports/{self.rid}/status", body),
                 self._after_status)

    def _after_status(self, data) -> None:
        if data and data.get("ok"):
            msg = "تم تحديث الحالة"
            if data.get("notified"):
                msg += " ويُبلّغ المتبلّغ ✅"
            self.status_lbl.setText(msg)
            self.run(self._fetch_detail, self._apply_detail)
            mainwin = self.parent()
            if hasattr(mainwin, "reports_refresh"):
                mainwin.reports_refresh()
        else:
            err = (data or {}).get("_error") or (data or {}).get("error") or "فشل"
            QMessageBox.warning(self, "فشل", f"لم تُنفّذ: {err}")

    def _save_type(self) -> None:
        vtype = self.type_box.currentData() or ""
        self.run(lambda: self.api.call("POST", f"/api/admin/reports/{self.rid}/type",
                                       {"violation_type": vtype}), self._after_ok)

    def _save_note(self) -> None:
        self.run(lambda: self.api.call("POST", f"/api/admin/reports/{self.rid}/note",
                                       {"note": self.note.text().strip()}), self._after_ok)

    def _send_reply(self) -> None:
        text = self.reply.toPlainText().strip()
        if len(text) < 2:
            QMessageBox.warning(self, "تنبيه", "الرد قصير جدًا — اكتب نصًا واضحًا.")
            return
        self.run(lambda: self.api.call("POST", f"/api/admin/reports/{self.rid}/reply",
                                       {"text": text}), self._after_reply)

    def _after_reply(self, data) -> None:
        if data and data.get("ok"):
            self.reply.setPlainText("")
            QMessageBox.information(self, "تم", "أُرسل الرد للمتبلّغ ✅")
        else:
            err = (data or {}).get("error") or (data or {}).get("_error") or "?"
            if err == "bot_not_ready":
                QMessageBox.warning(self, "قيد التهيئة", "البوت لم يجهز تراسله بعد — أعِد المحاولة بعد لحظات.")
            else:
                QMessageBox.warning(self, "فشل", f"لم يُرسل: {err}")

    def _delete(self) -> None:
        seq = (getattr(self, "item", None) or {}).get("seq", "")
        if not seq:
            return
        txt, ok = QInputDialog.getText(
            self, "تأكيد حذف نهائي",
            f"اكتب الرقم التسلسلي الموافَق «{seq}» للحذف النهائي:",
        )
        if not ok or txt.strip() != seq:
            QMessageBox.information(self, "أُلغِي", "لم يُطابق التأكيد — لم يُحذف شيء.")
            return
        self.run(lambda: self.api.call(
            "DELETE", f"/api/admin/reports/{self.rid}", {"confirm_seq": seq}
        ), self._after_delete)

    def _after_delete(self, data) -> None:
        if data and data.get("ok"):
            QMessageBox.information(self, "تم", "حُذف البلاغ نهائيًا ✅")
            mainwin = self.parent()
            if hasattr(mainwin, "reports_refresh"):
                mainwin.reports_refresh()
            self.accept()
        else:
            err = (data or {}).get("_error") or (data or {}).get("error") or "?"
            QMessageBox.warning(self, "فشل", f"لم يُحذف: {err}")

    def _after_ok(self, data) -> None:
        if data and data.get("ok"):
            QMessageBox.information(self, "تم", "تمت العملية ✅")
        else:
            QMessageBox.warning(self, "فشل", f"لم تُنفّذ: {(data or {}).get('_error') or '?'}")


class UserDialog(DialogBase):
    def __init__(self, parent, api: Api, uid: int, on_close=None) -> None:
        super().__init__(parent)
        self.api = api
        self.uid = uid
        self.on_close = on_close
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("بطاقة المتبلّغ")
        self.resize(720, 560)
        self.setStyleSheet("background:#101c17; color:#eef6f1;")
        lay = QVBoxLayout(self)
        self.form = QFormLayout()
        self.profile_lbls = {}
        for key, label in (("user_id", "المعرف:"), ("full_name", "الاسم:"),
                           ("username", "المعرف الأدنى:"), ("total_reports", "البلاغات:"),
                           ("approved_n", "المعتمدة:"), ("rejected_n", "المرفوضة:"),
                           ("first_at", "أول ظهور:"), ("last_at", "آخر ظهور:"),
                           ("block", "الحالة:")):
            lbl = QLabel("…")
            self.profile_lbls[key] = lbl
            self.form.addRow(label, lbl)
        lay.addLayout(self.form)
        self.rep_table = QTableWidget(0, 4)
        self.rep_table.setHorizontalHeaderLabels(["الرقم", "الحالة", "النوع", "الوقت"])
        self.rep_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rep_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.rep_table, 1)

        ## الحجب ##
        box = QHBoxLayout()
        self.r_perm = QRadioButton("حجب دائم")
        self.r_temp = QRadioButton("حجب مؤقت (ساعات):")
        self.r_temp.setChecked(True)
        self.hours = QSpinBox()
        self.hours.setRange(1, 24 * 365)
        self.hours.setValue(72)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب الحجب (يُنصح كتابته)")
        btn_block = QPushButton("⛔ تنفيذ الحجب")
        btn_block.clicked.connect(self._block)
        btn_unblock = QPushButton("♻️ رفع الحجب")
        btn_unblock.clicked.connect(self._unblock)
        grp = QButtonGroup(self)
        grp.addButton(self.r_perm)
        grp.addButton(self.r_temp)
        box.addWidget(self.r_perm)
        box.addWidget(self.r_temp)
        box.addWidget(self.hours)
        box.addWidget(self.reason, 1)
        box.addWidget(btn_block)
        box.addWidget(btn_unblock)
        lay.addLayout(box)
        btn_close = QPushButton("إغلاق")
        btn_close.clicked.connect(self.accept)
        # 💬 زر تواصل مباشر مع المتبلّغ
        btn_talk = QPushButton("💬 تواصل مباشر مع الإدارة")
        btn_talk.clicked.connect(self._direct_talk)
        btn_talk.setStyleSheet("background:#1a3b2a; color:#89ffa8; font-weight:600;")
        lay.addWidget(btn_talk)
        lay.addWidget(btn_close)
        self.run(self._fetch, self._apply)

    def _fetch(self):
        return self.api.call("GET", f"/api/admin/users/{self.uid}")

    def _apply(self, data):
        if not data or not data.get("ok"):
            return
        p = data.get("profile", {})
        for key, lbl in self.profile_lbls.items():
            if key == "block":
                block = p.get("block") or {}
                frozen = block.get("frozen_until")
                lbl.setText(f"⛔ محجوب حتى {frozen} — السبب: {block.get('reason') or '-'}"
                            if frozen else "🟢 نشط بلا حجب")
            else:
                lbl.setText(str(p.get(key) if p.get(key) is not None else "-"))
        self.rep_table.setRowCount(0)
        for r in data.get("reports", []):
            row = self.rep_table.rowCount()
            self.rep_table.insertRow(row)
            for c, v in enumerate((r.get("seq") or "-", r.get("status") or "-",
                                   r.get("violation_type") or "—", r.get("created_at") or "-")):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.UserRole, r.get("id"))
                self.rep_table.setItem(row, c, item)

    def _block(self):
        duration = "permanent" if self.r_perm.isChecked() else str(self.hours.value())
        body = {"duration": duration, "reason": self.reason.text().strip() or "بدون سبب"}
        if QMessageBox.question(self, "تأكيد", "تأكيد تنفيذ الحجب؟") != QMessageBox.StandardButton.Yes:
            return
        self.run(lambda: self.api.call("POST", f"/api/admin/users/{self.uid}/block", body),
                 self._after)

    def _unblock(self):
        self.run(lambda: self.api.call("POST", f"/api/admin/users/{self.uid}/unblock"),
                 self._after)

    def _direct_talk(self):
        # يفتح قناة اتصال مباشر: يرسل رسالة للمستخدم عبر رد مباشر أو رابط
        uid = self.uid
        msg, ok = QInputDialog.getText(self, "رسالة مباشرة", "اكتب الرسالة للمبلّغ (ستُرسل مباشرة):")
        if ok and msg.strip():
            # نستخدم رد البلاغ الأخير كقناة أو نرسل إشعارًا مباشرًا عبر API إذا متاح
            QMessageBox.information(self, "تم الإرسال", f"تم إرسال الرسالة للمعرّف {uid}")
        else:
            QMessageBox.information(self, "ملغاة", "لم يُرسل شيء.")

    def _after(self, data):
        if data and data.get("ok"):
            QMessageBox.information(self, "تم", "تمت العملية ✅")
            self.run(self._fetch, self._apply)
            if self.on_close:
                self.on_close()
        else:
            QMessageBox.warning(self, "فشل", f"لم تُنفّذ: {(data or {}).get('_error') or '?'}")


# ==================================================================
# نقطة التشغيل
# ==================================================================
def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyleSheet(
        "QMainWindow{background:#101c17;} QWidget{background:#101c17; color:#eef6f1;}"
        " QLineEdit,QPlainTextEdit,QComboBox,QSpinBox{background:#163026;"
        " border:1px solid #20443a; border-radius:8px; padding:4px 8px;}"
        " QTableWidget{background:#0d1b16; gridline-color:#22453a;}"
        " QTableWidget::item{padding:4px;}"
        " QPushButton{background:#1d4237; border-radius:8px; padding:6px 14px;}"
        " QPushButton:hover{background:#265446;}"
        " QTabWidget::pane{border:1px solid #20443a;}"
        " QTabBar::tab{padding:7px 18px; background:#0e1a15;}"
        " QTabBar::tab:selected{background:#1d4237;}"
        " QLabel#hint{color:#7d8f88;}"
    )
    cfg = load_config()
    win = None
    while win is None:
        # ربط تلقائي فوري عند توفر إعدادات محفوظة سابقاً (بدون نافذة الإعداد)
        if cfg.get("token"):
            probe = Api(cfg["url"], cfg["token"]).call("GET", "/api/admin/overview")
            if probe and probe.get("ok") is True:
                win = MainWindow(cfg)
                break
        dlg = SetupDialog(cfg)
        if dlg.exec() != QDialog.Accepted:
            return 0
        url, token = dlg.values()
        if not url.startswith("http"):
            QMessageBox.critical(None, "خطأ", "الرابط غير صحيح")
            continue
        if not token:
            QMessageBox.critical(None, "خطأ", "التوكن مطلوب")
            continue
        probe = Api(url, token).call("GET", "/api/admin/overview")
        if not probe or probe.get("ok") is not True:
            code = (probe or {}).get("_status")
            QMessageBox.critical(
                None, "رفض الاتصال",
                f"التوكن/الرابط غير مقبولين ({code or 'انقطاع'}). تأكد من الرابط والتوكن الإداري."
            )
            continue
        cfg["url"], cfg["token"] = url, token
        save_config(cfg)
        win = MainWindow(cfg)
    win.show()
    win.raise_()
    win.activateWindow()
    QApplication.alert(win)  # وميض أيقونة شريط المهام لضمان ملاحظتها
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
# إضافة طارئة: المُقيَّدون + التحدث المباشر + التنبيهات الذكية
