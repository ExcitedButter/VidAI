#!/usr/bin/env python3
"""Extract product info + images from a TikTok Shop PDP or a brand/official product page.

Usage:
    python web/extract_product.py <url> [--out DIR] [--max-images N] [--no-images]

Writes <out>/product.json (unified schema) and downloads images to <out>/images/ (default out: web/results/<slug>).

TikTok Shop: parses the server-rendered `__MODERN_ROUTER_DATA__` JSON (components_map ->
product_info): title, price, gallery, description blocks (+images), SKUs, attributes,
seller/shop, rating + review sample, shipping, categories. Falls back to og:* meta.
Official sites: Shopify `/products/<handle>.json` -> JSON-LD Product -> og:* / itemprop
-> <img> gallery heuristics. Bot-protected pages (403 / empty shells) are reported, not faked.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import mimetypes
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ----------------------------------------------------------------------------- helpers
def fetch(client: httpx.Client, url: str) -> httpx.Response:
    return client.get(url, headers=HEADERS, follow_redirects=True, timeout=40.0)


def clean_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(s or "")).strip()


def html_to_text(fragment: str | None) -> str:
    if not fragment:
        return ""
    return clean_text(BeautifulSoup(fragment, "html.parser").get_text(" "))


def first_url(obj: Any) -> str | None:
    """TikTok image objects carry url_list; take the first reachable CDN URL."""
    if isinstance(obj, dict):
        lst = obj.get("url_list") or []
        return lst[0] if lst else obj.get("url")
    if isinstance(obj, str):
        return obj
    return None


def blank_product(url: str, platform: str) -> dict[str, Any]:
    return {
        "platform": platform,
        "url": url,
        "title": None,
        "brand": None,
        "price": None,
        "description": "",
        "bullet_points": [],
        "images": [],
        "videos": [],
        "seller": {},
        "rating": {},
        "sold_count": None,
        "skus": [],
        "attributes": {},
        "shipping": {},
        "categories": [],
        "blocked": False,
        "notes": [],
    }


# ------------------------------------------------------------------------- TikTok Shop
def is_tiktok_shop(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "tiktok.com" in host and ("/pdp/" in url or "/product/" in url or host.startswith("vt."))


def extract_router_data(raw: str) -> dict[str, Any] | None:
    m = re.search(
        r'<script type="application/json" id="__MODERN_ROUTER_DATA__">(.*?)</script>', raw, re.S
    )
    if not m:
        return None
    try:
        return json.loads(html_mod.unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def extract_tiktok(raw: str, url: str) -> dict[str, Any]:
    out = blank_product(url, "tiktok_shop")
    soup = BeautifulSoup(raw, "html.parser")
    og = {m.get("property"): m.get("content") for m in soup.find_all("meta", property=True)}
    out["title"] = clean_text(og.get("og:title"))
    out["description"] = clean_text(og.get("og:description"))
    if og.get("og:image"):
        out["images"].append({"url": og["og:image"], "kind": "og", "width": None, "height": None})

    data = extract_router_data(raw)
    page = None
    if data:
        page = next(
            (v for v in (data.get("loaderData") or {}).values() if isinstance(v, dict) and "page_config" in v),
            None,
        )
    if not page:
        out["notes"].append("no __MODERN_ROUTER_DATA__ product payload; og:* fallback only")
        out["blocked"] = not out["title"]
        return out

    comps = {c.get("component_name"): c.get("component_data") for c in page["page_config"].get("components_map", [])}
    pinfo = comps.get("product_info") or {}
    core = (pinfo.get("product_info") or {})
    pm = core.get("product_model") or {}
    if not pm.get("name"):
        out["notes"].append("product_model empty (bot-gated response); og:* fallback only")
        return out

    # --- identity / basics
    out["product_id"] = pm.get("product_id")
    out["title"] = clean_text(pm.get("name")) or out["title"]
    out["sold_count"] = pm.get("sold_count")
    out["categories"] = [c.get("category_name") for c in pinfo.get("categories") or [] if c.get("category_name")]

    # --- price
    promo = (core.get("promotion_model") or {}).get("promotion_product_price") or {}
    mp = promo.get("min_price") or {}
    if mp:
        out["price"] = {
            "amount": mp.get("sale_price_decimal"),
            "currency": mp.get("currency_name"),
            "symbol": mp.get("currency_symbol"),
            "display": f"{mp.get('currency_symbol', '')}{mp.get('sale_price_format', '')}",
        }
    sku_prices = promo.get("skus_price") or {}

    # --- gallery images (url_list[0] is the CDN variant the page itself uses)
    out["images"] = []
    for i, img in enumerate(pm.get("images") or []):
        u = first_url(img)
        if u:
            out["images"].append({"url": u, "kind": "gallery", "index": i, "width": img.get("width"), "height": img.get("height"), "uri": img.get("uri")})

    # --- description blocks (JSON string: text + image blocks)
    desc = pm.get("description")
    if isinstance(desc, str):
        try:
            desc = json.loads(desc)
        except json.JSONDecodeError:
            desc = [{"type": "text", "text": desc}]
    paragraphs: list[str] = []
    for j, block in enumerate(desc or []):
        if block.get("type") == "text" and block.get("text"):
            paragraphs.append(clean_text(block["text"]))
        elif block.get("type") == "image":
            u = first_url(block.get("image"))
            if u:
                img = block["image"]
                out["images"].append({"url": u, "kind": "description", "index": j, "width": img.get("width"), "height": img.get("height"), "uri": img.get("uri")})
    if paragraphs:
        out["description"] = "\n".join(paragraphs)

    # --- videos (product videos, when present)
    vids = pm.get("videos") or {}
    for v in (vids.values() if isinstance(vids, dict) else vids):
        u = first_url(v if isinstance(v, dict) else {}) or (v.get("play_addr", {}).get("url_list") or [None])[0] if isinstance(v, dict) else None
        if u:
            out["videos"].append({"url": u})

    # --- SKUs
    for sku in pm.get("skus") or []:
        sp = sku_prices.get(str(sku.get("sku_id"))) or {}
        out["skus"].append({
            "sku_id": sku.get("sku_id"),
            "name": sku.get("sku_name"),
            "options": {p.get("sku_property_name"): p.get("sku_property_value_name") for p in sku.get("property_pairs") or []},
            "price": sp.get("sale_price_decimal"),
            "currency": sp.get("currency_name"),
            "available_quantity": (sku.get("sku_quantity") or {}).get("available_quantity"),
            "in_stock": sku.get("sku_stock_status") == 1,
            "gtin": (sku.get("gtin") or {}).get("gtin_code"),
            "weight": sku.get("weight"),
            "dimension": sku.get("dimension"),
        })

    # --- attributes (product + sale properties)
    attrs: dict[str, Any] = {}
    for prop in (pm.get("product_properties") or []) + (pm.get("sale_properties") or []):
        name = prop.get("property_name")
        vals = [v.get("property_value_name") for v in prop.get("property_values") or [] if v.get("property_value_name")]
        if name and vals:
            attrs[name] = vals if len(vals) > 1 else vals[0]
    out["attributes"] = attrs
    brand_key = next((k for k in attrs if k.lower() == "brand"), None)
    out["brand"] = attrs.get(brand_key) if brand_key else None

    # --- rating & reviews
    rm = core.get("review_model") or {}
    rinfo = pinfo.get("review_info") or {}
    out["rating"] = {
        "score": rm.get("product_overall_score") or (rinfo.get("review_ratings") or {}).get("overall_score"),
        "count": rm.get("product_review_count") or rinfo.get("total_reviews"),
        "distribution": (rinfo.get("review_ratings") or {}).get("rating_result"),
        "sample_reviews": [
            {
                "rating": r.get("review_rating"),
                "text": clean_text(r.get("review_text")),
                "reviewer": r.get("reviewer_name"),
                "verified": r.get("is_verified_purchase"),
                "images": [first_url(x) for x in r.get("review_images") or [] if first_url(x)],
            }
            for r in rinfo.get("product_reviews") or []
        ],
    }

    # --- seller / shop
    sm = core.get("seller_model") or {}
    shop = comps.get("shop_info") or pinfo.get("shop_info") or {}
    comp = (core.get("safety_model") or {}).get("business_compliance_info") or {}
    out["seller"] = {
        "seller_id": pm.get("seller_id"),
        "shop_name": sm.get("shop_name") or shop.get("shop_name"),
        "shop_link": shop.get("shop_link"),
        "shop_rating": shop.get("shop_rating"),
        "followers": shop.get("followers_count"),
        "shop_sold_count": shop.get("sold_count"),
        "official": (shop.get("shop_identity_label") or {}).get("identity_label_text"),
        "logo": first_url(sm.get("shop_logo") or shop.get("shop_logo")),
        "business_name": comp.get("business_name"),
        "business_address": comp.get("business_address"),
    }
    if not out["brand"] and out["seller"].get("business_name"):
        out["brand"] = out["seller"]["shop_name"]

    # --- shipping
    pkgs = (core.get("logistic_model") or {}).get("pkg_of_service") or {}
    if pkgs:
        p0 = next(iter(pkgs.values()))
        out["shipping"] = {
            "delivery_min_days": p0.get("delivery_min_days"),
            "delivery_max_days": p0.get("delivery_max_days"),
            "shipping_fee": p0.get("shipping_fee"),
            "currency": p0.get("currency"),
        }

    # --- AI marketing copy the page ships (bullets / FAQ)
    aigc = pinfo.get("aigc_info") or {}
    if aigc.get("bullet_points"):
        out["bullet_points"] = [clean_text(b) for b in re.split(r"\n|•", aigc["bullet_points"]) if clean_text(b)]
    if aigc.get("faq"):
        out["faq"] = clean_text(aigc["faq"])
    return out


# ----------------------------------------------------------------------- official sites
def extract_shopify_json(client: httpx.Client, url: str, out: dict[str, Any]) -> bool:
    m = re.search(r"(https?://[^/]+)(?:/[a-z]{2}(?:-[a-z]{2})?)?/products/([A-Za-z0-9\-_.%]+)", url)
    if not m:
        return False
    try:
        r = client.get(f"{m.group(1)}/products/{m.group(2)}.json", headers=HEADERS, follow_redirects=True, timeout=30.0)
        if r.status_code != 200 or "application/json" not in r.headers.get("content-type", ""):
            return False
        p = r.json()["product"]
    except Exception:
        return False
    out["title"] = out["title"] or p.get("title")
    out["brand"] = out["brand"] or p.get("vendor")
    out["description"] = out["description"] or html_to_text(p.get("body_html"))
    variants = p.get("variants") or []
    if variants and not out["price"]:
        out["price"] = {"amount": variants[0].get("price"), "currency": None, "display": variants[0].get("price")}
    out["skus"] = [
        {"sku_id": v.get("id"), "name": v.get("title"), "price": v.get("price"), "sku": v.get("sku"), "in_stock": v.get("available")}
        for v in variants
    ]
    for i, img in enumerate(p.get("images") or []):
        out["images"].append({"url": img.get("src"), "kind": "gallery", "index": i, "width": img.get("width"), "height": img.get("height")})
    out["notes"].append("shopify products.json")
    return True


def extract_jsonld(soup: BeautifulSoup, out: dict[str, Any]) -> None:
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
        except Exception:
            continue
        items = d if isinstance(d, list) else [d]
        items += [x for it in items if isinstance(it, dict) for x in it.get("@graph", [])]
        for it in items:
            if not isinstance(it, dict) or "Product" not in str(it.get("@type")):
                continue
            out["title"] = out["title"] or clean_text(it.get("name"))
            out["description"] = out["description"] or html_to_text(it.get("description"))
            brand = it.get("brand")
            out["brand"] = out["brand"] or (brand.get("name") if isinstance(brand, dict) else brand)
            out["sku"] = out.get("sku") or it.get("sku")
            imgs = it.get("image") or []
            for u in ([imgs] if isinstance(imgs, str) else imgs):
                u = u.get("url") if isinstance(u, dict) else u
                if u and all(u != x["url"] for x in out["images"]):
                    out["images"].append({"url": u, "kind": "gallery", "width": None, "height": None})
            offers = it.get("offers")
            offers = offers[0] if isinstance(offers, list) and offers else offers
            if isinstance(offers, dict) and not out["price"]:
                price = offers.get("price") or offers.get("lowPrice")
                if price:
                    out["price"] = {"amount": str(price), "currency": offers.get("priceCurrency"), "display": f"{price} {offers.get('priceCurrency') or ''}".strip(), "availability": offers.get("availability")}
            rating = it.get("aggregateRating")
            if isinstance(rating, dict):
                out["rating"] = {"score": rating.get("ratingValue"), "count": rating.get("reviewCount") or rating.get("ratingCount")}
            out["notes"].append("json-ld Product")


def extract_meta(soup: BeautifulSoup, out: dict[str, Any]) -> None:
    og = {m.get("property"): m.get("content") for m in soup.find_all("meta", property=True)}
    out["title"] = out["title"] or clean_text(og.get("og:title")) or (clean_text(soup.title.get_text()) if soup.title else None)
    out["description"] = out["description"] or clean_text(og.get("og:description")) or clean_text((soup.find("meta", attrs={"name": "description"}) or {}).get("content"))
    if og.get("og:image") and all(og["og:image"] != x["url"] for x in out["images"]):
        out["images"].append({"url": og["og:image"], "kind": "og", "width": og.get("og:image:width"), "height": og.get("og:image:height")})
    if not out["price"]:
        amount = og.get("og:price:amount") or og.get("product:price:amount")
        if not amount:
            ip = soup.find(attrs={"itemprop": "price"})
            amount = ip.get("content") if ip else None
        if amount:
            out["price"] = {"amount": amount, "currency": og.get("og:price:currency") or og.get("product:price:currency"), "display": amount}
    if not out["price"]:
        m = re.search(r"(?:\$|€|£|¥)\s?\d[\d,]*(?:\.\d{2})?", soup.get_text(" "))
        if m:
            out["price"] = {"amount": m.group(0), "currency": None, "display": m.group(0), "note": "regex from page text"}


def extract_img_tags(soup: BeautifulSoup, base: str, out: dict[str, Any], limit: int) -> None:
    seen = {x["url"] for x in out["images"]}
    skip = re.compile(r"(logo|icon|sprite|badge|flag|payment|pixel|tracking|\.svg|\.gif|1x1|avatar)", re.I)
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[-1].strip().split(" ")[0]
        if not src:
            continue
        u = urljoin(base, src.strip())
        if u in seen or skip.search(u) or u.startswith("data:"):
            continue
        w = img.get("width")
        if w and str(w).isdigit() and int(w) < 200:
            continue
        seen.add(u)
        out["images"].append({"url": u, "kind": "img-tag", "alt": clean_text(img.get("alt")), "width": w, "height": img.get("height")})
        if len(out["images"]) >= limit:
            break


def extract_generic(client: httpx.Client, url: str, max_images: int) -> dict[str, Any]:
    out = blank_product(url, "web")
    r = fetch(client, url)
    out["final_url"] = str(r.url)
    if r.status_code in (401, 403, 429, 503) or len(r.text) < 1500:
        out["blocked"] = True
        out["notes"].append(f"HTTP {r.status_code}, {len(r.text)} bytes — bot protection / JS-only page; needs a real browser (Playwright)")
    soup = BeautifulSoup(r.text, "html.parser")
    extract_shopify_json(client, str(r.url), out)
    extract_jsonld(soup, out)
    extract_meta(soup, out)
    if len(out["images"]) < 3:
        extract_img_tags(soup, str(r.url), out, max_images)
    if out["title"] and out["blocked"] and len(r.text) >= 1500:
        out["blocked"] = False
    return out


# --------------------------------------------------------------------------- downloads
def download_images(client: httpx.Client, out: dict[str, Any], out_dir: Path, max_images: int) -> None:
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    counters: dict[str, int] = {}
    done = 0
    for img in out["images"]:
        if done >= max_images:
            break
        kind = img.get("kind", "img")
        counters[kind] = counters.get(kind, 0) + 1
        try:
            r = client.get(img["url"], headers={"User-Agent": UA, "Referer": out["url"]}, follow_redirects=True, timeout=40.0)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                img["local"] = None
                img["error"] = f"HTTP {r.status_code} {r.headers.get('content-type', '')}"
                continue
            ext = mimetypes.guess_extension(r.headers["content-type"].split(";")[0]) or ".img"
            path = img_dir / f"{kind}_{counters[kind]:02d}{ext}"
            path.write_bytes(r.content)
            img["local"] = str(path)
            img["bytes"] = len(r.content)
            done += 1
        except Exception as exc:  # keep going; one dead CDN link must not kill the run
            img["local"] = None
            img["error"] = str(exc)[:120]


# -------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", default=None, help="output dir (default: data/products/<slug>)")
    ap.add_argument("--max-images", type=int, default=30)
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    with httpx.Client() as client:
        if is_tiktok_shop(args.url):
            r = fetch(client, args.url)
            product = extract_tiktok(r.text, args.url)
            product["final_url"] = str(r.url)
        else:
            product = extract_generic(client, args.url, args.max_images)

        slug = re.sub(r"[^a-z0-9]+", "-", (product.get("title") or "product").lower())[:60].strip("-")
        out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent / "results" / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        if not args.no_images:
            download_images(client, product, out_dir, args.max_images)

    (out_dir / "product.json").write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"platform : {product['platform']}{'  [BLOCKED]' if product.get('blocked') else ''}")
    print(f"title    : {product.get('title')}")
    print(f"brand    : {product.get('brand')}   price: {(product.get('price') or {}).get('display')}")
    if product.get("rating"):
        print(f"rating   : {product['rating'].get('score')} ({product['rating'].get('count')} reviews)   sold: {product.get('sold_count')}")
    if product.get("seller"):
        print(f"seller   : {product['seller'].get('shop_name')}  {product['seller'].get('official') or ''}")
    saved = sum(1 for i in product["images"] if i.get("local"))
    print(f"images   : {len(product['images'])} found, {saved} downloaded -> {out_dir / 'images'}")
    print(f"desc     : {(product.get('description') or '')[:160]}")
    for n in product.get("notes", []):
        print(f"note     : {n}")
    print(f"json     : {out_dir / 'product.json'}")
    return 2 if product.get("blocked") else 0


if __name__ == "__main__":
    sys.exit(main())
