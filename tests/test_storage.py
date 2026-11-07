import pytest
from cryptography.fernet import Fernet

from tracker.storage import StateStore


def test_round_trip_and_no_plaintext_on_disk(tmp_path):
    path = tmp_path / "state.enc"
    store = StateStore(path, Fernet.generate_key().decode())
    assert store.load() == (None, "")

    text = store.dump({"active_location": "away"})
    store.save(text)
    assert b"away" not in path.read_bytes()
    assert store.load() == ({"active_location": "away"}, text)


def test_wrong_key_fails_loudly(tmp_path):
    path = tmp_path / "state.enc"
    StateStore(path, Fernet.generate_key().decode()).save("{}")
    with pytest.raises(SystemExit):
        StateStore(path, Fernet.generate_key().decode()).load()
