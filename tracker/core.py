"""Pure tracker logic: commands, stock diffing, failure tracking, heartbeat.

`run_cycle` takes the saved state plus inputs from the outside world and returns the new state and
the messages to send. It does no I/O itself, so it can be tested without a browser or Telegram.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Callable

from .models import Product, ScrapeError

IST = timezone(timedelta(hours=5, minutes=30))
FAILURE_ALERT_THRESHOLD = 3
HEARTBEAT_WEEKDAY = 6  # Sunday
HEARTBEAT_HOUR = 9  # IST, "Sunday morning"


@dataclass(frozen=True)
class Config:
    locations: dict[str, str]  # name -> pincode
    default_location: str
    chat_id: str
    allowlist: frozenset[str] = frozenset()  # product aliases; empty = everything

    def watched(self, product: Product) -> bool:
        return not self.allowlist or product.alias in self.allowlist


@dataclass(frozen=True)
class Message:
    text: str
    silent: bool


def initial_state(config: Config) -> dict:
    return {
        "active_location": config.default_location,
        "telegram_offset": 0,
        "consecutive_failures": 0,
        "failure_alerted": False,
        "last_heartbeat": None,
        "pending_snapshot": True,  # greet with a snapshot on the very first run
        "locations": {},
    }


# ---------- formatting ----------

def _title(location: str) -> str:
    return location.capitalize()


def _price(p: Product) -> str:
    if p.price is None:
        return ""
    value = int(p.price) if float(p.price).is_integer() else p.price
    return f" — ₹{value}"


def _line(p: Product, link: bool = True) -> str:
    name = escape(p.name)
    if link:
        name = f'<a href="{p.url}">{name}</a>'
    return f"• {name}{_price(p)}"


def _snapshot(location: str, products: list[Product], config: Config, header: str) -> str:
    watched = [p for p in products if config.watched(p)]
    in_stock = [p for p in watched if p.available]
    lines = [f"{header}\n📍 <b>{_title(location)}</b> · {len(in_stock)}/{len(watched)} in stock"]
    if in_stock:
        lines.append("")
        lines.extend(_line(p) for p in in_stock)
    return "\n".join(lines)


HELP = (
    "Commands:\n"
    "/location &lt;name&gt; — switch location ({names})\n"
    "/status — current location and stock"
)


# ---------- commands ----------

def _parse_command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return "", ""
    cmd = parts[0][1:].split("@", 1)[0].lower()
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    return cmd, arg


def _handle_updates(state: dict, updates: list[dict], config: Config, out: list[Message]) -> bool:
    """Apply Telegram commands. Returns True if /status was requested."""
    status_requested = False
    names = ", ".join(sorted(config.locations))
    for upd in updates:
        state["telegram_offset"] = max(state["telegram_offset"], upd.get("update_id", 0))
        msg = upd.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(config.chat_id):
            continue  # not my chat
        cmd, arg = _parse_command(msg.get("text") or "")
        if cmd == "location":
            if arg in config.locations:
                state["active_location"] = arg
                state["pending_snapshot"] = True
            else:
                out.append(Message(f"Unknown location. Choose one of: {names}", silent=True))
        elif cmd == "status":
            status_requested = True
        else:
            out.append(Message(HELP.format(names=names), silent=True))
    return status_requested


# ---------- main cycle ----------

def run_cycle(
    state: dict | None,
    config: Config,
    updates: list[dict],
    fetch: Callable[[str], list[Product]],
    now: datetime,
) -> tuple[dict, list[Message]]:
    state = copy.deepcopy(state) if state else initial_state(config)
    # Locations renamed or removed in config.json: forget them and start fresh at the default.
    state["locations"] = {k: v for k, v in state.get("locations", {}).items() if k in config.locations}
    if state.get("active_location") not in config.locations:
        state["active_location"] = config.default_location
        state["pending_snapshot"] = True
    out: list[Message] = []

    status_requested = _handle_updates(state, updates, config, out)
    location = state["active_location"]

    try:
        products = fetch(config.locations[location])
    except ScrapeError as e:
        _on_failure(state, location, str(e), status_requested, out)
        return state, out

    if state["failure_alerted"]:
        out.append(Message("✅ Tracker recovered — stock checks are working again.", silent=True))
    state["consecutive_failures"] = 0
    state["failure_alerted"] = False

    loc_state = state["locations"].setdefault(location, {"stock": {}})
    previous: dict[str, bool] = loc_state["stock"]
    # On a first check or right after a switch, the snapshot reports stock instead of
    # change alerts, so a stale memory can't produce false "back in stock" messages.
    if previous and not state.get("pending_snapshot"):
        restocked = [p for p in products if p.available and not previous.get(p.alias, False)]
        sold_out = [p for p in products if not p.available and previous.get(p.alias, False)]
        restocked = [p for p in restocked if config.watched(p)]
        sold_out = [p for p in sold_out if config.watched(p)]
        if restocked:
            text = f"🟢 <b>Back in stock</b> · {_title(location)}\n\n" + "\n".join(
                _line(p) for p in restocked
            )
            out.append(Message(text, silent=False))
        if sold_out:
            text = f"🔴 Sold out again · {_title(location)}\n" + "\n".join(
                _line(p, link=False) for p in sold_out
            )
            out.append(Message(text, silent=True))

    loc_state["stock"] = {p.alias: p.available for p in products}

    if state.get("pending_snapshot"):
        out.append(Message(_snapshot(location, products, config, "👀 Now tracking"), silent=True))
        state["pending_snapshot"] = False
    elif status_requested:
        out.append(Message(_snapshot(location, products, config, "ℹ️ Checked just now"), silent=True))

    local = now.astimezone(IST)
    today = local.date().isoformat()
    if (
        local.weekday() == HEARTBEAT_WEEKDAY
        and local.hour >= HEARTBEAT_HOUR
        and state.get("last_heartbeat") != today
    ):
        watched = [p for p in products if config.watched(p)]
        count = sum(p.available for p in watched)
        out.append(
            Message(f"💓 Tracker alive · {_title(location)} · {count}/{len(watched)} in stock", silent=True)
        )
        state["last_heartbeat"] = today

    return state, out


def _on_failure(state: dict, location: str, reason: str, status_requested: bool, out: list[Message]):
    state["consecutive_failures"] += 1
    reason = escape(reason)
    if state["consecutive_failures"] >= FAILURE_ALERT_THRESHOLD and not state["failure_alerted"]:
        out.append(
            Message(
                f"⚠️ <b>Tracker is failing</b> — {state['consecutive_failures']} checks in a row.\n"
                f"Last error: {reason}\nRestock alerts may be missed until this is fixed.",
                silent=False,
            )
        )
        state["failure_alerted"] = True
    if state.get("pending_snapshot") or status_requested:
        out.append(
            Message(
                f"📍 Tracking <b>{_title(location)}</b>, but the stock check just failed: {reason}\n"
                "I'll retry on the next run.",
                silent=True,
            )
        )
