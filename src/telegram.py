"""Send a Telegram message (HTML) from the cloud jobs. Never raises; does nothing without the two env vars.

    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID    (GitHub Actions secrets)
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid

LIMIT = 4000   # Telegram's hard cap is 4096 characters per message
TIMEOUT = 10
PHOTO_TIMEOUT = 30
CAPTION_LIMIT = 1024   # Telegram's cap for a photo caption


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


def multipart(fields, filename, data, boundary):
    """multipart/form-data body: text `fields` plus one PNG file part named 'photo'."""
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
            f"Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return out


def send_photo(png_bytes, caption=""):
    """sendPhoto with an HTML caption (cut to Telegram's 1024 characters). False on missing env or any failure."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        boundary = uuid.uuid4().hex
        fields = {"chat_id": chat, "caption": caption[:CAPTION_LIMIT], "parse_mode": "HTML"}
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=multipart(fields, "briefing.png", png_bytes, boundary),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        with urllib.request.urlopen(req, timeout=PHOTO_TIMEOUT) as r:
            return r.status == 200
    except Exception as e:
        print(f"telegram photo failed: {type(e).__name__}")   # never print the URL: it holds the token
        return False
