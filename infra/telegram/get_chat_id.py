#!/usr/bin/env python
"""ดึง chat_id ของ Telegram bot: รันหลังกด Start กับบอทแล้ว
   python infra/telegram/get_chat_id.py <BOT_TOKEN>
พิมพ์ค่า TELEGRAM_CHAT_ID ที่พร้อมใช้ต่อท้าย"""
import json
import sys
import urllib.request


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python get_chat_id.py <BOT_TOKEN>")
        sys.exit(1)
    token = sys.argv[1].strip()
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}")
        sys.exit(1)
    updates = data.get("result", [])
    if not updates:
        print("ยังไม่มีข้อความ — กด Start ในแชทกับบอทก่อน (หรือพิมพ์ hi) แล้วรันซ้ำ")
        sys.exit(2)
    seen: set[int] = set()
    print("--- chats ที่พบ ---")
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid, title = chat.get("id"), chat.get("title") or chat.get("first_name") or chat.get("username")
        if cid is not None and cid not in seen:
            seen.add(cid)
            kind = "group/channel" if str(cid).startswith("-") else "private"
            print(f"  {kind}: {title} -> chat_id = {cid}")
    print("\nใส่ค่านี้ใน backend env:")
    print(f"TELEGRAM_CHAT_ID={seen.pop() if len(seen) == 1 else '<เลือก id จากด้านบน>'}")


if __name__ == "__main__":
    main()
