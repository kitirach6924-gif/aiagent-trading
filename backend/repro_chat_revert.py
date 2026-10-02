"""Prove the chat regression tests FAIL on the pre-fix code.

Reverts the three defects in app/ai/chat_agent.py:
  1. _handle_chat fallback -> the old menu string (discarded live context)
  2. _handle_analysis regex -> requires a side word again
  3. _handle_analysis tail -> the old ask-back string

A regression test that never went red proves nothing. Run:
    python repro_chat_revert.py
"""
import pathlib
import shutil
import subprocess
import sys

TARGET = pathlib.Path("app/ai/chat_agent.py")
TEST = pathlib.Path("tests/test_chat_answers.py")
BACKUP = TARGET.with_suffix(".py.bak_repro")

# 1. the menu fallback
OLD_FALLBACK = '''        if ai:
            return ai["content"]
        return ("ผมเป็น trading agent chat — ถามเรื่อง market/positions/statistics หรือสั่งงาน strategy ได้ครับ "
                "(LLM ยังไม่ได้ตั้งค่า: ตอบได้เฉพาะคำสั่งที่รู้จัก)")'''

# 2. the side-word-required regex
OLD_REGEX = 'm = re.search(r"ทำไม.*(ไม่|ยังไม่)\\s*(buy|sell|ขาย|ซื้อ)", t)'

# 3. the ask-back tail
OLD_TAIL = 'return "ให้ข้อมูลหน่อยสิ: จะวิเคราะห์ trade ที่แพ้, สถิติ, หรือกราฟย้อนหลัง?"'


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    buggy = original
    applied = []

    if OLD_FALLBACK in buggy:
        buggy = buggy.replace(OLD_FALLBACK, OLD_FALLBACK)  # already there
    else:
        # _handle_chat is the last method in the file; replace to EOF.
        start = buggy.index('        if ai and ai.get("content", "").strip():')
        buggy = buggy[:start] + OLD_FALLBACK + "\n"
        applied.append("chat fallback")

    if OLD_REGEX in buggy:
        buggy = buggy.replace(OLD_REGEX, OLD_REGEX)
    else:
        i = buggy.index('m = re.search(r"ทำไม.*(?:ไม่|ยังไม่)')
        j = buggy.index("\n", i)
        buggy = buggy[:i] + OLD_REGEX + buggy[j:]
        applied.append("analysis regex")

    if OLD_TAIL in buggy:
        buggy = buggy.replace(OLD_TAIL, OLD_TAIL)
    else:
        i = buggy.index('        # Unknown analysis question.')
        j = buggy.index("    # ---------- STRATEGY", i)
        buggy = buggy[:i] + "        " + OLD_TAIL + "\n\n" + buggy[j:]
        applied.append("analysis tail")

    BACKUP.write_text(original, encoding="utf-8")
    try:
        TARGET.write_text(buggy, encoding="utf-8")
        print(f"--- reverted: {', '.join(applied)} ---")
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(TEST), "-q", "--no-header", "-p", "no:warnings"],
            capture_output=True, text=True, timeout=300,
        )
        out = r.stdout + r.stderr
        # show the failure names, not the whole traceback
        for line in out.splitlines():
            if line.startswith("FAILED") or "passed" in line or "failed" in line:
                print("  " + line.strip())
        detected = r.returncode != 0
        print()
        print("RESULT: chat tests DETECT the pre-fix bug  [PASS]"
              if detected else "RESULT: chat tests PASSED on buggy code  [FAIL]")
        return 0 if detected else 1
    finally:
        shutil.copyfile(BACKUP, TARGET)
        BACKUP.unlink(missing_ok=True)
        print("\n--- restored the fixed version ---")


if __name__ == "__main__":
    raise SystemExit(main())
