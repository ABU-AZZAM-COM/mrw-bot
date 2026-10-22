"""استخراج الكلمات الإنجليزية الملتصقة بالكلمات العربية لمراجعتها يدويًا."""
import pathlib
import re
from collections import Counter

ROOT = pathlib.Path(".")
SKIP_DIRS = {".venv", "__pycache__", "data", "logs", "exports", "media", ".git"}
ARABIC = r"\u0600-\u06FF"
LATIN = r"A-Za-z"

rx = re.compile(f"[{ARABIC}]([{LATIN}]{{2,}})|([{LATIN}]{{2,}})[{ARABIC}]")
counter = Counter()

for path in sorted(ROOT.rglob("*")):
    if path.is_dir() or any(part in SKIP_DIRS for part in path.parts):
        continue
    if path.suffix.lower() not in {".py", ".md", ".txt", ".sh", ".ps1", ".cmd", ".service", ".timer", ""}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        continue
    for line in text.splitlines():
        for m in rx.finditer(line):
            token = m.group(1) or m.group(2)
            counter[token] += 1

for token, count in counter.most_common():
    print(f"{count:3d}  {token}")
