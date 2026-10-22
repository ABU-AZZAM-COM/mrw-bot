import pathlib

p = pathlib.Path("archive.py")
text = p.read_text(encoding="utf-8")

for i, line in enumerate(text.splitlines(), 1):
    bad_chars = [c for c in line if 0x2E80 <= ord(c) <= 0x9FFF or 0xAC00 <= ord(c) <= 0xD7AF]
    if bad_chars:
        print(i, repr(line))
        print("   محارف غريبة:", [hex(ord(c)) for c in bad_chars])
        # استبدال السلسلةCJK كاملة بكلمة عربية
        new_line = line
        for c in set(bad_chars):
            new_line = new_line.replace(c, "")
        new_line = new_line.replace("طلبات ,", "طلبات لوقت طويل،")
        new_line = new_line.replace("طلبات", "طلبات لوقت طويل،", 1) if "لوقت طويل" not in new_line else new_line
        lines = text.splitlines()
        lines[i - 1] = new_line
        text = "\n".join(lines)
        print("   بعد الإصلاح:", repr(new_line))

p.write_text(text + "\n", encoding="utf-8")
print("تم")
