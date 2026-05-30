# TikTok Farm — 7-day account warm-up orchestration

import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Actions scale up over 7 days (spec: natural growth, no spam)
WARMUP_DAY_PROFILES: Dict[int, Dict] = {
    1: {"scroll": True, "like": 1, "comment": 0, "follow": 0, "watch": 2, "duration": 8},
    2: {"scroll": True, "like": 2, "comment": 0, "follow": 0, "watch": 3, "duration": 10},
    3: {"scroll": True, "like": 2, "comment": 1, "follow": 0, "watch": 3, "duration": 10},
    4: {"scroll": True, "like": 3, "comment": 1, "follow": 1, "watch": 4, "duration": 12},
    5: {"scroll": True, "like": 4, "comment": 1, "follow": 1, "watch": 5, "duration": 13},
    6: {"scroll": True, "like": 4, "comment": 2, "follow": 2, "watch": 5, "duration": 14},
    7: {"scroll": True, "like": 5, "comment": 2, "follow": 2, "watch": 6, "duration": 15},
}


class WarmupManager:
    """Moves accounts pending → warming and scales farm intensity by day."""

    def __init__(self, account_manager, proxy_manager, settings: dict):
        self.account_mgr = account_manager
        self.proxy_mgr = proxy_manager
        self.settings = settings
        warmup_cfg = settings.get("warmup", {})
        self.warmup_days = warmup_cfg.get("days", 7)
        self.auto_assign_proxy = warmup_cfg.get("auto_assign_proxy", True)

    def get_warmup_day(self, account) -> int:
        day = account.days_since_creation + 1
        return min(max(day, 1), self.warmup_days)

    def get_actions_for_account(self, account) -> Dict:
        day = self.get_warmup_day(account)
        profile = WARMUP_DAY_PROFILES.get(
            day, WARMUP_DAY_PROFILES[self.warmup_days]
        )
        return {
            "scroll": profile.get("scroll", True),
            "like": profile.get("like", 3),
            "comment": profile.get("comment", 1),
            "follow": profile.get("follow", 1),
            "watch": profile.get("watch", 4),
            "duration_minutes": profile.get("duration", 12),
            "warmup_day": day,
        }

    def promote_pending_accounts(self) -> List[int]:
        """pending → warming when proxy is available (or auto-assigned)."""
        promoted = []
        for account in self.account_mgr.get_all_accounts(status="pending"):
            proxy_id = account.proxy_id
            if not proxy_id and self.auto_assign_proxy:
                proxy = self.proxy_mgr.get_random_proxy()
                if proxy:
                    self.account_mgr.assign_proxy(account.id, proxy.id)
                    proxy_id = proxy.id

            if proxy_id or not self.auto_assign_proxy:
                if self.account_mgr.set_status(account.id, "warming"):
                    promoted.append(account.id)
                    logger.info(
                        f"Account {account.username} started warm-up (day 1/{self.warmup_days})"
                    )
        return promoted

    def finalize_warmed_accounts(self) -> List[int]:
        """warming → active after warmup_days."""
        activated = []
        for account in self.account_mgr.get_all_accounts(status="warming"):
            if account.days_since_creation >= self.warmup_days:
                if self.account_mgr.set_status(account.id, "active"):
                    activated.append(account.id)
                    logger.info(f"Account {account.username} completed warm-up → active")
        return activated

    def run_daily_tick(self) -> Dict:
        promoted = self.promote_pending_accounts()
        activated = self.finalize_warmed_accounts()
        return {
            "promoted_to_warming": len(promoted),
            "activated": len(activated),
            "promoted_ids": promoted,
            "activated_ids": activated,
        }
