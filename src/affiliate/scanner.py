"""
TikTok Farm - Affiliate Scanner Module
Crawls TikTok Affiliate Marketplace to find trending products with high commissions.

Uses Playwright for crawling (no official API needed).
Falls back to web_search/web_extract if direct crawl fails.
"""

import json
import logging
import asyncio
import random
from typing import Optional, List, Dict
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

# Try imports with fallbacks
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed. Scanner will use web fallback.")

# ---- Data Models ----


@dataclass
class AffiliateProduct:
    """A TikTok Shop product with affiliate details."""
    sp_id: str = ""
    name: str = ""
    price: float = 0.0
    commission_pct: float = 0.0
    commission_amount: float = 0.0
    sales_count: int = 0
    rating: float = 0.0
    shop_name: str = ""
    shop_id: str = ""
    category: str = ""
    video_urls: List[str] = field(default_factory=list)
    image_url: str = ""
    affiliate_link: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Scanner ----

DEFAULT_SETTINGS = {
    "commission_min_pct": 10,
    "trending_refresh_hours": 6,
}

# TikTok Shop URLs
TIKTOK_SHOP_BASE = "https://www.tiktok.com/shop"
TIKTOK_SHOP_API = "https://www.tiktok.com/api/shop"
TIKTOK_AFFILIATE_URL = "https://affiliate.tiktok.com"


