# TikTok Farm — Affiliate uploader (video + caption + link)

import logging
import random
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AffiliateUploader:
    """Upload affiliate videos via PostEngine."""

    def __init__(self, post_engine, account_manager, settings: Optional[dict] = None):
        self.post_engine = post_engine
        self.account_mgr = account_manager
        cfg = (settings or {}).get("content", {})
        self.default_affiliate_link = cfg.get("default_affiliate_link", "")

    def build_caption(self, product: Dict):
        name = product.get("name") or "Must-have deal"
        price = product.get("price", 0)
        comm = product.get("commission_pct", 0)
        captions = [
            f"{name} — deal hot! 🔥",
            f"San pham ban chay: {name} ✨",
            f"Gia tot {price} — xem ngay! 👆",
        ]
        tags = ["fyp", "foryou", "tiktokshop", "affiliate", "muasam", "deal"]
        if comm >= 15:
            tags.append("hoahongcao")
        return random.choice(captions), " ".join(random.sample(tags, min(5, len(tags))))

    async def upload(
        self,
        account_id: int,
        video_path: Path,
        product: Dict,
        session: Optional[Dict] = None,
    ) -> Dict:
        session = session or {}
        account = session.get("account") or self.account_mgr.get_account(account_id)
        if not account:
            return {"success": False, "error": "Account not found"}

        caption, hashtags = self.build_caption(product)
        affiliate_link = (
            product.get("affiliate_link")
            or self.default_affiliate_link
            or ""
        )

        return await self.post_engine.upload_video(
            account_id=account_id,
            video_path=str(video_path),
            caption=caption,
            hashtags=hashtags,
            affiliate_link=affiliate_link,
            username=session.get("username") or account.username,
            password=session.get("password") or getattr(account, "password", ""),
            cookie_data=session.get("cookie_data") or account.cookie_data,
            proxy_url=session.get("proxy_url"),
        )
