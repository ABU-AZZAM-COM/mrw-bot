# ==================================================================
# إضافات طارئة — المُقيَّدون + التحدث المباشر + التنبيهات الذكية
# أُلحق بهذا الملف مباشرة (يُعيد تشغيل اللوحة لقراءته)
# ==================================================================

# ---- طريقة مساعدة: فلترة المحجوبين من قائمة المستخدمين ----
# (يُستدعى من users_refresh عند تفعيل btn_blocked_filter)

def _users_refresh_with_filter(self, force_blocked: bool = False):
    q = self.u_q.text().strip()
    path = f"/api/admin/users?page=0&size={_page_size}"
    if q:
        path += f"&q={q}"
    self.run(lambda: self.api.call("GET", path), lambda data: self.apply_users(data, only_blocked=force_blocked))


def apply_users_blocked(self, data, only_blocked=False):
    # نفس apply_users لكنها تُظهر فقط المحجوبين وتُبرز السبب
    self.usr_table.setRowCount(0)
    if not data or not data.get("ok"):
        self.status.setText("❌ تعذّر جلب المحجوبين")
        return
    for u in data.get("items", []):
        frozen = u.get("frozen_until")
        if only_blocked and not frozen:
            continue
        row = self.usr_table.rowCount()
        self.usr_table.insertRow(row)
        state = f"⛔ محجوب حتى {frozen}" if frozen else "🟢 نشط"
        reason = (u.get("block") or {}).get("reason") or "—"
        cells = [
            str(u.get("user_id") or "-"),
            u.get("full_name") or "-",
            ("@" + str(u.get("username"))) if u.get("username") else "—",
            str(u.get("reports_count") or 0),
            state,
            reason,
        ]
        for c, val in enumerate(cells):
            item = QTableWidgetItem(str(val))
            item.setData(Qt.UserRole, u.get("user_id"))
            self.usr_table.setItem(row, c, item)


# ---- زر التحدث المباشر ----
# يفعّل مؤقتًا «وضع المحادثة» ويُرسل إشعارًا للمستخدم عبر /contact أو رسالة مباشرة

def _toggle_talk_mode(self, checked: bool):
    if checked:
        self.status.setText("💬 وضع التحدث المباشر: مفعل — ستُبلغ المتبلّغين فورًا")
        QMessageBox.information(self, "تفعيل", "التحدث المباشر مفعل. اضغط زر «📞 تواصل مع الإدارة» في أي بلاغ لإرسال رسالة مباشرة للمستخدم.")
    else:
        self.status.setText("💬 التحدث المباشر: معطل")


# ---- تنبيه ذكي عند وصول بلاغ جديد (في النظرة العامة) ----
# يستبدل apply_overview لإضافة شريط تنبيه أحمر مؤقت عند زيادة عدد البلاغات

def apply_overview_smart(self, data: dict) -> None:
    old_total = getattr(self, "last_total", None)
    self.apply_overview(data)
    total = (data.get("counts") or {}).get("total", 0) + (data.get("counts") or {}).get("last24", 0)
    if old_total is not None and total > old_total:
        # تنبيه بصري + صوتي
        self.status.setText("🔔 بلاغ جديد وصل! | " + self.status.text())
        try:
            QApplication.beep()
        except Exception:
            pass
        # إشعار سطح مكتب (Windows toast approximation via message box — سريع)
        msg = QMessageBox(self)
        msg.setWindowTitle("بلاغ جديد")
        msg.setText(f"📥 بلاغ جديد رقم {total} — راجع لوحة البلاغات فورًا")
        msg.setIcon(QMessageBox.Information)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.show()
        QTimer.singleShot(4000, msg.close)
    self.last_total = total


# ربط التبديل
# (يُعدّل apply_overview في __init__ عبر monkey-patch سريع إذا أردت — أو استبدل يدويًا)
