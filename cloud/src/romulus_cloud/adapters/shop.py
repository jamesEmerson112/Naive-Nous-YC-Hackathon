"""Shop facts for the Naïve Technician's part gate (design §4, §7): one public read per product.

`product(origin, handle)` reads a Shopify store's public `{origin}/products/{handle}.js`, which
gives `price` in **cents** (`1599`) and numeric variant ids. We normalize to
`{"title", "variants": [{"id": str, "title", "price_cents": int, "available": bool}]}`.
Match on the exact variant id: the WowRobo servo products also have 5-pack and 10-pack variants.
`FakeShop` serves the same shape from a dict, for tests.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

import httpx

HANDLE_RE = re.compile(r"^[a-z0-9-]+$")


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


def normalize_product(raw: Any) -> dict[str, Any]:
    """A Shopify product JSON (`/products/{handle}.js`) → `{title, variants: [...]}`."""
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
                "price_cents": _price_cents(v.get("price")),
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
    """GET `{origin}/products/{handle}.js` → the normalized product. Raises ShopError."""
    origin = origin.rstrip("/")
    if not origin.startswith("https://"):
        raise ShopError(f"origin {origin!r} must be https")
    if not HANDLE_RE.match(handle):
        raise ShopError(f"handle {handle!r} must match {HANDLE_RE.pattern}")
    url = f"{origin}/products/{handle}.js"
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
        return normalize_product(_urllib_json(url, timeout))
    if resp.status_code != 200:
        raise ShopError(f"GET {url} answered {resp.status_code}")
    try:
        raw = resp.json()
    except ValueError:
        raise ShopError(f"GET {url} answered non-JSON: {resp.text[:120]!r}") from None
    return normalize_product(raw)


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
