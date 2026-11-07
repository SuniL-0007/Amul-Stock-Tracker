"""Entry point: one stock check. Run on a schedule (GitHub Actions every 15 minutes).

Environment:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   required
  PINCODE_<NAME>                         one per location in config.json, e.g. PINCODE_HOME
  STATE_KEY                              Fernet key that encrypts the state file (required)
  STATE_PATH                             encrypted state file (default: ./state.enc)
  DRY_RUN=1                              print messages instead of sending; skip Telegram entirely

The repo is public, so nothing printed here may reveal the active location or stock details.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import Config, run_cycle
from .scraper import fetch_products
from .storage import StateStore

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> Config:
    raw = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    locations = {}
    for name in raw["locations"]:
        pin = os.environ.get(f"PINCODE_{name.upper()}", "").strip()
        if not pin:
            sys.exit(f"Missing environment variable PINCODE_{name.upper()}")
        locations[name.lower()] = pin
    return Config(
        locations=locations,
        default_location=raw.get("default_location", raw["locations"][0]).lower(),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        allowlist=frozenset(raw.get("allowlist") or []),
    )


def main() -> None:
    config = load_config()
    key = os.environ.get("STATE_KEY", "").strip()
    if not key:
        sys.exit("Missing STATE_KEY secret (see README: 'Generate a state key')")
    store = StateStore(Path(os.environ.get("STATE_PATH", "state.enc")), key)
    state, old_text = store.load()

    dry_run = os.environ.get("DRY_RUN") == "1"
    telegram = None
    updates: list[dict] = []
    if not dry_run:
        from .telegram import Telegram

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token or not config.chat_id:
            sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        telegram = Telegram(token, config.chat_id)
        try:
            updates = telegram.get_updates((state or {}).get("telegram_offset", 0))
        except Exception:  # commands can wait for the next run; stock check shouldn't
            print("Could not read Telegram commands", file=sys.stderr)

    new_state, messages = run_cycle(state, config, updates, fetch_products, datetime.now(timezone.utc))

    for m in messages:
        if telegram:
            telegram.send(m.text, m.silent)
        else:
            print(f"--- {'silent' if m.silent else 'LOUD'} ---\n{m.text}\n")

    new_text = store.dump(new_state)
    changed = new_text != old_text
    if changed:
        store.save(new_text)
    ok = new_state["consecutive_failures"] == 0
    print(f"check={'ok' if ok else 'failed'} messages={len(messages)} state_changed={changed}")


if __name__ == "__main__":
    main()
