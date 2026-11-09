"""Shop facts for the Naïve Technician's part gate (design §4, §7): one public read per product.

`product(origin, handle)` reads a Shopify store's public `{origin}/products/{handle}.js`, which
gives `price` in **cents** (`1599`) and numeric variant ids. We normalize to
`{"title", "variants": [{"id": str, "title", "price_cents": int, "available": bool}]}`.
Match on the exact variant id: the WowRobo servo products also have 5-pack and 10-pack variants.
`FakeShop` serves the same shape from a dict, for tests.

**Fallback (seen live Nov 8):** from inside Docker, Shopify answered `.js` with 429 (Retry-After
60) for minutes while `{origin}/products.json?limit=250` answered 200. So on any `.js` failure
we read the catalogue and pick the product by `handle`; it gives `price` as a **dollar string**
("15.99"), converted to cents. Successful reads are cached per (origin, handle) for
`CACHE_S` (10 min) in this process, so sync ticks never hammer the store.
"""

from __future__ import annotations

import copy
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

import httpx

HANDLE_RE = re.compile(r"^[a-z0-9-]+$")
CACHE_S = 600.0
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


class ShopError(Exception):
    """The shop read failed or answered something we can't use."""


def _price_cents(value: Any) -> int:
    # `.js` gives int cents; `products.json` gives a "15.99" string. Accept both.
    if isinstance(value, bool):
        raise ShopError(f"price {value!r} is not a number")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value * 100) if not value.is_integer() else int(value)
    if isinstance(value, str):
        try:
            return round(float(value) * 100) if "." in value else int(value)
        except ValueError:
            raise ShopError(f"price {value!r} is not a number") from None
    raise ShopError(f"price {value!r} is not a number")


def _dollars_to_cents(value: Any) -> int:
    """`products.json` prices are dollar strings ("15.99", "15"): always dollars."""
    if isinstance(value, bool) or value is None:
        raise ShopError(f"price {value!r} is not a number")
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        raise ShopError(f"price {value!r} is not a number") from None


def normalize_product(raw: Any, *, dollars: bool = False) -> dict[str, Any]:
    """A Shopify product JSON → `{title, variants: [...]}`. `.js` prices are cents;
    `dollars=True` for a `products.json` entry, whose prices are dollar strings."""
    if not isinstance(raw, dict) or not isinstance(raw.get("variants"), list):
        raise ShopError("product JSON has no variants list")
    variants = []
    for v in raw["variants"]:
        if not isinstance(v, dict) or v.get("id") is None:
            continue
        variants.append(
            {
                "id": str(v["id"]),
                "title": str(v.get("title") or ""),
                "price_cents": (_dollars_to_cents if dollars else _price_cents)(v.get("price")),
                "available": bool(v.get("available")),
            }
        )
    return {"title": str(raw.get("title") or ""), "variants": variants}


def product(
    origin: str,
    handle: str,
    *,
    timeout: float = 5.0,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """The normalized product: `{origin}/products/{handle}.js`, else the catalogue
    (`/products.json?limit=250`) by handle. Cached CACHE_S when no client is injected.
    Raises ShopError when both fail."""
    origin = origin.rstrip("/")
    if not origin.startswith("https://"):
        raise ShopError(f"origin {origin!r} must be https")
    if not HANDLE_RE.match(handle):
        raise ShopError(f"handle {handle!r} must match {HANDLE_RE.pattern}")
    key = (origin, handle)
    hit = _cache.get(key) if http_client is None else None
    if hit is not None and time.monotonic() - hit[0] < CACHE_S:
        return copy.deepcopy(hit[1])
    try:
        found = normalize_product(_get_json(f"{origin}/products/{handle}.js", timeout, http_client))
    except ShopError as first:
        try:
            found = _from_catalog(origin, handle, timeout, http_client)
        except ShopError as second:
            raise ShopError(f"{first}; catalogue fallback: {second}") from None
    if http_client is None:
        _cache[key] = (time.monotonic(), found)
    return copy.deepcopy(found)


def _from_catalog(
    origin: str, handle: str, timeout: float, http_client: httpx.Client | None
) -> dict[str, Any]:
    url = f"{origin}/products.json?limit=250"
    raw = _get_json(url, timeout, http_client)
    products = raw.get("products") if isinstance(raw, dict) else None
    if not isinstance(products, list):
        raise ShopError(f"GET {url} has no products list")
    for p in products:
        if isinstance(p, dict) and p.get("handle") == handle:
            return normalize_product(p, dollars=True)
    raise ShopError(f"no product {handle!r} in {url}")


def _get_json(url: str, timeout: float, http_client: httpx.Client | None) -> Any:
    client = http_client or httpx.Client(follow_redirects=True)
    try:
        resp = client.get(url, timeout=timeout, headers=HEADERS)
    except httpx.HTTPError as exc:
        raise ShopError(f"GET {url} failed: {exc}") from exc
    finally:
        if http_client is None:
            client.close()
    if resp.status_code == 429 and http_client is None:
        # Seen live Nov 8: WowRobo's edge answered httpx 429 (Retry-After 60) while a stdlib
        # urllib GET of the same URL, same headers, got 200. One fallback read, same timeout.
        return _urllib_json(url, timeout)
    if resp.status_code != 200:
        raise ShopError(f"GET {url} answered {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise ShopError(f"GET {url} answered non-JSON: {resp.text[:120]!r}") from None


HEADERS = {"Accept": "application/json", "User-Agent": "romulus-cloud/0.1"}


def _urllib_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ShopError(f"GET {url} answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ShopError(f"GET {url} failed: {exc}") from exc


class FakeShop:
    """Products from a dict keyed by `(origin, handle)`; values may be raw Shopify JSON or
    already normalized. Records each call in `self.calls`."""

    def __init__(self, products: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.products = {(o.rstrip("/"), h): p for (o, h), p in products.items()}
        self.calls: list[tuple[str, str]] = []

    def product(self, origin: str, handle: str, *, timeout: float = 5.0) -> dict[str, Any]:
        key = (origin.rstrip("/"), handle)
        self.calls.append(key)
        if key not in self.products:
            raise ShopError(f"fake shop: no product {handle!r} at {origin}")
        p = self.products[key]
        if all("price_cents" in v for v in p.get("variants", [])):
            return {"title": p.get("title", ""), "variants": [dict(v) for v in p["variants"]]}
        return normalize_product(p)
