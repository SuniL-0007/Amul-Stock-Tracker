import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
ATTEMPTS = 4
MAX_WAIT_S = 30


class Telegram:
    def __init__(self, token: str, chat_id: str, sleep=time.sleep):
        self.token = token
        self.chat_id = chat_id
        self._sleep = sleep

    def _call(self, method: str, params: dict) -> dict:
        """POST to the Bot API, retrying network errors, 429s and 5xxs with backoff."""
        data = urllib.parse.urlencode(params).encode()
        url = API.format(token=self.token, method=method)
        for attempt in range(1, ATTEMPTS + 1):
            wait = 2**attempt
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as resp:
                    body = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 and e.code < 500:
                    raise RuntimeError(f"Telegram {method} failed: HTTP {e.code}") from None
                try:
                    wait = json.load(e).get("parameters", {}).get("retry_after", wait)
                except Exception:
                    pass
                err = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = type(e).__name__
            if attempt == ATTEMPTS:
                # `from None`: the original exception can carry the URL, which holds the bot token.
                raise RuntimeError(f"Telegram {method} failed after {ATTEMPTS} attempts: {err}") from None
            self._sleep(min(wait, MAX_WAIT_S))
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
