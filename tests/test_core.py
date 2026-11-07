from datetime import datetime

import pytest

from tracker.core import IST, Config, run_cycle
from tracker.models import Product, ScrapeError
from tracker.scraper import parse_products

CHAT = "111"
CFG = Config(locations={"home": "111111", "away": "222222"}, default_location="home", chat_id=CHAT)
WEEKDAY = datetime(2026, 11, 4, 12, tzinfo=IST)  # Wednesday
SUNDAY_EARLY = datetime(2026, 11, 8, 7, tzinfo=IST)
SUNDAY_LATE = datetime(2026, 11, 8, 10, tzinfo=IST)


def p(alias, available, price=100):
    return Product(alias=alias, name=alias.title(), price=price, available=available)


def fetcher(by_pin):
    calls = []

    def fetch(pin):
        calls.append(pin)
        result = by_pin[pin]
        if isinstance(result, Exception):
            raise result
        return result

    fetch.calls = calls
    return fetch


def msg(update_id, text, chat=CHAT):
    return {"update_id": update_id, "message": {"chat": {"id": int(chat)}, "text": text}}


def settled(stock, location="home"):
    """State after a normal earlier run at `location` with `stock` remembered."""
    state, _ = run_cycle(None, CFG, [], fetcher({CFG.locations[location]: stock}), WEEKDAY)
    state["active_location"] = location
    return state


def test_first_run_sends_snapshot_but_no_restock_alerts():
    state, out = run_cycle(None, CFG, [], fetcher({"111111": [p("whey", True), p("lassi", False)]}), WEEKDAY)
    assert len(out) == 1
    assert out[0].silent and "Now tracking" in out[0].text and "1/2 in stock" in out[0].text
    assert state["locations"]["home"]["stock"] == {"whey": True, "lassi": False}


def test_restock_is_loud_with_buy_link():
    state = settled([p("whey", False)])
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", True, 2400)]}), WEEKDAY)
    assert len(out) == 1
    assert not out[0].silent
    assert "Back in stock" in out[0].text
    assert "https://shop.amul.com/en/product/whey" in out[0].text and "₹2400" in out[0].text


def test_no_message_when_nothing_changes():
    state = settled([p("whey", True), p("lassi", False)])
    new_state, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", True), p("lassi", False)]}), WEEKDAY)
    assert out == [] and new_state == state


def test_sold_out_again_is_silent():
    state = settled([p("whey", True)])
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", False)]}), WEEKDAY)
    assert len(out) == 1 and out[0].silent and "Sold out again" in out[0].text


def test_new_product_appearing_in_stock_counts_as_restock():
    state = settled([p("whey", False)])
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", False), p("paneer", True)]}), WEEKDAY)
    assert len(out) == 1 and not out[0].silent and "Paneer" in out[0].text


def test_allowlist_limits_alerts_but_memory_tracks_everything():
    cfg = Config(**{**CFG.__dict__, "allowlist": frozenset({"whey"})})
    state, _ = run_cycle(None, cfg, [], fetcher({"111111": [p("whey", False), p("lassi", False)]}), WEEKDAY)
    state, out = run_cycle(state, cfg, [], fetcher({"111111": [p("whey", False), p("lassi", True)]}), WEEKDAY)
    assert out == []
    assert state["locations"]["home"]["stock"]["lassi"] is True


def test_location_switch_checks_new_pincode_and_sends_snapshot_only():
    state = settled([p("whey", False)])
    state["locations"]["away"] = {"stock": {"whey": False}}  # stale memory from weeks ago
    fetch = fetcher({"222222": [p("whey", True)]})
    new_state, out = run_cycle(state, CFG, [msg(5, "/location Away")], fetch, WEEKDAY)
    assert fetch.calls == ["222222"]
    assert new_state["active_location"] == "away"
    assert new_state["telegram_offset"] == 5
    assert len(out) == 1 and out[0].silent and "Away" in out[0].text and "Whey" in out[0].text
    assert new_state["locations"]["home"]["stock"] == {"whey": False}  # untouched


def test_commands_from_other_chats_are_ignored():
    state = settled([p("whey", False)])
    new_state, out = run_cycle(state, CFG, [msg(9, "/location away", chat="999")], fetcher({"111111": [p("whey", False)]}), WEEKDAY)
    assert new_state["active_location"] == "home"
    assert new_state["telegram_offset"] == 9  # consumed, so it isn't re-read
    assert out == []


