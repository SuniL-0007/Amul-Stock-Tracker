"""Reads protein stock from shop.amul.com for one pincode using a headless browser."""

import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .models import Product, ScrapeError

PROTEIN_URL = "https://shop.amul.com/en/browse/protein"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def parse_products(body: dict) -> list[Product]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ScrapeError("products response has no data list")
    products = []
    for item in data:
        if not isinstance(item, dict):
            continue
        alias = item.get("alias")
        if not alias:
            continue
        qty = item.get("inventory_quantity")
        products.append(
            Product(
                alias=alias,
                name=item.get("name") or alias,
                price=item.get("price"),
                available=bool(item.get("available")) and (qty is None or qty > 0),
                quantity=qty,
            )
        )
    return products


def fetch_products(pincode: str, timeout_s: int = 60) -> list[Product]:
    """Open the protein page, select `pincode`, and return the products the site reports."""
    pin_set = False
    captured: list[dict] = []
    errors: list[str] = []

    def on_response(resp):
        nonlocal pin_set
        url = resp.url
        if "setPreferences" in url:
            pin_set = True
        elif pin_set and "/api/1/entity/ms.products" in url:
            if resp.status != 200:
                errors.append(f"products API returned HTTP {resp.status}")
                return
            try:
                captured.append(resp.json())
            except Exception as e:  # body unavailable if the page navigated mid-read
                errors.append(f"could not read products response: {e}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.on("response", on_response)
                page.goto(PROTEIN_URL, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                pin_box = page.locator("#search")
                pin_box.wait_for(state="visible", timeout=30000)
                pin_box.fill(pincode)
                page.locator("a.searchitem-name").first.click(timeout=20000)

                deadline = time.monotonic() + timeout_s
                while not captured and time.monotonic() < deadline:
                    page.wait_for_timeout(500)
            finally:
                browser.close()
    except PlaywrightError as e:
        raise ScrapeError(f"browser error: {e.message.splitlines()[0]}") from e
    except Exception as e:  # anything else is still a failed check, not a crashed run
        raise ScrapeError(f"unexpected error: {type(e).__name__}") from e

    if not pin_set:
        raise ScrapeError("pincode was not accepted by the site")
    if not captured:
        detail = "; ".join(errors) or "no products response after setting pincode"
        raise ScrapeError(detail)

    # Use the largest response in case the page fired more than one products request.
    products = max((parse_products(b) for b in captured), key=len)
    if not products:
        raise ScrapeError("zero products found")
    return products
