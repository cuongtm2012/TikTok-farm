# TikTok Farm - Scheduler Module
# APScheduler-based job scheduling for farm sessions and post uploads

import asyncio
import logging
import random
from typing import Optional, List, Dict
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
    ):
        self.account_mgr = account_manager
        self.farm_engine = farm_engine
        self.post_engine = post_engine
        self.health_monitor = health_monitor
        self.settings = settings

        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False
        self._retry_counts: Dict[str, int] = {}  # job_id -> retry count
        self._max_retries = 3

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

    def start(self):
        """Start the APScheduler."""
        if not APSCHEDULER_AVAILABLE:
            logger.error("APScheduler not available. Cannot start scheduler.")
            return

        if self._running:
            logger.warning("Scheduler already running")
            return

        try:
            jobstores = {"default": MemoryJobStore()}

            self._scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
            self._scheduler.start()
            self._running = True
            logger.info("Farm scheduler started")

            # Schedule health checks
            self._schedule_health_checks()

            # Schedule daily jobs for all accounts
            self._schedule_daily_jobs()

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

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

    def _schedule_daily_jobs(self):
        """Schedule daily farm and post jobs for all accounts."""
        if not self._scheduler:
            return

        # Get all active accounts
        accounts = self.account_mgr.get_all_accounts()
        active_accounts = [a for a in accounts if a.status in ("active", "warming")]

        for account in active_accounts:
            self.schedule_account_daily(account.id)

        logger.info(f"Scheduled daily jobs for {len(active_accounts)} accounts")

    def schedule_account_daily(self, account_id: int):
        """Schedule farm sessions and posts for a specific account for today."""
        if not self._scheduler:
            return

        account = self.account_mgr.get_account(account_id)
        if not account:
            logger.warning(f"Account {account_id} not found for scheduling")
            return

        # Schedule farm sessions (2-3 random times throughout the day)
        farm_sessions = self._generate_random_times(
            self.farm_sessions_per_day,
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

        # Schedule posts (3/day in defined time slots)
        posts_per_slot = max(1, self.posts_per_day // len(self.post_time_slots))

        for slot_idx, (start_str, end_str) in enumerate(self.post_time_slots):
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

        try:
            account = self.account_mgr.get_account(account_id)
            if not account or account.status in ("banned", "shadowbanned", "paused"):
                logger.warning(f"Account {account_id} not eligible for farm session (status: {account.status if account else 'N/A'})")
                return

            # Get proxy for this account
            proxy = None
            if account.proxy_id:
                from src.proxy_manager import ProxyManager
                pm = ProxyManager.from_settings(self.settings)
                pm.load_from_csv()
                proxy_obj = pm.get_proxy(account.proxy_id)
                if proxy_obj and proxy_obj.is_alive:
                    proxy = proxy_obj.url

            # Randomize actions a bit
            actions = {
                "scroll": True,
                "like": random.randint(2, 5),
                "comment": random.randint(0, 2),
                "follow": random.randint(0, 2),
                "watch": random.randint(3, 6),
            }

            # Run the farm session
            session_stats = await self.farm_engine.run_farm_session(
                account_id=account_id,
                proxy_url=proxy,
                duration_minutes=self.farm_session_minutes,
                actions=actions,
            )

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

    async def _execute_post(self, account_id: int):
        """Execute a post upload for an account."""
        job_id = f"post_{account_id}"
        logger.info(f"Starting post upload for account {account_id}")

        try:
            account = self.account_mgr.get_account(account_id)
            if not account or account.status != "active":
                logger.warning(f"Account {account_id} not active (status: {account.status if account else 'N/A'})")
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
            affiliate_link = ""

            # Upload the post
            upload_result = await self.post_engine.upload_slideshow(
                account_id=account_id,
                images_dir=post_dir,
                caption=caption,
                hashtags=hashtags,
                affiliate_link=affiliate_link,
                username=account.username,
            )

            if upload_result.get("success"):
                # Record the post in database
                self.account_mgr.add_post(
                    account_id=account_id,
                    content_path=post_dir,
                    caption=caption,
                    hashtags=hashtags,
                    affiliate_link=affiliate_link,
                    scheduled_at=datetime.now().isoformat(),
                )

                # Update account stats
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
    def from_settings(cls, settings: dict, account_manager, farm_engine, post_engine, health_monitor) -> "FarmScheduler":
        """Create instance from settings dict."""
        return cls(
            account_manager=account_manager,
            farm_engine=farm_engine,
            post_engine=post_engine,
            health_monitor=health_monitor,
            settings=settings,
        )