def test_unknown_location_and_unknown_command_get_help():
    state = settled([p("whey", False)])
    _, out = run_cycle(state, CFG, [msg(1, "/location delhi"), msg(2, "hello")], fetcher({"111111": [p("whey", False)]}), WEEKDAY)
    assert "Unknown location" in out[0].text and "/status" in out[1].text


def test_status_replies_with_fresh_stock():
    state = settled([p("whey", True)])
    _, out = run_cycle(state, CFG, [msg(3, "/status@AmulBot")], fetcher({"111111": [p("whey", True)]}), WEEKDAY)
    assert len(out) == 1 and "Checked just now" in out[0].text and "1/1" in out[0].text


def test_failure_alert_after_three_in_a_row_then_recovery():
    state = settled([p("whey", False)])
    broken = fetcher({"111111": ScrapeError("HTTP 401")})
    loud = []
    for _ in range(4):
        state, out = run_cycle(state, CFG, [], broken, WEEKDAY)
        loud.append(out)
    assert loud[0] == [] and loud[1] == []
    assert len(loud[2]) == 1 and not loud[2][0].silent and "HTTP 401" in loud[2][0].text
    assert loud[3] == []  # alerted once only
    state, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", False)]}), WEEKDAY)
    assert len(out) == 1 and out[0].silent and "recovered" in out[0].text
    assert state["consecutive_failures"] == 0 and not state["failure_alerted"]


def test_failure_keeps_stock_memory_so_no_false_alert_after():
    state = settled([p("whey", True)])
    state, _ = run_cycle(state, CFG, [], fetcher({"111111": ScrapeError("x")}), WEEKDAY)
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", True)]}), WEEKDAY)
    assert out == []


def test_switch_during_failure_explains_and_snapshot_waits():
    state = settled([p("whey", False)])
    state, out = run_cycle(state, CFG, [msg(1, "/location away")], fetcher({"222222": ScrapeError("timeout")}), WEEKDAY)
    assert "failed" in out[0].text and state["pending_snapshot"]
    state, out = run_cycle(state, CFG, [], fetcher({"222222": [p("whey", True)]}), WEEKDAY)
    assert "Now tracking" in out[0].text and not state["pending_snapshot"]


@pytest.mark.parametrize(
    "now,expected",
    [(WEEKDAY, 0), (SUNDAY_EARLY, 0), (SUNDAY_LATE, 1)],
    ids=["same-day-as-snapshot", "next-day-before-9am", "next-day-after-9am"],
)
def test_daily_summary_after_9am_once_per_day(now, expected):
    state = settled([p("whey", True), p("lassi", False)])  # first-run snapshot covers that day
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", True), p("lassi", False)]}), now)
    summaries = [m for m in out if "Daily summary" in m.text]
    assert len(summaries) == expected
    if summaries:
        assert summaries[0].silent and "1/2 in stock" in summaries[0].text and "Whey" in summaries[0].text


def test_snapshot_before_9am_still_gets_summary_later_that_day():
    early = datetime(2026, 11, 4, 7, tzinfo=IST)
    state, _ = run_cycle(None, CFG, [], fetcher({"111111": [p("whey", True)]}), early)
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", True)]}), WEEKDAY)
    assert ["Daily summary" in m.text for m in out] == [True]


def test_summary_once_per_day():
    state = settled([p("whey", True)])
    state, _ = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", True)]}), SUNDAY_LATE)
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [p("whey", True)]}), SUNDAY_LATE)
    assert out == []


def test_parse_products_treats_zero_quantity_as_sold_out():
    body = {"data": [
        {"alias": "a", "name": "A", "price": 10, "available": 1, "inventory_quantity": 5},
        {"alias": "b", "name": "B", "price": 10, "available": 1, "inventory_quantity": 0},
        {"alias": "c", "name": "C", "price": 10, "available": 0, "inventory_quantity": 3},
    ]}
    assert [x.available for x in parse_products(body)] == [True, False, False]


def test_names_are_html_escaped():
    state = settled([Product("x", "A<b>&", 1, False)])
    _, out = run_cycle(state, CFG, [], fetcher({"111111": [Product("x", "A<b>&", 1, True)]}), WEEKDAY)
    assert "A&lt;b&gt;&amp;" in out[0].text


def test_renamed_locations_are_forgotten_and_snapshot_sent():
    old = settled([p("whey", True)])
    old["active_location"] = "oldcity"
    old["locations"] = {"oldcity": {"stock": {"whey": False}}}
    state, out = run_cycle(old, CFG, [], fetcher({"111111": [p("whey", True)]}), WEEKDAY)
    assert state["active_location"] == "home"
    assert set(state["locations"]) == {"home"}
    assert len(out) == 1 and "Now tracking" in out[0].text
