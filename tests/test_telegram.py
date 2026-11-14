import io
import json
import urllib.error

import pytest

from tracker import telegram as tg
from tracker.scraper import parse_products
from tracker.models import ScrapeError


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, body=b"{}"):
    return urllib.error.HTTPError("https://api.telegram.org/botSECRET/x", code, "err", {}, io.BytesIO(body))


def _client(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return FakeResp(json.dumps(o).encode())

    monkeypatch.setattr(tg.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    return tg.Telegram("SECRET", "1", sleep=sleeps.append), calls, sleeps


def test_send_retries_transient_errors(monkeypatch):
    retry_429 = _http_error(429, b'{"parameters": {"retry_after": 3}}')
    client, calls, sleeps = _client(
        monkeypatch, [urllib.error.URLError("reset"), _http_error(502), retry_429, {"ok": True}]
    )
    client.send("hi", silent=True)
    assert len(calls) == 4
    assert sleeps == [2, 4, 3]


def test_gives_up_without_leaking_token(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_http_error(503)] * tg.ATTEMPTS)
    with pytest.raises(RuntimeError) as exc:
        client.send("hi", silent=True)
    assert len(calls) == tg.ATTEMPTS
    assert "SECRET" not in str(exc.value) and exc.value.__cause__ is None


def test_client_errors_are_not_retried(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_http_error(400)])
    with pytest.raises(RuntimeError):
        client.send("hi", silent=True)
    assert len(calls) == 1


def test_parse_products_rejects_non_dict_body():
    with pytest.raises(ScrapeError):
        parse_products(["not", "a", "dict"])
    assert parse_products({"data": ["junk", {"alias": "a", "available": 1}]})[0].alias == "a"
