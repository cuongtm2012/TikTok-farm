# TikTok Farm — Public profile lookup via TikTok-Api (optional)
# https://github.com/davidteather/TikTok-Api
# Requires: pip install TikTokApi && TIKTOK_MS_TOKEN from browser cookies on tiktok.com

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TIKTOK_API_IMPORT_ERROR: Optional[str] = None
try:
    from TikTokApi import TikTokApi  # type: ignore

    _HAS_TIKTOK_API = True
except ImportError as e:
    TikTokApi = None  # type: ignore
    _HAS_TIKTOK_API = False
    _TIKTOK_API_IMPORT_ERROR = str(e)


def _int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def normalize_profile(raw: Dict[str, Any], username: str = "") -> Dict[str, Any]:
    """Normalize TikTok user.info() payloads into a stable shape."""
    if not raw:
        raise ValueError("Empty profile response from TikTok")

    ui = raw.get("userInfo") if isinstance(raw.get("userInfo"), dict) else raw
    user = ui.get("user") if isinstance(ui.get("user"), dict) else {}
    stats = ui.get("stats") if isinstance(ui.get("stats"), dict) else {}

    if not user and isinstance(raw.get("user"), dict):
        user = raw["user"]
    if not stats and isinstance(raw.get("stats"), dict):
        stats = raw["stats"]

    unique_id = (
        user.get("uniqueId")
        or user.get("unique_id")
        or username
        or ""
    ).strip().lstrip("@")
    nickname = user.get("nickname") or user.get("nickName") or unique_id
    signature = user.get("signature") or user.get("bio") or ""
    avatar = (
        user.get("avatarLarger")
        or user.get("avatarMedium")
        or user.get("avatarThumb")
        or ""
    )

    followers = _int(stats.get("followerCount") or stats.get("follower_count"))
    following = _int(stats.get("followingCount") or stats.get("following_count"))
    video_count = _int(stats.get("videoCount") or stats.get("video_count"))
    heart_count = _int(stats.get("heartCount") or stats.get("heart_count"))

    return {
        "username": unique_id or username,
        "nickname": nickname,
        "bio": signature,
        "verified": bool(user.get("verified")),
        "followers": followers,
        "following": following,
        "video_count": video_count,
        "heart_count": heart_count,
        "avatar_url": avatar,
        "profile_url": f"https://www.tiktok.com/@{unique_id or username}",
        "sec_uid": user.get("secUid") or user.get("sec_uid") or "",
    }


class TikTokProfileService:
    """Fetch public TikTok user stats using davidteather/TikTok-Api."""

    def __init__(self, settings: Optional[dict] = None):
        settings = settings or {}
        cfg = settings.get("tiktok_profile", {})
        self.enabled = cfg.get("enabled", True)
        self.ms_token = (os.getenv("TIKTOK_MS_TOKEN") or cfg.get("ms_token") or "").strip()
        self.browser = os.getenv("TIKTOK_BROWSER", cfg.get("browser", "chromium"))

    def status(self) -> Dict[str, Any]:
        return {
            "library": "davidteather/TikTok-Api",
            "pypi": "TikTokApi",
            "installed": _HAS_TIKTOK_API,
            "enabled": self.enabled,
            "configured": bool(self.ms_token),
            "ready": self.is_ready(),
            "install_hint": "pip install TikTokApi && playwright install chromium",
            "token_hint": "Set TIKTOK_MS_TOKEN in .env (msToken cookie from tiktok.com)",
        }

    def is_ready(self) -> bool:
        return self.enabled and _HAS_TIKTOK_API and bool(self.ms_token)

    async def fetch_profile(self, username: str) -> Dict[str, Any]:
        """Return normalized public profile for @username."""
        username = (username or "").strip().lstrip("@")
        if not username:
            raise ValueError("username is required")

        if not self.enabled:
            raise RuntimeError("TikTok profile lookup is disabled in settings (tiktok_profile.enabled)")

        if not _HAS_TIKTOK_API:
            raise RuntimeError(
                "TikTokApi package not installed. Run: pip install TikTokApi"
                + (f" ({_TIKTOK_API_IMPORT_ERROR})" if _TIKTOK_API_IMPORT_ERROR else "")
            )

        if not self.ms_token:
            raise RuntimeError(
                "TIKTOK_MS_TOKEN is not set. Copy msToken from browser cookies on tiktok.com "
                "into .env or config/settings.yaml → tiktok_profile.ms_token"
            )

        raw = await self._fetch_via_tiktok_api(username)
        profile = normalize_profile(raw, username)
        profile["source"] = "TikTok-Api"
        return profile

    async def _fetch_via_tiktok_api(self, username: str) -> Dict[str, Any]:
        assert TikTokApi is not None
        async with TikTokApi() as api:
            await api.create_sessions(
                ms_tokens=[self.ms_token],
                num_sessions=1,
                sleep_after=3,
                browser=self.browser,
            )
            user = api.user(username)
            data = await user.info()
            if not data:
                raise ValueError(f"No profile data for @{username}")
            if isinstance(data, dict):
                empty_user = (
                    data.get("userInfo", {})
                    .get("user", {})
                    if isinstance(data.get("userInfo"), dict)
                    else {}
                )
                if empty_user == {} and not data.get("user"):
                    raise ValueError(
                        f"TikTok returned empty profile for @{username}. "
                        "Check TIKTOK_MS_TOKEN or try again later."
                    )
            return data if isinstance(data, dict) else {}
