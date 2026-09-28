"""iCal (ICS) の取得・キャッシュ・予定の展開。"""

from __future__ import annotations

import hashlib
import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events
import requests

from .config import CACHE_DIR, CalendarSource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    calendar: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    description: str = ""

    def is_ongoing(self, t: datetime) -> bool:
        return self.start <= t < self.end


def _cache_path(src: CalendarSource) -> Path:
    # URL が変わったら別キャッシュにする
    h = hashlib.sha256(src.url.encode()).hexdigest()[:12]
    return CACHE_DIR / "calendars" / f"{src.name}-{h}.ics"


def fetch(src: CalendarSource, max_age_minutes: float, timeout: float = 5.0) -> bytes | None:
    """キャッシュが新しければそれを、古ければ取得し直す。取得失敗時は古いキャッシュを返す。"""
    path = _cache_path(src)
    if path.exists() and _time.time() - path.stat().st_mtime < max_age_minutes * 60:
        return path.read_bytes()
    try:
        r = requests.get(src.url, timeout=timeout)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return r.content
    except requests.RequestException as e:
        if path.exists():
            log.warning("カレンダー %s の取得に失敗、キャッシュを使います: %s", src.name, e)
            return path.read_bytes()
        log.error("カレンダー %s の取得に失敗 (キャッシュ無し): %s", src.name, e)
        return None


def _to_datetime(v: date | datetime, tz: ZoneInfo) -> datetime:
    if isinstance(v, datetime):
        return v.replace(tzinfo=tz) if v.tzinfo is None else v.astimezone(tz)
    return datetime.combine(v, datetime.min.time(), tz)


def parse_events(
    calendar_name: str, ics: bytes, start: datetime, end: datetime, tz: ZoneInfo
) -> list[Event]:
    cal = icalendar.Calendar.from_ical(ics)
    events = []
    for comp in recurring_ical_events.of(cal).between(start, end):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = comp.decoded("DTSTART")
        all_day = not isinstance(dtstart, datetime)
        s = _to_datetime(dtstart, tz)
        if "DTEND" in comp:
            e = _to_datetime(comp.decoded("DTEND"), tz)
        elif "DURATION" in comp:
            e = s + comp.decoded("DURATION")
        else:
            e = s + (timedelta(days=1) if all_day else timedelta(0))
        events.append(
            Event(
                calendar=calendar_name,
                title=str(comp.get("SUMMARY", "")).strip(),
                start=s,
                end=e,
                all_day=all_day,
                description=str(comp.get("DESCRIPTION", "")).strip(),
            )
        )
    return events


def load_events(
    sources: tuple[CalendarSource, ...],
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    max_age_minutes: float,
) -> list[Event]:
    events: list[Event] = []
    for src in sources:
        ics = fetch(src, max_age_minutes)
        if ics is None:
            continue
        try:
            events += parse_events(src.name, ics, start, end, tz)
        except Exception:
            log.exception("カレンダー %s の解析に失敗", src.name)
    events.sort(key=lambda e: (e.start, e.calendar, e.title))
    return events
