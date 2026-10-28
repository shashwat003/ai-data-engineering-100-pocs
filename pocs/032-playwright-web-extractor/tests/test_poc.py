import pytest
from playwright_web_extractor.extract import Product, chromium_path, extract
from playwright_web_extractor.main import run
from playwright_web_extractor.site import PER_PAGE, Site, catalogue, valid_ids
from pydantic import ValidationError


def test_ground_truth_shape():
    rows = catalogue()
    assert len(rows) == 40 and len(valid_ids()) == 36
    assert "P005" not in valid_ids() and "P013" not in valid_ids() and "P022" not in valid_ids()
    assert PER_PAGE == 10


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "x", "name": "", "price": 1},
        {"id": "x", "name": "n", "price": -1},
        {"id": "x", "name": "n", "price": "abc"},
        {"id": "x", "name": "n", "price": 1, "extra": 1},
    ],
)
def test_product_validation(bad):
    with pytest.raises(ValidationError):
        Product(**bad)


def test_product_strips_and_coerces():
    p = Product(id=" a ", name="  Widget ", price="3.50")
    assert (p.id, p.name, p.price) == ("a", "Widget", 3.5)


def test_resilient_survives_flaky_api_drift_and_banner():
    with Site() as site:
        r = extract(site.url)
    assert {x.id for x in r.records} == valid_ids()
    assert r.retries >= 2 and r.selector_fallbacks > 0 and r.pages_ok == 4 and r.pages_failed == []
    assert {q["error"] for q in r.quarantined} >= {"duplicate id"}


def test_persistent_failure_is_recorded_not_crashing():
    with Site(always_fail=(2,)) as site:
        r = extract(site.url, max_retries=2)
    assert 2 in r.pages_failed and r.pages_ok >= 2
    assert all(
        int(x.id[1:]) <= 10 or int(x.id[1:]) > 20 for x in r.records
    )  # nothing from the dead page
    assert site.hits["api2"] == 3  # 1 try + 2 retries, then stop


def test_robots_disallow_is_respected():
    with Site(disallow_products=True) as site:
        r = extract(site.url)
        assert r.robots_allowed is False and r.records == []
        assert "/products" not in site.hits and "/api/products" not in site.hits


def test_naive_baseline_is_much_worse():
    r = run()
    assert r["resilient"]["recall"] == 1.0 and r["resilient"]["false_records"] == 0
    assert r["naive"]["recall"] < 0.5 < r["resilient"]["recall"]
    assert chromium_path() is None or chromium_path().startswith("/opt/pw-browsers")
