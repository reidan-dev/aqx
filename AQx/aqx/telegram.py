from __future__ import annotations

import json
import urllib.error
import urllib.request


class TelegramError(Exception):
    """Raised for any failure sending a Telegram message - missing configuration,
    a network problem, or an API-level rejection. Callers (GraphRunner) log the
    message and keep the flow running rather than aborting on it, same as Log never
    blocks a flow on anything."""


def send_message(bot_token: str, chat_id: str, text: str, timeout: float = 10.0) -> None:
    if not bot_token or not chat_id:
        raise TelegramError("bot token or chat ID not configured (Settings > Preferences)")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("description", str(exc))
        except Exception:
            detail = str(exc)
        raise TelegramError(detail) from exc
    except urllib.error.URLError as exc:
        raise TelegramError(f"could not reach Telegram ({exc.reason})") from exc
    if not body.get("ok"):
        raise TelegramError(str(body.get("description", body)))
