"""Send a Telegram message (HTML) from the cloud jobs. Never raises; does nothing without the two env vars.

    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID    (GitHub Actions secrets)
"""
from __future__ import annotations

import json
import os
import urllib.request

LIMIT = 4000   # Telegram's hard cap is 4096 characters per message
TIMEOUT = 10


def split(text, limit=LIMIT):
    """Cut at line breaks where possible so a tag never ends up in two messages."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur); cur = ""
            out.append(line[:limit]); line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def send(text):
    """True when every part was accepted by Telegram; False on missing env or any failure."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        for part in split(text):
            body = json.dumps({"chat_id": chat, "text": part, "parse_mode": "HTML",
                               "disable_web_page_preview": True}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                if r.status != 200:
                    return False
        return True
    except Exception as e:
        print(f"telegram send failed: {type(e).__name__}")   # never print the URL: it holds the token
        return False
