"""Stage A — Product URL ingestion + Product Intelligence (PRD §4)."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from vidai.agent.run_context import PipelineContext
from vidai.agent.stages.base import Stage, StageError, call_module
from vidai.plan import ProductIntelligence

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
MAX_PAGE_TEXT = 9000
MAX_IMAGES = 3


class PageUnreachable(RuntimeError):
    """URL could not be read (network error, bot wall, JS-only page)."""


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _blank(url: str) -> dict[str, Any]:
    return {"url": url, "title": "", "brand": "", "price": None, "description": "",
            "bulletPoints": [], "attributes": {}, "reviews": [], "images": [], "pageText": "",
            "notes": [], "blocked": False}


def _load_html(url: str) -> tuple[str, str]:
    """Return (html, base_url). Local paths / file:// are allowed for offline runs."""
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        path = Path(parsed.path if parsed.scheme == "file" else url).expanduser()
        if not path.is_file():
            raise PageUnreachable(f"local page not found: {path}")
        return path.read_text(encoding="utf-8", errors="ignore"), path.resolve().as_uri()
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=40.0) as client:
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            raise PageUnreachable(f"request failed: {exc}") from exc
        if response.status_code in (401, 403, 429, 503):
            raise PageUnreachable(f"HTTP {response.status_code} — bot protection or login wall")
        if response.status_code >= 400:
            raise PageUnreachable(f"HTTP {response.status_code}")
        html = response.text
        if len(html) < 1500 and "<title" not in html.lower():
            raise PageUnreachable("page mostly JS / empty response; needs browser rendering")
        return html, str(response.url)


def _shopify_json(url: str, out: dict[str, Any]) -> None:
    match = re.search(r"(https?://[^/]+)(?:/[a-z]{2}(?:-[a-z]{2})?)?/products/([A-Za-z0-9\-_.%]+)", url)
    if not match:
        return
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as client:
            response = client.get(f"{match.group(1)}/products/{match.group(2)}.json")
        if response.status_code != 200 or "json" not in response.headers.get("content-type", ""):
            return
        product = response.json()["product"]
    except Exception:
        return
    out["title"] = out["title"] or _clean(product.get("title"))
    out["brand"] = out["brand"] or _clean(product.get("vendor"))
    out["description"] = out["description"] or _clean(BeautifulSoup(product.get("body_html") or "", "html.parser").get_text(" "))
    variants = product.get("variants") or []
    if variants and not out["price"]:
        out["price"] = str(variants[0].get("price"))
    out["attributes"]["variants"] = [v.get("title") for v in variants][:12]
    for image in product.get("images") or []:
        if image.get("src"):
            out["images"].append(image["src"])
    out["notes"].append("shopify products.json")


def _jsonld(soup: BeautifulSoup, out: dict[str, Any]) -> None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        items += [x for it in items if isinstance(it, dict) for x in it.get("@graph", [])]
        for item in items:
            if not isinstance(item, dict) or "Product" not in str(item.get("@type")):
                continue
            out["title"] = out["title"] or _clean(item.get("name"))
            out["description"] = out["description"] or _clean(BeautifulSoup(str(item.get("description") or ""), "html.parser").get_text(" "))
            brand = item.get("brand")
            out["brand"] = out["brand"] or _clean(brand.get("name") if isinstance(brand, dict) else brand)
            images = item.get("image") or []
            for image in [images] if isinstance(images, str) else images:
                image = image.get("url") if isinstance(image, dict) else image
                if image and image not in out["images"]:
                    out["images"].append(image)
            offers = item.get("offers")
            offers = offers[0] if isinstance(offers, list) and offers else offers
            if isinstance(offers, dict) and not out["price"]:
                price = offers.get("price") or offers.get("lowPrice")
                if price:
                    out["price"] = f"{price} {offers.get('priceCurrency') or ''}".strip()
            rating = item.get("aggregateRating")
            if isinstance(rating, dict) and rating.get("ratingValue"):
                out["reviews"].append(f"rating {rating.get('ratingValue')} from "
                                      f"{rating.get('reviewCount') or rating.get('ratingCount')} reviews")
            out["notes"].append("json-ld Product")


def _meta(soup: BeautifulSoup, out: dict[str, Any]) -> None:
    og = {m.get("property"): m.get("content") for m in soup.find_all("meta", property=True)}
    out["title"] = out["title"] or _clean(og.get("og:title")) or (_clean(soup.title.get_text()) if soup.title else "")
    desc = soup.find("meta", attrs={"name": "description"})
    out["description"] = out["description"] or _clean(og.get("og:description")) or _clean(desc.get("content") if desc else "")
    if og.get("og:image") and og["og:image"] not in out["images"]:
        out["images"].append(og["og:image"])
    if not out["price"]:
        amount = og.get("og:price:amount") or og.get("product:price:amount")
        if not amount:
            node = soup.find(attrs={"itemprop": "price"})
            amount = node.get("content") if node else None
        if not amount:
            match = re.search(r"(?:\$|€|£|¥)\s?\d[\d,]*(?:\.\d{2})?", soup.get_text(" "))
            amount = match.group(0) if match else None
        out["price"] = amount


