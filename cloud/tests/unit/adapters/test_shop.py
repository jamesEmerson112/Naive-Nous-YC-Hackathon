"""adapters/shop.py: the catalogue fallback (Shopify 429s `.js` from inside Docker), its
dollar-string prices, and the 10-minute cache. No network."""

import httpx
import pytest

from romulus_cloud.adapters import shop

WOWROBO = "https://shop.wowrobo.com"
HANDLE = "feetech-sts3215-servo-12v-30kg-high-torque-servo-for-so-arm100"
C018 = "48037575033049"
CATALOG = {
    "products": [
        {"handle": "something-else", "title": "Other", "variants": [{"id": 1, "price": "9.00"}]},
        {
            "handle": HANDLE,
            "title": "Feetech STS3215 C018 Servo – 12V 30KG",
            "variants": [
                {"id": 48037575033049, "title": "Single", "price": "15.99", "available": True},
                {"id": 48037575065817, "title": "5 Pieces", "price": "74.95", "available": True},
                {"id": 48037575098585, "title": "10 Pieces", "price": "140", "available": False},
            ],
        },
    ]
}


@pytest.fixture(autouse=True)
def _empty_cache():
    shop._cache.clear()
    yield
    shop._cache.clear()


def _client(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            request.url.path + ("?" + request.url.query.decode() if request.url.query else "")
        )
        for prefix, resp in routes.items():
            if request.url.path.startswith(prefix):
                return resp
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_429_on_js_falls_back_to_the_catalogue_with_dollar_prices():
    seen: list[str] = []
    client = _client(
        {
            f"/products/{HANDLE}.js": httpx.Response(429, headers={"Retry-After": "60"}),
            "/products.json": httpx.Response(200, json=CATALOG),
        },
        seen,
    )
    p = shop.product(WOWROBO, HANDLE, http_client=client)
    assert seen == [f"/products/{HANDLE}.js", "/products.json?limit=250"]
    assert p["title"].startswith("Feetech STS3215 C018")
    assert p["variants"][0] == {
        "id": C018,
        "title": "Single",
        "price_cents": 1599,
        "available": True,
    }
    assert [v["price_cents"] for v in p["variants"]] == [1599, 7495, 14000]  # "140" is dollars


def test_both_failing_says_both():
    client = _client({"/products": httpx.Response(429)}, [])
    with pytest.raises(shop.ShopError, match="catalogue fallback"):
        shop.product(WOWROBO, HANDLE, http_client=client)
    missing = _client(
        {
            f"/products/{HANDLE}.js": httpx.Response(500),
            "/products.json": httpx.Response(200, json={"products": []}),
        },
        [],
    )
    with pytest.raises(shop.ShopError, match="no product"):
        shop.product(WOWROBO, HANDLE, http_client=missing)


def test_dollar_prices_convert_exactly():
    assert shop._dollars_to_cents("15.99") == 1599
    assert shop._dollars_to_cents("15") == 1500
    assert shop._dollars_to_cents(15.5) == 1550
    with pytest.raises(shop.ShopError):
        shop._dollars_to_cents("free")


def test_reads_are_cached_for_ten_minutes(monkeypatch):
    calls: list[str] = []
    real_client = httpx.Client

    def fake_client(**kw):
        def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith(".js"):
                return httpx.Response(429)
            return httpx.Response(200, json=CATALOG)

        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(shop.httpx, "Client", fake_client)
    monkeypatch.setattr(
        shop, "_urllib_json", lambda url, timeout: (_ for _ in ()).throw(shop.ShopError("429"))
    )
    clock = [1000.0]
    monkeypatch.setattr(shop.time, "monotonic", lambda: clock[0])
    first = shop.product(WOWROBO, HANDLE)
    n = len(calls)
    clock[0] += 599
    assert shop.product(WOWROBO, HANDLE) == first and len(calls) == n  # served from cache
    first["variants"].clear()  # callers get copies
    assert shop.product(WOWROBO, HANDLE)["variants"]
    clock[0] += 2
    shop.product(WOWROBO, HANDLE)
    assert len(calls) > n  # stale after 10 minutes: read again
