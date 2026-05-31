# TikTok Farm - batch post schedule planner

import random
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

VIDEO_GLOB = ("*.mp4", "*.mov", "*.webm", "*.mkv")


def parse_time_slots(raw: Sequence) -> List[Tuple[str, str]]:
    """Normalize time slots from settings or API ([start, end] pairs)."""
    slots: List[Tuple[str, str]] = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            slots.append((str(item[0]).strip(), str(item[1]).strip()))
    return slots


def scan_video_files(folder: Path) -> List[Path]:
    """List video files in folder, sorted by name."""
    if not folder.is_dir():
        return []
    found: List[Path] = []
    for pattern in VIDEO_GLOB:
        found.extend(folder.glob(pattern))
    return sorted({p.resolve() for p in found}, key=lambda p: p.name.lower())


def _random_minute_in_slot(start_str: str, end_str: str) -> int:
    sh, sm = map(int, start_str.split(":")[:2])
    eh, em = map(int, end_str.split(":")[:2])
    slot_start = sh * 60 + sm
    slot_end = eh * 60 + em
    if slot_end <= slot_start:
        slot_end = slot_start + 60
    return random.randint(slot_start, min(slot_end, 23 * 60 + 59))


def _round_to_5_minutes(dt: datetime) -> datetime:
    minute = (dt.minute // 5) * 5
    return dt.replace(minute=minute, second=0, microsecond=0)


def build_post_schedule(
    count: int,
    posts_per_day: int,
    time_slots: Sequence,
    start_date: Optional[datetime] = None,
) -> List[datetime]:
    """
    Assign one datetime per video across days and configured time slots.
    """
    if count <= 0:
        return []

    slots = parse_time_slots(time_slots)
    if not slots:
        slots = [("08:00", "11:00"), ("14:00", "17:00"), ("19:00", "22:00")]

    posts_per_day = max(1, min(posts_per_day, len(slots) * 3))
    active_slots = slots[:posts_per_day] if posts_per_day <= len(slots) else slots

    base = start_date or datetime.now()
    if base.tzinfo:
        base = base.replace(tzinfo=None)
    day_cursor = base.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_cursor < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
        day_cursor = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    schedule: List[datetime] = []
    posts_today = 0
    slot_index = 0

    for _ in range(count):
        while True:
            start_str, end_str = active_slots[slot_index % len(active_slots)]
            minute_of_day = _random_minute_in_slot(start_str, end_str)
            run_at = day_cursor + timedelta(
                minutes=minute_of_day,
            )
            run_at = _round_to_5_minutes(run_at)
            if run_at > datetime.now() + timedelta(minutes=20):
                schedule.append(run_at)
                break
            # Same day too soon — try next day
            day_cursor += timedelta(days=1)

        posts_today += 1
        slot_index += 1
        if posts_today >= posts_per_day:
            posts_today = 0
            slot_index = 0
            day_cursor += timedelta(days=1)

    return schedule