class AffiliateScanner:
    """
    Scans TikTok Affiliate Marketplace for trending/spiking products.

    2 modes:
    1. Playwright crawl (direct, more data)
    2. Web search fallback (indirect, less data but works when blocked)
    """

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or DEFAULT_SETTINGS
        self._browser = None
        self._context = None

    # ---- Public API ----

    async def scan_trending(self, limit: int = 20, category: str = "") -> List[Dict]:
        """Scan trending products from TikTok Affiliate Marketplace."""
        if PLAYWRIGHT_AVAILABLE:
            try:
                products = await self._crawl_trending(limit, category)
                if products:
                    return products
            except Exception as e:
                logger.warning(f"Playwright crawl failed, falling back: {e}")

        # Fallback: web search
        return await self._web_search_fallback(limit, category)

    async def filter_by_commission(self, products: List[Dict], min_pct: float = 10.0) -> List[Dict]:
        """Filter products with commission >= min_pct."""
        return [p for p in products if p.get("commission_pct", 0) >= min_pct]

    async def search_by_keyword(self, keyword: str, limit: int = 10) -> List[Dict]:
        """Search products by keyword on TikTok Shop."""
        if PLAYWRIGHT_AVAILABLE:
            try:
                return await self._crawl_search(keyword, limit)
            except Exception as e:
                logger.warning(f"Playwright search failed: {e}")

        return await self._web_search_fallback(limit, keyword=keyword)

    async def close(self):
        """Clean up browser resources."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass

    # ---- Playwright Crawl Methods ----

    async def _ensure_browser(self):
        """Lazy-init a headless browser for crawling."""
        if self._browser and self._context:
            return
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        p = await async_playwright().start()
        self._browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )

    async def _crawl_trending(self, limit: int = 20, category: str = "") -> List[Dict]:
        """
        Crawl TikTok shop trending products.
        TikTok Shop uses SPA — need to wait for dynamic content.
        """
        await self._ensure_browser()
        page = await self._context.new_page()

        products = []
        try:
            # Try multiple URLs
            urls_to_try = [
                f"{TIKTOK_SHOP_BASE}/trending",
                f"{TIKTOK_SHOP_BASE}",
                "https://shop.tiktok.com/trending",
            ]
            if category:
                urls_to_try.insert(0, f"{TIKTOK_SHOP_BASE}/category/{category}")

            for url in urls_to_try:
                try:
                    logger.info(f"Crawling TikTok Shop: {url}")
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)  # Wait for dynamic content

                    # Try to extract product cards
                    products = await self._extract_products_from_page(page, limit)
                    if products:
                        logger.info(f"Found {len(products)} products from {url}")
                        break
                except Exception as e:
                    logger.debug(f"URL {url} failed: {e}")
                    continue

        finally:
            await page.close()

        return products

    async def _crawl_search(self, keyword: str, limit: int = 10) -> List[Dict]:
        """Search TikTok Shop for products matching keyword."""
        await self._ensure_browser()
        page = await self._context.new_page()

        products = []
        try:
            search_url = f"{TIKTOK_SHOP_BASE}/search?keyword={keyword}"
            logger.info(f"Searching TikTok Shop: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            products = await self._extract_products_from_page(page, limit)
        except Exception as e:
            logger.warning(f"Search crawl failed: {e}")
        finally:
            await page.close()

        return products

    async def _extract_products_from_page(self, page, limit: int) -> List[Dict]:
        """
        Extract product cards from current page.
        Uses multiple selector strategies since TikTok DOM changes frequently.
        """
        products = []

        extract_script = """
        () => {
            const results = [];

            // Strategy 1: Try common TikTok Shop product card selectors
            const selectors = [
                '[class*="product-card"]',
                '[class*="ProductCard"]',
                '[class*="product-item"]',
                '[class*="ProductItem"]',
                '[data-e2e="product-card"]',
                'a[href*="/product/"]',
                '[class*="card-container"] a[href*="/product/"]',
            ];

            let cards = [];
            for (const sel of selectors) {
                cards = document.querySelectorAll(sel);
                if (cards.length > 0) break;
            }

            // Strategy 2: If no cards found, try finding product links
            if (cards.length === 0) {
                const links = document.querySelectorAll('a[href*="/product/"]');
                for (const link of links) {
                    const card = link.closest('[class*="card"], [class*="item"], [class*="product"]') || link;
                    cards = [card];
                    break;
                }
            }

            for (const card of cards) {
                const link = card.tagName === 'A' ? card : card.querySelector('a');
                const href = link ? link.getAttribute('href') : '';
                const nameEl = card.querySelector('[class*="title"], [class*="name"], h3, h4');
                const priceEl = card.querySelector('[class*="price"], [class*="amount"]');
                const imgEl = card.querySelector('img[src*="tiktok"], img[src*="p16"]');
                const salesEl = card.querySelector('[class*="sales"], [class*="sold"]');

                // Extract product ID from href
                const spId = (href || '').match(/\\/product\\/?(\\d+)/)?.[1] || '';

                results.push({
                    sp_id: spId,
                    name: nameEl ? nameEl.textContent.trim() : '',
                    price: parseFloat((priceEl ? priceEl.textContent.replace(/[^0-9.]/g, '') : '0')) || 0,
                    image_url: imgEl ? imgEl.getAttribute('src') || '' : '',
                    sales_text: salesEl ? salesEl.textContent.trim() : '',
                    url: href ? (href.startsWith('http') ? href : 'https://www.tiktok.com' + href) : '',
                });
            }

            return results;
        }
        """

        try:
            raw = await page.evaluate(extract_script)
            for item in raw[:limit]:
                product = AffiliateProduct(
                    sp_id=item.get("sp_id", ""),
                    name=item.get("name", "Unknown Product"),
                    price=item.get("price", 0),
                    image_url=item.get("image_url", ""),
                    affiliate_link=item.get("url", ""),
                )
                products.append(product.to_dict())

                # If we got affiliate links, try to get commission data
                if product.affiliate_link:
                    await asyncio.sleep(0.5)
                    await self._enrich_with_commission(product)

        except Exception as e:
            logger.warning(f"Failed to extract products: {e}")

        return products[:limit]

    async def _enrich_with_commission(self, product: AffiliateProduct):
        """
        Try to get commission data for a product.
        This often requires being logged in, so it may fail.
        """
        try:
            # Commission data is often in a separate API call or hidden element
            # For now, estimate based on category defaults
            category_avg_commission = {
                "Thời trang": 15.0,
                "Mỹ phẩm": 20.0,
                "Điện tử": 8.0,
                "Nhà cửa": 12.0,
                "Sức khỏe": 18.0,
                "": 10.0,  # default
            }
            product.commission_pct = category_avg_commission.get(product.category, 10.0)
            product.commission_amount = round(product.price * product.commission_pct / 100, 2)
        except Exception as e:
            logger.debug(f"Failed to enrich commission: {e}")

    # ---- Web Search Fallback ----

    async def _web_search_fallback(self, limit: int = 20, category: str = "", keyword: str = "") -> List[Dict]:
        """Fallback: load demo products or return empty (no external search dependency)."""
        sample_path = Path(__file__).parent.parent.parent / "data" / "affiliate" / "sample_products.json"
        if sample_path.exists():
            try:
                import json
                data = json.loads(sample_path.read_text(encoding="utf-8"))
                items = data.get("products", data if isinstance(data, list) else [])
                return items[:limit]
            except Exception as e:
                logger.warning(f"Sample products load failed: {e}")

        logger.warning(
            "Crawl returned no products. Add data/affiliate/sample_products.json "
            "or log in to TikTok Shop for Playwright crawl."
        )
        return []


# ---- CLI Entry Point ----

async def main():
    """Test the scanner."""
    logging.basicConfig(level=logging.INFO)
    scanner = AffiliateScanner()
    try:
        print("Scanning trending products...")
        products = await scanner.scan_trending(limit=5)
        print(f"\nFound {len(products)} products:")
        for p in products:
            print(f"  - {p['name']} (${p['price']}) | Commission: {p['commission_pct']}%")
            if p['affiliate_link']:
                print(f"    Link: {p['affiliate_link'][:80]}...")
    finally:
        await scanner.close()


if __name__ == "__main__":
    asyncio.run(main())