def _img_tags(soup: BeautifulSoup, base_url: str, out: dict[str, Any]) -> None:
    skip = re.compile(r"(logo|icon|sprite|badge|flag|payment|pixel|tracking|\.svg|\.gif|1x1|avatar)", re.I)
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src.startswith("data:") or skip.search(src):
            continue
        url = urljoin(base_url, src.strip())
        if url not in out["images"]:
            out["images"].append(url)
        if len(out["images"]) >= 8:
            break


def _text_blocks(soup: BeautifulSoup, out: dict[str, Any]) -> None:
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    bullets = [_clean(li.get_text(" ")) for li in soup.find_all("li")]
    out["bulletPoints"] = [b for b in bullets if 12 <= len(b) <= 220][:25]
    for row in soup.find_all("tr"):
        cells = [_clean(c.get_text(" ")) for c in row.find_all(["th", "td"])]
        if len(cells) == 2 and cells[0] and len(cells[1]) < 120:
            out["attributes"][cells[0][:60]] = cells[1]
    out["pageText"] = _clean(soup.get_text(" "))[:MAX_PAGE_TEXT]


def _download_images(images: list[str], base_url: str, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    parsed_base = urlparse(base_url)
    for index, url in enumerate(images[:MAX_IMAGES]):
        try:
            parsed = urlparse(url)
            if parsed.scheme == "file" or (parsed_base.scheme == "file" and parsed.scheme == ""):
                src = Path(parsed.path if parsed.scheme == "file" else urljoin(parsed_base.path, url))
                if src.is_file():
                    dest = out_dir / f"image_{index:02d}{src.suffix or '.jpg'}"
                    shutil.copy2(src, dest)
                    saved.append(str(dest))
                continue
            with httpx.Client(headers={"User-Agent": UA, "Referer": base_url}, follow_redirects=True, timeout=40.0) as client:
                response = client.get(url)
            ctype = response.headers.get("content-type", "")
            if response.status_code != 200 or not ctype.startswith("image/"):
                continue
            ext = mimetypes.guess_extension(ctype.split(";")[0]) or ".jpg"
            dest = out_dir / f"image_{index:02d}{ext}"
            dest.write_bytes(response.content)
            saved.append(str(dest))
        except Exception as exc:  # one dead CDN link must not kill the run
            logger.debug("image download failed %s: %s", url, exc)
    return saved


def scrape_product_page(url: str, out_dir: Path) -> dict[str, Any]:
    """Deterministic parser (PRD §17: 'Mostly deterministic'). Raises PageUnreachable."""
    html, base_url = _load_html(url)
    out = _blank(url)
    soup = BeautifulSoup(html, "html.parser")
    if urlparse(base_url).scheme.startswith("http"):
        _shopify_json(base_url, out)
    _jsonld(soup, out)
    _meta(soup, out)
    if len(out["images"]) < 2:
        _img_tags(soup, base_url, out)
    _text_blocks(soup, out)
    out["localImages"] = _download_images(out["images"], base_url, out_dir / "images")
    (out_dir / "scraped.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


class ProductParserStage(Stage):
    name = "product_parser"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        scraped: dict[str, Any] | None = None
        try:
            scraped = await asyncio.to_thread(scrape_product_page, plan.productUrl, ctx.product_dir)
        except PageUnreachable as exc:
            ctx.trace("page_unreachable", error=str(exc))
            if not plan.productTextFallback:
                raise StageError(
                    f"product page could not be read ({exc}). Paste the product description with "
                    "--product-text (and optionally --product-image) to continue."
                ) from exc
            scraped = _blank(plan.productUrl)
            scraped["notes"].append(f"page unreachable ({exc}); using user-provided description")
        if plan.productTextFallback:
            scraped["pageText"] = (scraped.get("pageText", "") + "\n" + plan.productTextFallback).strip()[:MAX_PAGE_TEXT]
            scraped["notes"].append("user-provided product text appended")
        if len(scraped.get("pageText", "")) < 200 and not scraped.get("description"):
            plan.warnings.append("Very little product data on the page; product intelligence is low confidence.")

        payload = {"url": plan.productUrl, "scraped": {k: v for k, v in scraped.items() if k != "localImages"}}
        product = await call_module(ctx, "product_parser", payload, model=ProductIntelligence)
        product.sourceUrl = plan.productUrl
        product.visualAssets = list(scraped.get("localImages", []))
        if plan.userOverrides.get("productImage"):
            product.visualAssets.insert(0, plan.userOverrides["productImage"])
        product.heroImagePath = product.visualAssets[0] if product.visualAssets else None
        if not product.productName:
            product.productName = scraped.get("title") or "the product"
        if not product.brandName:
            product.brandName = scraped.get("brand") or ""
        if product.price is None and scraped.get("price"):
            product.price = str(scraped["price"])
        if product.confidence < 0.4:
            plan.warnings.append(
                f"Low-confidence product intelligence ({product.confidence:.2f}): "
                + "; ".join(product.sourceNotes[:3])
            )
        if product.multipleProductsOnPage:
            plan.warnings.append("Several products detected on the page; the primary one was used. "
                                 "Pass a more specific URL if this is wrong.")
        plan.product = product
