"""Browser extraction with retries, selector fallbacks, overlay handling, validation and drift detection."""

from __future__ import annotations

import glob
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin

from playwright.sync_api import Error as PwError
from playwright.sync_api import TimeoutError as PwTimeout
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

# ordered fallbacks per field: first selector that yields text wins
CARD = ["div.product", "div.item-card", "[data-id]"]
FIELDS = {"name": [".title", ".item-name"], "price": [".price", ".item-price"]}


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str
    name: str
    price: float

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not v:
            raise ValueError("empty name")
        return v

    @field_validator("price")
    @classmethod
    def _price(cls, v):
        if v < 0:
            raise ValueError("negative price")
        return v


@dataclass
class Report:
    records: list[Product] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    pages_ok: int = 0
    pages_failed: list[int] = field(default_factory=list)
    retries: int = 0
    selector_fallbacks: int = 0
    robots_allowed: bool = True
    seconds: float = 0.0


def chromium_path() -> str | None:
    return (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome") + [None])[0]


def launch(p):
    exe = chromium_path()
    return (
        p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        if exe
        else p.chromium.launch()
    )


def _first_text(card, selectors, rep: Report) -> str | None:
    for i, s in enumerate(selectors):
        loc = card.locator(s)
        if loc.count():
            if i:
                rep.selector_fallbacks += 1
            return loc.first.inner_text()
    return None


def _cards(page, rep: Report):
    for i, s in enumerate(CARD):
        loc = page.locator(s)
        if loc.count():
            if i:
                rep.selector_fallbacks += 1
            return loc
    return None


def _load_page(page, url: str, rep: Report, max_retries: int):
    page.goto(url, wait_until="domcontentloaded")
    banner = page.locator("#accept")
    if banner.count():
        banner.click()
    for attempt in range(max_retries + 1):
        try:
            page.wait_for_selector(", ".join(CARD + [".error"]), timeout=3000)
        except PwTimeout:
            return None
        if page.locator(".error").count():  # transient API failure: click Retry with backoff
            if attempt == max_retries:
                return None
            rep.retries += 1
            time.sleep(0.05 * 2**attempt)
            page.locator("#retry").click()
            continue
        return _cards(page, rep)
    return None


def extract(
    base_url: str, *, resilient: bool = True, max_pages: int = 10, max_retries: int = 3
) -> Report:
    rep, t0 = Report(), time.perf_counter()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(
        __import__("urllib.request")
        .request.urlopen(urljoin(base_url, "/robots.txt"), timeout=3)
        .read()
        .decode()
        .splitlines()
    )
    rep.robots_allowed = rp.can_fetch("*", urljoin(base_url, "/products"))
    if not rep.robots_allowed:
        return rep
    seen: set[str] = set()
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_context().new_page()
        page.set_default_timeout(4000)
        page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff2}", lambda r: r.abort()
        )  # no needless downloads
        n, fails = 1, 0
        while n <= max_pages:
            url = urljoin(base_url, f"/products?page={n}")
            try:
                cards = (
                    _load_page(page, url, rep, max_retries) if resilient else _naive_load(page, url)
                )
            except PwError:
                cards = None
            if cards is None:
                rep.pages_failed.append(n)
                fails += 1
                if (
                    fails >= 2
                ):  # two dead pages in a row: assume the end rather than hammering the site
                    break
                n += 1
                continue
            fails = 0
            rep.pages_ok += 1
            for i in range(cards.count()):
                c = cards.nth(i)
                raw = {
                    "id": c.get_attribute("data-id"),
                    "name": _first_text(c, FIELDS["name"], rep),
                    "price": _first_text(c, FIELDS["price"], rep),
                }
                try:
                    rec = Product(id=raw["id"], name=raw["name"] or "", price=float(raw["price"]))
                except (ValidationError, ValueError, TypeError) as e:
                    msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
                    rep.quarantined.append({**raw, "error": msg[:80]})
                    continue
                if rec.id in seen:
                    rep.quarantined.append({**raw, "error": "duplicate id"})
                    continue
                seen.add(rec.id)
                rep.records.append(rec)
            if not page.locator("#next").count():
                break
            n += 1
        browser.close()
    rep.seconds = round(time.perf_counter() - t0, 2)
    return rep


def _naive_load(page, url):
    """Baseline: one fixed selector, no overlay handling, no retry. Loses whatever it cannot read."""
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("div.product", timeout=1500)
    except PwTimeout:
        return None
    return page.locator("div.product")
