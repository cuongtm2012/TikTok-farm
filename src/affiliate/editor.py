# TikTok Farm — Video editor (ffmpeg subprocess, non-blocking)

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont

    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False


class VideoEditor:
    """Trim/re-encode videos and optional thumbnail overlay via ffmpeg."""

    def __init__(self, settings: Optional[dict] = None):
        cfg = settings or {}
        base = Path(cfg.get("video_output_dir", "data/videos/"))
        self.output_dir = base
        self.max_duration = int(cfg.get("max_duration_seconds", 60))
        self.min_duration = int(cfg.get("min_duration_seconds", 15))
        self.ffmpeg = shutil.which("ffmpeg") or cfg.get("ffmpeg_path", "ffmpeg")

    def final_dir(self, sp_id: str) -> Path:
        d = self.output_dir / str(sp_id) / "final"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def edit_video(
        self,
        input_path: Path,
        sp_id: str,
        product: Optional[Dict] = None,
    ) -> Optional[Path]:
        """Produce a short mp4 ready for TikTok upload."""
        if not input_path.exists():
            logger.error(f"Input missing: {input_path}")
            return None

        out_dir = self.final_dir(sp_id)
        out_path = out_dir / f"{sp_id}_final.mp4"

        if self.ffmpeg and shutil.which(self.ffmpeg):
            ok = await self._ffmpeg_process(input_path, out_path)
            if ok and out_path.exists():
                return out_path

        # No ffmpeg: copy/trim not possible — use raw file if short enough
        logger.warning("ffmpeg not found; using raw file as final")
        if input_path.suffix.lower() == ".mp4":
            shutil.copy2(input_path, out_path)
            return out_path if out_path.exists() else None
        return None

    async def _ffmpeg_process(self, input_path: Path, out_path: Path) -> bool:
        duration = self.max_duration
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
        )
        cmd = [
            self.ffmpeg, "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"ffmpeg failed: {stderr.decode()[:500]}")
                return False
            logger.info(f"Edited video → {out_path}")
            return True
        except Exception as e:
            logger.error(f"ffmpeg error: {e}")
            return False

    def _create_overlay_png(self, out_dir: Path, product: Dict) -> Path:
        path = out_dir / "overlay.png"
        w, h = 1080, 200
        img = Image.new("RGBA", (w, h), (0, 0, 0, 180))
        draw = ImageDraw.Draw(img)
        name = (product.get("name") or "Hot deal")[:60]
        price = product.get("price", 0)
        comm = product.get("commission_pct", 0)
        lines = [name, f"Gia: {price} | HH: {comm}%", "Link bio / comment"]
        y = 20
        for line in lines:
            draw.text((24, y), line, fill=(255, 255, 255, 255))
            y += 36
        img.save(path)
        return path
