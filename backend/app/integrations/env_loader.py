"""Load backend/.env (if present) without extra dependencies.

dotenv-less: อ่านบรรทัดแบบ KEY=VALUE (ข้าม comment/บรรทัดว่าง) แล้ว setdefault
ลง os.environ — env ที่มีอยู่ก่อนชนะไฟล์เสมอ เปิดใช้เมื่อมีไฟล์ ทำให้ "ใส่ token
Telegram ใน .env แล้วใช้ได้ทันที" โดยไม่ต้องแก้ run_dev.py
"""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    p = Path(path) if path else _DEFAULT_PATH
    loaded: dict[str, str] = {}
    if not p.exists():
        return loaded
    try:
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if not key:
                continue
            loaded[key] = value
            os.environ.setdefault(key, value)
    except OSError:
        pass
    return loaded
