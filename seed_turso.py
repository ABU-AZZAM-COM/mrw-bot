import sqlite3, os, sys

db_url = os.getenv('TURSO_DATABASE_URL', '').strip()
auth_token = os.getenv('TURSO_AUTH_TOKEN', '').strip()

texts = [
    ("welcome_message","رسالة الترحيب","🛡️ منظومة البلاغات...","user_facing"),
    ("verify_consent","نص الموافقة للتوثيق","🪪 التسجيل كمراسل موثوق... النص الطويل...","user_facing"),
    ("help_text","دليل الاستخدام","📖 الدليل السريع...","user_facing"),
    ("privacy_policy","سياسة الخصوصية","🔒 سياسة الخصوصية...","user_facing"),
    ("verify_button","زر التوثيق","✅ توثيق","button_label"),
    ("menu_button","زر القائمة","📋 القائمة","button_label"),
]

try:
    import turso_serverless
    conn = turso_serverless.connect(db_url, auth_token=auth_token)
    conn.execute("CREATE TABLE IF NOT EXISTS bot_texts (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, label TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', category TEXT DEFAULT 'user_facing', updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    for key, label, content, category in texts:
        conn.execute("INSERT OR IGNORE INTO bot_texts (key, label, content, category) VALUES (?, ?, ?, ?)", (key, label, content, category))
    print("Applied to Turso successfully.")
except Exception as e:
    print("Turso seed failed:", e)
    # Fallback: show that local SQLite has it
    local = sqlite3.connect('data/reports.db')
    count = local.execute("SELECT count(*) FROM bot_texts").fetchone()[0]
    print("Local SQLite bot_texts count:", count)
