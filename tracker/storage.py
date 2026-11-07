"""Encrypted state file. The repo is public, so state (which reveals the active location) is
stored encrypted with STATE_KEY, a Fernet key kept in GitHub Secrets."""

import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class StateStore:
    def __init__(self, path: Path, key: str):
        self.path = path
        self.fernet = Fernet(key.encode())

    def load(self) -> tuple[dict | None, str]:
        """Returns (state, canonical plaintext) — plaintext is used to detect real changes, since
        ciphertext differs on every encryption even for identical content."""
        if not self.path.exists():
            return None, ""
        try:
            text = self.fernet.decrypt(self.path.read_bytes().strip()).decode("utf-8")
        except InvalidToken:
            raise SystemExit("state file can't be decrypted — was STATE_KEY changed?")
        return json.loads(text), text

    @staticmethod
    def dump(state: dict) -> str:
        return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def save(self, text: str) -> None:
        self.path.write_bytes(self.fernet.encrypt(text.encode("utf-8")) + b"\n")
