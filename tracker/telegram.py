import json
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def _call(self, method: str, params: dict) -> dict:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(API.format(token=self.token, method=method), data=data)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
        return body

    def get_updates(self, offset: int) -> list[dict]:
        """Messages received since `offset` (the last processed update_id)."""
        body = self._call("getUpdates", {"offset": offset + 1, "timeout": 0, "allowed_updates": '["message"]'})
        return body.get("result", [])

    def send(self, text: str, silent: bool) -> None:
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                "disable_notification": "true" if silent else "false",
            },
        )
