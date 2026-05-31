# TikTok Farm — Affiliate pipeline orchestrator

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .scanner import AffiliateScanner
from .downloader import VideoDownloader
from .editor import VideoEditor
from .uploader import AffiliateUploader

logger = logging.getLogger(__name__)


class AffiliatePipeline:
    """Scan → download → edit → upload affiliate videos."""

    def __init__(
        self,
        settings: dict,
        post_engine,
        account_manager,
    ):
        self.settings = settings
        aff_cfg = settings.get("affiliate", {})
        self.scanner = AffiliateScanner(aff_cfg)
        self.downloader = VideoDownloader(aff_cfg)
        self.editor = VideoEditor(aff_cfg)
        self.uploader = AffiliateUploader(post_engine, account_manager, settings)
        self.commission_min = float(aff_cfg.get("commission_min_pct", 10))
        self.trending_file = Path(aff_cfg.get("trending_cache", "data/affiliate/trending.json"))
        self.trending_file.parent.mkdir(parents=True, exist_ok=True)

    async def close(self):
        await self.scanner.close()

    async def scan_and_cache(self, limit: int = 20, category: str = "") -> List[Dict]:
        products = await self.scanner.scan_trending(limit=limit, category=category)
        products = await self.scanner.filter_by_commission(
            products, self.commission_min
        )
        payload = {
            "updated_at": datetime.now().isoformat(),
            "count": len(products),
            "products": products,
        }
        self.trending_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Cached {len(products)} trending products")
        return products

    def load_cached_trending(self) -> List[Dict]:
        if not self.trending_file.exists():
            return []
        try:
            data = json.loads(self.trending_file.read_text(encoding="utf-8"))
            return data.get("products", [])
        except Exception as e:
            logger.warning(f"Failed to read trending cache: {e}")
            return []

    async def run_for_account(
        self,
        account_id: int,
        session: Dict,
        product: Optional[Dict] = None,
    ) -> Dict:
        """Full pipeline for one real account."""
        result = {
            "success": False,
            "account_id": account_id,
            "steps": {},
            "error": None,
        }

        try:
            if not product:
                products = self.load_cached_trending()
                if not products:
                    products = await self.scan_and_cache(limit=10)
                if not products:
                    result["error"] = "No trending products found"
                    return result
                product = products[0]

            sp_id = product.get("sp_id") or "product"
            result["steps"]["product"] = product.get("name", sp_id)

            raw_paths = await self.downloader.download_product_videos(product, max_videos=1)
            result["steps"]["downloaded"] = len(raw_paths)
            if not raw_paths:
                result["error"] = "Video download failed (add video_urls or install yt-dlp)"
                return result

            final = await self.editor.edit_video(raw_paths[0], sp_id, product)
            result["steps"]["edited"] = str(final) if final else None
            if not final:
                result["error"] = "Video edit failed (check ffmpeg)"
                return result

            upload = await self.uploader.upload(account_id, final, product, session)
            result["steps"]["upload"] = upload
            result["success"] = bool(upload.get("success"))
            if not result["success"]:
                result["error"] = upload.get("error", "Upload failed")
            return result

        except Exception as e:
            logger.error(f"Affiliate pipeline error: {e}", exc_info=True)
            result["error"] = str(e)
            return result

    def status(self) -> Dict:
        import shutil

        try:
            import yt_dlp  # noqa: F401

            ytdlp = True
        except ImportError:
            ytdlp = False

        return {
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "yt_dlp": ytdlp,
            "trending_cached": self.trending_file.exists(),
            "trending_count": len(self.load_cached_trending()),
            "commission_min_pct": self.commission_min,
        }
