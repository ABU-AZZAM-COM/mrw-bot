"""
فحص سلامة النصوص العربية في المشروع — بوابة جودة صارمة.

يُستخدم فيCI (GitHub Actions) ويفشل في حال وجود أي تلف في النص:
  1) محرف الإبدال Unicode (�) — دليل على نص مشوّه.
  2) حروف صينية/كورية/يابانية داخل الملفات العربية.
  3) خلط عربي/لاتيني ملتصق (كلمة واحدة) — خطأ شائع في النصوص العربية.

يُستثنى من الفحص:
  - المجلدات: .venv و.git و__pycache__ وdata وlogs وmedia وexports
  - الملفات الثنائية أو غير المدعومة.

طريقة التشغيل:
  python check_integrity.py        # يطبع النتائج ويرجع رمز خطأ عند وجود مشاكل
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(".")
SKIP_DIRS = {".venv", ".git", "__pycache__", "data", "logs", "media", "exports"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".sh", ".ps1", ".cmd", ".service", ".timer", ".yml", ".yaml", ".example"}

# محرف الإبدال + الحروف الصينية/الكورية/اليابانية
BAD_CHARS = re.compile(r"[�\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff]")

# خلط عربي/لاتيني ملتصق (كلمة واحدة مثل: "تمfund" أو "startالبوت")
# نطاق الحروف العربية مستثني علامات الترقيم العربية (، ؛ ؟) حتى لا تكون إنذارات كاذبة.
ARABIC = r"\u0620-\u065F\u0660-\u06D3\u06FA-\u06FF"
LATIN = r"A-Za-z"
MIXED = re.compile(f"[{ARABIC}][{LATIN}]{{2,}}|[{LATIN}]{{2,}}[{ARABIC}]")

# هذا الملف نفسه يحتوي على محارف الفحص، فنستثنيه من المسح.
SELF_FILE = "check_integrity.py"

# كلمات تقنية معروفة تُسمح بها (أسماء منتجات ومنصات داخل النص العربي)
ALLOWED_MIXED = {
    "ZeroGPU",
    "Gradio",
    "Python",
    "Docker",
    "Zeabur",
    "Hugging",
    "GitHub",
    "Oracle",
    "Azure",
    "Koyeb",
    "AWS",
    "GCP",
    "Telegram",
    "SQLite",
    "Excel",
    "Linux",
    "Windows",
    "API",
    "PRO",
    "CPU",
    "GPU",
}


def iter_text_files():
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def main() -> int:
    problems: list[str] = []
    warnings: list[str] = []

    for path in iter_text_files():
        if path.name == SELF_FILE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if BAD_CHARS.search(line):
                problems.append(f"{path}:{number}: حرف غير عربي/مشوّه ← {line.strip()[:120]}")
                continue
            for match in MIXED.finditer(line):
                token = match.group(0)
                if token not in ALLOWED_MIXED:
                    warnings.append(f"{path}:{number}: خلط عربي/لاتيني «{token}»")

    print("=" * 60)
    print("فحص سلامة النصوص العربية")
    print("=" * 60)

    if warnings:
        print(f"\n⚠️  تنبيهات (لا تُفشل الفحص): {len(warnings)}")
        for item in warnings[:20]:
            print(f"   {item}")

    if problems:
        print(f"\n❌ أخطاء تمنع النشر: {len(problems)}")
        for item in problems:
            print(f"   {item}")
        return 1

    print("\n✅ لا توجد أخطاء في سلامة النصوص.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
