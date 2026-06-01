# TikTok Farm - Scheduler Module
# APScheduler-based job scheduling for farm sessions and post uploads

import asyncio
import logging
import random
from collections import defaultdict
from typing import Optional, List, Dict, Set
from datetime import datetime, timedelta, time
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.jobstores.memory import MemoryJobStore
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("APScheduler not installed. Scheduler disabled.")


def _build_jobstores(settings: dict) -> dict:
    redis_cfg = settings.get("redis", {})
    if redis_cfg.get("enabled"):
        try:
            from apscheduler.jobstores.redis import RedisJobStore
            return {
                "default": RedisJobStore(
                    host=redis_cfg.get("host", "localhost"),
                    port=int(redis_cfg.get("port", 6379)),
                    db=int(redis_cfg.get("db", 0)),
                    password=redis_cfg.get("password") or None,
                )
            }
        except Exception as e:
            logger.warning(f"Redis jobstore unavailable, using memory: {e}")
    return {"default": MemoryJobStore()}


class FarmScheduler:
    """APScheduler-based job scheduler for TikTok farm operations.

    Manages:
    - Post schedules: 3/day/account in random time slots
    - Farm sessions: 2-3/day/account with random timing
    - Health checks: periodic
    - Retry queue: max 3 retries on failure
    """

    def __init__(
        self,
        account_manager,
        farm_engine,
        post_engine,
        health_monitor,
        settings: dict,
        proxy_manager=None,
        session_service=None,
        warmup_manager=None,
    ):
        self.account_mgr = account_manager
        self.farm_engine = farm_engine
        self.post_engine = post_engine
        self.health_monitor = health_monitor
        self.proxy_mgr = proxy_manager
        self.session_svc = session_service
        self.warmup_mgr = warmup_manager
        self.settings = settings

        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False
        self._retry_counts: Dict[str, int] = {}
        self._max_retries = 3
        self._account_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._farm_active: Set[int] = set()
        self.active_farm_tasks: Optional[dict] = None
        self.farm_semaphore = None
        self.make_farm_engine = None
        self.event_bus = None
        self.max_concurrent_farms = 3

        # Scheduler config
        scheduler_config = settings.get("scheduler", {})
        self.posts_per_day = scheduler_config.get("posts_per_day", 3)
        self.farm_sessions_per_day = scheduler_config.get("farm_sessions_per_day", 3)
        self.farm_session_minutes = scheduler_config.get("farm_session_minutes", 15)
        self.post_time_slots = scheduler_config.get("post_time_slots", [
            ["08:00", "11:00"],
            ["14:00", "17:00"],
            ["19:00", "22:00"],
        ])

        # Health check config
        health_config = settings.get("health_check", {})
        self.health_interval = health_config.get("interval_minutes", 60)

        content_cfg = settings.get("content", {})
        self.default_affiliate_link = content_cfg.get("default_affiliate_link", "")

        accounts_cfg = settings.get("accounts", {})
        self.real_account_ids = set(accounts_cfg.get("real_account_ids") or [])
        aff_cfg = settings.get("affiliate", {})
        real_sched = aff_cfg.get("real_account_post_schedule", {})
        self.real_posts_per_day = int(real_sched.get("posts_per_day", 2))
        self.real_post_time_slots = real_sched.get(
            "time_slots", [["19:00", "22:00"]]
        )
        self.affiliate_pipeline = None  # set from main.py

    def start(self):
        """Start the APScheduler."""
        if not APSCHEDULER_AVAILABLE:
            logger.error("APScheduler not available. Cannot start scheduler.")
            return

        if self._running:
            logger.warning("Scheduler already running")
            return

        try:
            jobstores = _build_jobstores(self.settings)
            store_name = "redis" if self.settings.get("redis", {}).get("enabled") else "memory"
            self._scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
            self._scheduler.start()
            self._running = True
            logger.info(f"Farm scheduler started (jobstore={store_name})")

            if self.warmup_mgr:
                self.warmup_mgr.run_daily_tick()

            self._schedule_health_checks()
            self._schedule_warmup_tick()
            self._schedule_pending_post_checker()
            self._schedule_daily_jobs()

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

    def _schedule_pending_post_checker(self):
        """Publish queued batch posts when scheduled_at is due."""
        if not self._scheduler:
            return
        self._scheduler.add_job(
            self._process_due_pending_posts,
            IntervalTrigger(minutes=1),
            id="pending_posts_checker",
            name="Pending batch post publisher",
            replace_existing=True,
        )
        logger.info("Scheduled pending post checker (every 1 min)")

    def _schedule_health_checks(self):
        """Schedule periodic health checks."""
        if not self._scheduler:
            return

        self._scheduler.add_job(
            self._run_health_check,
            IntervalTrigger(minutes=self.health_interval),
            id="health_check",
            name="Periodic Health Check",
            replace_existing=True,
        )
        logger.info(f"Scheduled health checks every {self.health_interval} minutes")

    def _schedule_warmup_tick(self):
        if not self._scheduler or not self.warmup_mgr:
            return
        self._scheduler.add_job(
            self._run_warmup_tick,
            CronTrigger(hour=0, minute=5),
            id="warmup_daily",
            name="Daily Warm-up Tick",
            replace_existing=True,
        )

    async def _run_warmup_tick(self):
        if self.warmup_mgr:
            result = self.warmup_mgr.run_daily_tick()
            logger.info(f"Warm-up tick: {result}")
            self.reschedule_all()

    def _schedule_daily_jobs(self):
        """Schedule daily farm and post jobs for all accounts."""
        if not self._scheduler:
            return

        # Get all active accounts
        accounts = self.account_mgr.get_all_accounts()
        active_accounts = [
            a for a in accounts if a.status in ("active", "warming", "pending")
        ]

        for account in active_accounts:
            self.schedule_account_daily(account.id)

        logger.info(f"Scheduled daily jobs for {len(active_accounts)} accounts")

    def is_real_account(self, account_id: int) -> bool:
        return account_id in self.real_account_ids

    def schedule_account_daily(self, account_id: int):
        """Schedule farm sessions and posts for a specific account for today."""
        if not self._scheduler:
            return

        account = self.account_mgr.get_account(account_id)
        if not account:
            logger.warning(f"Account {account_id} not found for scheduling")
            return

        is_real = self.is_real_account(account_id)
        farm_count = 1 if is_real else self.farm_sessions_per_day
        posts_per_day = self.real_posts_per_day if is_real else self.posts_per_day
        post_slots = self.real_post_time_slots if is_real else self.post_time_slots

        # Schedule farm sessions (fewer for Real accounts — focus on video posts)
        farm_sessions = self._generate_random_times(
            farm_count,
            hours_range=(6, 23),
        )

        for i, session_time in enumerate(farm_sessions):
            job_id = f"farm_{account_id}_{i}_{datetime.now().strftime('%Y%m%d')}"
            run_time = datetime.now().replace(
                hour=session_time.hour,
                minute=session_time.minute,
                second=0,
                microsecond=0,
            )

            # If time has passed today, schedule for tomorrow
            if run_time < datetime.now():
                run_time += timedelta(days=1)

            self._scheduler.add_job(
                self._execute_farm_session,
                DateTrigger(run_date=run_time),
                args=[account_id],
                id=job_id,
                name=f"Farm Session {account.username} #{i + 1}",
                replace_existing=True,
            )
            logger.info(f"Scheduled farm session for {account.username} at {run_time}")

        # Schedule posts (Real: golden hours video; Farm: slideshow slots)
        if not post_slots:
            post_slots = self.post_time_slots
        posts_per_slot = max(1, posts_per_day // len(post_slots))

        for slot_idx, (start_str, end_str) in enumerate(post_slots):
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))

            for p in range(posts_per_slot):
                # Random time within the slot
                slot_start = start_h * 60 + start_m
                slot_end = end_h * 60 + end_m
                if slot_end <= slot_start:
                    continue

                random_minute = random.randint(slot_start, slot_end)
                post_time = time(hour=random_minute // 60, minute=random_minute % 60)

                job_id = f"post_{account_id}_{slot_idx}_{p}_{datetime.now().strftime('%Y%m%d')}"
                run_time = datetime.now().replace(
                    hour=post_time.hour,
                    minute=post_time.minute,
                    second=0,
                    microsecond=0,
                )

                if run_time < datetime.now():
                    run_time += timedelta(days=1)

                self._scheduler.add_job(
                    self._execute_post,
                    DateTrigger(run_date=run_time),
                    args=[account_id],
                    id=job_id,
                    name=f"Post {account.username} Slot {slot_idx + 1}.{p + 1}",
                    replace_existing=True,
                )
                logger.info(f"Scheduled post for {account.username} at {run_time}")

    def _generate_random_times(self, count: int, hours_range: tuple = (6, 23)) -> List[time]:
        """Generate random times within a range, spaced apart."""
        min_hour, max_hour = hours_range
        if count <= 0:
            return []

        # Divide the day into segments and pick one random time per segment
        segment_size = (max_hour - min_hour) / count
        times = []
        used_minutes = set()

        for i in range(count):
            segment_start = int(min_hour + i * segment_size)
            segment_end = int(min_hour + (i + 1) * segment_size)

            attempts = 0
            while attempts < 10:
                hour = random.randint(segment_start, max(segment_start, segment_end - 1))
                minute = random.randint(0, 59)
                key = hour * 60 + minute
                if key not in used_minutes:
                    used_minutes.add(key)
                    times.append(time(hour=hour, minute=minute))
                    break
                attempts += 1

            if attempts >= 10:
                # Fallback
                times.append(time(hour=segment_start, minute=random.randint(0, 59)))

        return sorted(times, key=lambda t: t.hour * 60 + t.minute)

    async def _execute_farm_session(self, account_id: int):
        """Execute a single farm session for an account."""
        job_id = f"farm_{account_id}"
        logger.info(f"Starting farm session for account {account_id}")

        if account_id in self._farm_active:
            logger.info(f"Farm already running for account {account_id}, skip duplicate")
            return

        async with self._account_locks[account_id]:
            self._farm_active.add(account_id)
            try:
                await self._run_farm_session_body(account_id, job_id)
            finally:
                self._farm_active.discard(account_id)

    async def _run_farm_session_body(self, account_id: int, job_id: str):
        try:
            account = self.account_mgr.get_account(account_id)
            if not account or account.status in ("banned", "shadowbanned", "paused"):
                logger.warning(
                    f"Account {account_id} not eligible for farm "
                    f"(status: {account.status if account else 'N/A'})"
                )
                return

            session = await self._prepare_session(account_id)
            if not session.get("ok"):
                await self._handle_retry(job_id, account_id, "farm_session")
                return

            account = session["account"]
            if account.status == "pending" and self.warmup_mgr:
                self.warmup_mgr.promote_pending_accounts()

            if self.warmup_mgr and account.status == "warming":
                wp = self.warmup_mgr.get_actions_for_account(account)
                actions = {
                    "scroll": wp["scroll"],
                    "like": wp["like"],
                    "comment": wp["comment"],
                    "follow": wp["follow"],
                    "watch": wp["watch"],
                }
                duration = wp["duration_minutes"]
            else:
                actions = {
                    "scroll": True,
                    "like": random.randint(2, 5),
                    "comment": random.randint(0, 2),
                    "follow": random.randint(0, 2),
                    "watch": random.randint(3, 6),
                }
                duration = self.farm_session_minutes

            active = self.active_farm_tasks
            if active is not None and account_id in active:
                logger.info(f"Skipping scheduled farm {account_id}: manual farm already running")
                return
            if active is not None:
                while len(active) >= self.max_concurrent_farms:
                    await asyncio.sleep(5)

            engine = (
                self.make_farm_engine()
                if self.make_farm_engine
                else self.farm_engine
            )
            session_id = f"farm_{account_id}_{int(datetime.now().timestamp())}"
            if self.event_bus:
                if not self.event_bus.has_session(session_id):
                    self.event_bus.create_session(session_id)
            if active is not None:
                active[account_id] = session_id

            async def _run():
                if self.farm_semaphore:
                    async with self.farm_semaphore:
                        return await engine.run_farm_session(
                            account_id=account_id,
                            proxy_url=session["proxy_url"],
                            duration_minutes=duration,
                            actions=actions,
                            session_id=session_id,
                        )
                return await engine.run_farm_session(
                    account_id=account_id,
                    proxy_url=session["proxy_url"],
                    duration_minutes=duration,
                    actions=actions,
                    session_id=session_id,
                )

            try:
                session_stats = await _run()
            finally:
                if active is not None:
                    active.pop(account_id, None)

            # Log activity
            if session_stats.get("completed"):
                total_duration = session_stats.get("duration_minutes", self.farm_session_minutes) * 60
                self.account_mgr.log_activity(
                    account_id=account_id,
                    activity_type="farm_session",
                    duration_seconds=total_duration,
                    actions_count=sum(
                        s.get("liked", 0) + s.get("followed", 0) + s.get("commented", 0)
                        for s in session_stats.get("actions", {}).values()
                        if isinstance(s, dict)
                    ),
                )
                self.account_mgr.mark_last_active(account_id)
                logger.info(f"Farm session for account {account_id} completed and logged")

                # Check if warming account can be promoted to active
                if account.status == "warming":
                    self.account_mgr.complete_warming(account_id)

                # Clear retry count on success
                if job_id in self._retry_counts:
                    del self._retry_counts[job_id]

            else:
                logger.warning(f"Farm session for account {account_id} did not complete successfully")
                await self._handle_retry(job_id, account_id, "farm_session")

        except Exception as e:
            logger.error(f"Farm session error for account {account_id}: {e}", exc_info=True)
            await self._handle_retry(job_id, account_id, "farm_session")

    async def _prepare_session(self, account_id: int) -> Dict:
        if self.session_svc:
            return await self.session_svc.prepare(account_id)
        account = self.account_mgr.get_account(account_id)
        return {"ok": bool(account), "account": account, "proxy_url": None, "cookie_data": None, "username": "", "password": ""}

    async def _execute_post(self, account_id: int):
        """Execute a post upload for an account."""
        job_id = f"post_{account_id}"
        logger.info(f"Starting post upload for account {account_id}")

        if account_id in self._farm_active:
            logger.info(f"Farm in progress for {account_id}, deferring post (priority: farm > post)")
            if self._scheduler:
                retry_time = datetime.now() + timedelta(minutes=15)
                self._scheduler.add_job(
                    self._execute_post,
                    DateTrigger(run_date=retry_time),
                    args=[account_id],
                    id=f"{job_id}_defer_{int(retry_time.timestamp())}",
                    name=f"Deferred post account {account_id}",
                )
            return

        async with self._account_locks[account_id]:
            await self._run_post_body(account_id, job_id)

    async def _run_post_body(self, account_id: int, job_id: str):
        try:
            account = self.account_mgr.get_account(account_id)
            if not account or account.status != "active":
                logger.warning(
                    f"Account {account_id} not active (status: {account.status if account else 'N/A'})"
                )
                return

            session = await self._prepare_session(account_id)
            if not session.get("ok"):
                await self._handle_retry(job_id, account_id, "post")
                return

            account = session["account"]

            if self.is_real_account(account_id) and self.affiliate_pipeline:
                await self._run_affiliate_video_post(account_id, session, job_id)
                return

            # Get content pipeline
            from src.content_pipeline import ContentPipeline
            pipeline = ContentPipeline.from_settings(self.settings)

            # Generate slideshow content
            post_dir = await pipeline.generate_post(
                account_id=account_id,
                product_name=f"Product_{account_id}",
                rating=round(random.uniform(3.5, 5.0), 1),
                review=random.choice([
                    "Amazing quality! Highly recommend this product.",
                    "Best purchase I've made this year. Love it!",
                    "Perfect for daily use. Great value for money.",
                    "Exceeded my expectations. Will buy again!",
                    "Fast shipping and excellent quality. 5 stars!",
                ]),
                price=f"${random.randint(9, 99)}.{random.randint(0, 99):02d}",
            )

            if not post_dir:
                logger.error(f"Failed to generate content for account {account_id}")
                await self._handle_retry(job_id, account_id, "post")
                return

            # Generate caption and hashtags
            captions = [
                "Check out this amazing find! 🔥",
                "You need this in your life! ❤️",
                "Best product ever! Link in bio 👆",
                "Game changer alert! 🚀",
                "Obsessed with this! 😍",
            ]
            hashtag_pool = ["fyp", "foryou", "viral", "amazonfinds", "musthave",
                            "trending", "affiliate", "productreview", "shopping", "deals"]
            selected_tags = random.sample(hashtag_pool, random.randint(3, 6))

            caption = random.choice(captions)
            hashtags = " ".join(selected_tags)
            affiliate_link = self.default_affiliate_link or ""

            upload_result = await self.post_engine.upload_slideshow(
                account_id=account_id,
                images_dir=post_dir,
                caption=caption,
                hashtags=hashtags,
                affiliate_link=affiliate_link,
                username=session.get("username") or account.username,
                password=session.get("password") or getattr(account, "password", ""),
                cookie_data=session.get("cookie_data") or account.cookie_data,
                proxy_url=session.get("proxy_url"),
            )

            if upload_result.get("success"):
                post_id = self.account_mgr.add_post(
                    account_id=account_id,
                    content_path=post_dir,
                    caption=caption,
                    hashtags=hashtags,
                    affiliate_link=affiliate_link,
                    scheduled_at=datetime.now().isoformat(),
                )
                if post_id and upload_result.get("tiktok_post_id"):
                    self.account_mgr.mark_post_posted(
                        post_id, tiktok_post_id=upload_result["tiktok_post_id"]
                    )

                account = self.account_mgr.get_account(account_id)
                self.account_mgr.update_account(
                    account_id,
                    total_posts=(account.total_posts or 0) + 1,
                    last_active=datetime.now().isoformat(),
                )

                logger.info(f"Post for account {account_id} uploaded successfully!")

                # Clear retry count on success
                if job_id in self._retry_counts:
                    del self._retry_counts[job_id]

            else:
                logger.warning(f"Post upload for account {account_id} failed: {upload_result.get('error')}")
                await self._handle_retry(job_id, account_id, "post")

        except Exception as e:
            logger.error(f"Post error for account {account_id}: {e}", exc_info=True)
            await self._handle_retry(job_id, account_id, "post")

    async def _handle_retry(self, job_id: str, account_id: int, job_type: str):
        """Handle job retry logic (max 3 retries)."""
        current = self._retry_counts.get(job_id, 0) + 1
        self._retry_counts[job_id] = current

        if current >= self._max_retries:
            logger.error(f"Job {job_id} failed after {current} retries. Giving up.")
            self.account_mgr.add_alert(
                account_id=account_id,
                alert_type="rate_limit" if "post" in job_type else "login_fail",
                message=f"{job_type.capitalize()} failed after {current} attempts",
            )
            # Clean up retry tracking
            if job_id in self._retry_counts:
                del self._retry_counts[job_id]
        else:
            # Reschedule with exponential backoff (5min, 15min, 30min)
            backoff = [5, 15, 30][current - 1] * 60
            retry_time = datetime.now() + timedelta(seconds=backoff)

            if self._scheduler:
                self._scheduler.add_job(
                    self._execute_farm_session if "farm" in job_type else self._execute_post,
                    DateTrigger(run_date=retry_time),
                    args=[account_id],
                    id=f"{job_id}_retry_{current}",
                    name=f"Retry {job_type} #{current} for account {account_id}",
                )
                logger.info(f"Rescheduled {job_type} for account {account_id} in {backoff//60} min (attempt {current}/{self._max_retries})")

    async def _process_due_pending_posts(self):
        """Upload videos/slideshows queued with scheduled_at."""
        posts = self.account_mgr.get_due_pending_posts(limit=3)
        if not posts:
            return
        for row in posts:
            account_id = row.get("account_id")
            post_id = row.get("id")
            if not account_id or not post_id:
                continue
            if account_id in self._farm_active:
                logger.info(f"Defer batch post {post_id}: farm active on account {account_id}")
                continue
            if not self.account_mgr.claim_post_for_publish(post_id):
                continue
            async with self._account_locks[account_id]:
                await self._publish_scheduled_post(row)

    async def _publish_scheduled_post(self, row: dict):
        """Publish a single queued post from the batch schedule."""
        post_id = row.get("id")
        account_id = row.get("account_id")
        try:
            account = self.account_mgr.get_account(account_id)
            if not account or account.status not in ("active", "warming"):
                self.account_mgr.update_post(post_id, status="failed")
                logger.warning(f"Batch post {post_id}: account {account_id} not active")
                return

            session = await self._prepare_session(account_id)
            if not session.get("ok"):
                self.account_mgr.update_post(post_id, status="pending")
                logger.warning(f"Batch post {post_id}: session prep failed, reset to pending")
                return

            content_path = row.get("content_path") or ""
            path = Path(content_path)
            if not path.exists():
                self.account_mgr.update_post(post_id, status="failed")
                logger.error(f"Batch post {post_id}: missing file {content_path}")
                return

            proxy_url = session.get("proxy_url")
            if not proxy_url and account.proxy_id and self.proxy_mgr:
                proxy_obj = self.proxy_mgr.get_proxy(account.proxy_id)
                if proxy_obj and proxy_obj.is_alive:
                    proxy_url = proxy_obj.url

            caption = row.get("caption") or ""
            hashtags = row.get("hashtags") or "fyp foryou viral"
            affiliate_link = row.get("affiliate_link") or ""

            if path.is_dir():
                result = await self.post_engine.upload_slideshow(
                    account_id=account_id,
                    images_dir=str(path),
                    caption=caption,
                    hashtags=hashtags,
                    affiliate_link=affiliate_link,
                    username=session.get("username") or account.username,
                    password=session.get("password") or getattr(account, "password", ""),
                    cookie_data=session.get("cookie_data") or account.cookie_data,
                    proxy_url=proxy_url,
                )
            else:
                result = await self.post_engine.upload_video(
                    account_id=account_id,
                    video_path=str(path),
                    caption=caption,
                    hashtags=hashtags,
                    affiliate_link=affiliate_link,
                    username=session.get("username") or account.username,
                    password=session.get("password") or getattr(account, "password", ""),
                    cookie_data=session.get("cookie_data") or account.cookie_data,
                    proxy_url=proxy_url,
                )

            if result.get("success"):
                self.account_mgr.mark_post_posted(
                    post_id,
                    tiktok_post_id=result.get("tiktok_post_id") or result.get("post_id"),
                    views=0,
                )
                acc = self.account_mgr.get_account(account_id)
                self.account_mgr.update_account(
                    account_id,
                    total_posts=(acc.total_posts or 0) + 1,
                    last_active=datetime.now().isoformat(),
                )
                logger.info(f"Batch post {post_id} published for account {account_id}")
            else:
                self.account_mgr.update_post(post_id, status="failed")
                logger.warning(
                    f"Batch post {post_id} failed: {result.get('error', 'unknown')}"
                )
        except Exception as e:
            logger.error(f"Batch post {post_id} error: {e}", exc_info=True)
            self.account_mgr.update_post(post_id, status="failed")

    async def _run_health_check(self):
        """Run periodic health checks on all accounts."""
        logger.info("Running scheduled health check")
        try:
            if self.health_monitor:
                results = await self.health_monitor.check_all_accounts()
                logger.info(f"Health check complete: {results}")
        except Exception as e:
            logger.error(f"Health check error: {e}")

    def reschedule_all(self):
        """Reschedule all jobs (useful after adding new accounts)."""
        if not self._scheduler:
            return

        # Remove existing account-specific jobs
        jobs = self._scheduler.get_jobs()
        for job in jobs:
            if job.id.startswith("farm_") or job.id.startswith("post_"):
                self._scheduler.remove_job(job.id)

        self._schedule_daily_jobs()
        logger.info("All jobs rescheduled")

    def stop(self):
        """Stop the scheduler and clean up."""
        if self._scheduler and self._running:
            try:
                self._scheduler.shutdown(wait=False)
                logger.info("Scheduler stopped")
            except Exception as e:
                logger.warning(f"Error stopping scheduler: {e}")
        self._running = False

    @classmethod
    async def _run_affiliate_video_post(self, account_id: int, session: dict, job_id: str):
        """Real account: affiliate pipeline video upload."""
        try:
            result = await self.affiliate_pipeline.run_for_account(
                account_id, session
            )
            if result.get("success"):
                product = result.get("steps", {}).get("product", "")
                post_id = self.account_mgr.add_post(
                    account_id=account_id,
                    content_path=result.get("steps", {}).get("edited", ""),
                    caption=product,
                    hashtags="affiliate tiktokshop",
                    affiliate_link=self.default_affiliate_link,
                    scheduled_at=datetime.now().isoformat(),
                )
                upload = result.get("steps", {}).get("upload") or {}
                if post_id and upload.get("tiktok_post_id"):
                    self.account_mgr.mark_post_posted(
                        post_id, tiktok_post_id=upload["tiktok_post_id"]
                    )
                acc = self.account_mgr.get_account(account_id)
                self.account_mgr.update_account(
                    account_id,
                    total_posts=(acc.total_posts or 0) + 1,
                    last_active=datetime.now().isoformat(),
                )
                if job_id in self._retry_counts:
                    del self._retry_counts[job_id]
                logger.info(f"Affiliate video post OK for account {account_id}")
            else:
                logger.error(f"Affiliate post failed: {result.get('error')}")
                await self._handle_retry(job_id, account_id, "post")
        except Exception as e:
            logger.error(f"Affiliate post error: {e}", exc_info=True)
            await self._handle_retry(job_id, account_id, "post")

    @classmethod
    def from_settings(
        cls,
        settings: dict,
        account_manager,
        farm_engine,
        post_engine,
        health_monitor,
        proxy_manager=None,
        session_service=None,
        warmup_manager=None,
        affiliate_pipeline=None,
    ) -> "FarmScheduler":
        inst = cls(
            account_manager=account_manager,
            farm_engine=farm_engine,
            post_engine=post_engine,
            health_monitor=health_monitor,
            settings=settings,
            proxy_manager=proxy_manager,
            session_service=session_service,
            warmup_manager=warmup_manager,
        )
        inst.affiliate_pipeline = affiliate_pipeline
        return inst

    def bind_farm_runtime(
        self,
        *,
        active_farm_tasks: dict,
        farm_semaphore,
        make_farm_engine,
        event_bus,
        max_concurrent_farms: int = 3,
    ):
        """Share manual-farm concurrency limits with API / farm queue."""
        self.active_farm_tasks = active_farm_tasks
        self.farm_semaphore = farm_semaphore
        self.make_farm_engine = make_farm_engine
        self.event_bus = event_bus
        self.max_concurrent_farms = max(1, int(max_concurrent_farms))
