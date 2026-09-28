"""Проверка проекта на следы сбитой перекодировки UTF-8 ↔ ANSI.

Признак: текст был прочитан однобайтовой кодировкой (CP1251) и записан обратно
как UTF-8. Тогда каждая строка с русским текстом «залипает» в виде
«Р°РѕС‚РѕРІ», и обратное кодирование в CP1251 даёт валидный UTF-8.

Скрипт чинит строки по одной: если обратное кодирование возможно и строка
меняется — она испорчена. Строки с легитимными символами, которых нет в
CP1251 («→», «×», «°»), обратно не кодируются и остаются как есть.

Запуск: `python tmp/scan_mojibake.py` — только отчёт, `python tmp/scan_mojibake.py --fix`
— починить.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"node_modules", ".venv", "bin", "output", "__pycache__", "uploads"}
SUFFIXES = (".py", ".js", ".html", ".css", ".md", ".bat", ".txt", ".json")
FIX = "--fix" in sys.argv


def repair(line):
    out = bytearray()
    for ch in line:
        code = ord(ch)
        if 0x80 <= code <= 0x9F:
            out.append(code)
        else:
            out += ch.encode("cp1251")
    return bytes(out).decode("utf-8")


def main():
    total = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(p in SKIP for p in path.parts):
            continue
        if path.suffix.lower() not in SUFFIXES:
            continue
        text = path.read_bytes().decode("utf-8-sig")
        lines = text.replace("\r\n", "\n").split("\n")
        broken = []
        for number, line in enumerate(lines, 1):
            try:
                fixed = repair(line)
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if fixed != line:
                broken.append((number, line, fixed))
        if not broken:
            print("ок:  ", path.relative_to(ROOT))
            continue
        total += len(broken)
        print("БИТЫЙ:", path.relative_to(ROOT), "строк:", len(broken))
        for number, line, fixed in broken[:4]:
            print("   %d было: %s" % (number, line.strip()[:70]))
            print("   %d стало: %s" % (number, fixed.strip()[:70]))
        if FIX:
            for number, line, fixed in broken:
                lines[number - 1] = fixed
            path.write_bytes("\n".join(lines).encode("utf-8"))
            print("   -> починено")
    print()
    print("всего битых строк:", total)
    return 1 if (total and not FIX) else 0


if __name__ == "__main__":
    sys.exit(main())
