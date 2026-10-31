import sqlite3, os
path = r'C:\Users\AZZAM\.copilot\copilot-worktrees\مشروع المخالفات العسكرية\abu-azzam-com-verbose-train\live_demo.db'
texts = [
    ("welcome_message", "رسالة الترحيب", "🛡️ منظومة البلاغات والشكاوى\nإدارة الشرطة العسكرية\n────────────────────\nأهلًا بك...", "user_facing"),
    ("verify_consent", "نص الموافقة للتوثيق", "🪪 التسجيل كمراسل موثوق\n\nالغرض: توثيق حساب المراسل لدى الشرطة العسكرية فرع الحسكة...", "user_facing"),
    ("help_text", "دليل الاستخدام", "📖 الدليل السريع\n────────────────────\n📝 إرسال بلاغ: اضغط /start...", "user_facing"),
    ("privacy_policy", "سياسة الخصوصية", "🔒 سياسة الخصوصية\nتُحفظ البيانات بشكل آمن ولا تُشارك مع جهات خارجية...", "user_facing"),
    ("verify_button", "زر التوثيق", "✅ توثيق", "button_label"),
    ("menu_button", "زر القائمة", "📋 القائمة", "button_label"),
    ("cancel_button", "زر الإلغاء", "❌ إلغاء", "button_label"),
    ("confirm_button", "زر التأكيد", "✅ إرسال البلاغ", "button_label"),
    ("contact_admin", "تواصل مع الإدارة", "📞 تواصل مع الإدارة", "button_label"),
    ("reporting_paused", "إيقاف الاستقبال", "🛑 استقبال البلاغات الجديدة متوقف مؤقتًا.", "user_facing"),
]
conn = sqlite3.connect(path)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS bot_texts (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, label TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', category TEXT DEFAULT 'user_facing', updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
for key, label, content, category in texts:
    cursor.execute("INSERT OR IGNORE INTO bot_texts (key, label, content, category) VALUES (?, ?, ?, ?)", (key, label, content, category))
conn.commit()
print(f"Inserted/updated {len(texts)} texts in {path}")
