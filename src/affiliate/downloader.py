# TikTok Farm — Affiliate video downloader (yt-dlp + direct URL fallback)

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional, List, Dict
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

try:
    import yt_dlp

    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    logger.info("yt-dlp not installed; direct URL download only")


class VideoDownloader:
    """Download sample videos for affiliate products."""

    def __init__(self, settings: Optional[dict] = None):
        cfg = settings or {}
        self.output_dir = Path(cfg.get("video_output_dir", "data/videos/"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = int(cfg.get("download_timeout", 120))

    def raw_dir(self, sp_id: str) -> Path:
        d = self.output_dir / str(sp_id) / "raw"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def download_product_videos(
        self, product: Dict, max_videos: int = 1
    ) -> List[Path]:
        """Download up to max_videos for a product. Returns local file paths."""
        sp_id = product.get("sp_id") or "unknown"
        urls = list(product.get("video_urls") or [])
        link = product.get("affiliate_link") or product.get("url") or ""
        if link and _looks_like_video_url(link):
            urls.insert(0, link)
        if not urls and link:
            urls = [link]

        saved: List[Path] = []
        out = self.raw_dir(sp_id)

        for url in urls[:max_videos]:
            if not url:
                continue
            try:
                path = await self.download_url(url, out, sp_id)
                if path:
                    saved.append(path)
            except Exception as e:
                logger.warning(f"Download failed {url[:80]}: {e}")

        return saved

    async def download_url(
        self, url: str, out_dir: Path, sp_id: str = "clip"
    ) -> Optional[Path]:
        if YTDLP_AVAILABLE and _is_tiktok_or_youtube(url):
            path = await asyncio.to_thread(self._download_ytdlp, url, out_dir, sp_id)
            if path:
                return path

        if url.lower().endswith((".mp4", ".mov", ".webm")):
            return await self._download_direct(url, out_dir, sp_id)

        # TikTok page without yt-dlp: cannot download reliably
        logger.warning(f"No downloader for URL: {url[:100]}")
        return None

    def _download_ytdlp(self, url: str, out_dir: Path, sp_id: str) -> Optional[Path]:
        out_template = str(out_dir / f"{sp_id}_%(id)s.%(ext)s")
        opts = {
            "outtmpl": out_template,
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": self.timeout,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info.get("requested_downloads"):
                    return Path(info["requested_downloads"][0]["filepath"])
                ext = info.get("ext", "mp4")
                vid = info.get("id", sp_id)
                candidate = out_dir / f"{sp_id}_{vid}.{ext}"
                if candidate.exists():
                    return candidate
                for f in out_dir.glob(f"{sp_id}_*"):
                    if f.suffix in (".mp4", ".mov", ".webm", ".mkv"):
                        return f
        except Exception as e:
            logger.warning(f"yt-dlp error: {e}")
        return None

    async def _download_direct(self, url: str, out_dir: Path, sp_id: str) -> Optional[Path]:
        name = re.sub(r"[^\w.-]", "_", urlparse(url).path.split("/")[-1] or "video.mp4")
        if not name.endswith((".mp4", ".mov", ".webm")):
            name = f"{sp_id}.mp4"
        dest = out_dir / name

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                dest.write_bytes(await resp.read())
        logger.info(f"Downloaded {dest}")
        return dest


def _is_tiktok_or_youtube(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(x in host for x in ("tiktok.com", "youtube.com", "youtu.be"))


def _looks_like_video_url(url: str) -> bool:
    return bool(re.search(r"\.(mp4|mov|webm)(\?|$)", url, re.I)) or "/video/" in url
