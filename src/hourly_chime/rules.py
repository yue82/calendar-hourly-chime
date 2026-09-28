"""時刻と予定から、何を喋るか (喋らないか) を決める。副作用なし。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .ical import Event
from .config import Config, Rule


@dataclass(frozen=True)
class Decision:
    text: str | None  # None なら鳴らさない
    reason: str


def spoken_time(t: datetime) -> str:
    return f"{t.hour}時" if t.minute == 0 else f"{t.hour}時{t.minute}分"


def time_vars(t: datetime) -> dict[str, object]:
    h12 = t.hour % 12 or 12
    return {"hour": t.hour, "hour12": h12, "ampm": "午前" if t.hour < 12 else "午後"}


def rule_matches(rule: Rule, ev: Event) -> bool:
    if rule.calendars is not None and ev.calendar not in rule.calendars:
        return False
    if rule.title is not None and not rule.title.search(ev.title):
        return False
    if rule.all_day is not None and ev.all_day != rule.all_day:
        return False
    return True


def upcoming(now: datetime, events: list[Event], cfg: Config) -> list[Event]:
    an = cfg.announce_next
    if an.within_minutes <= 0:
        return []
    limit = now + timedelta(minutes=an.within_minutes)
    out = [
        e
        for e in events
        if not e.all_day
        and now < e.start <= limit
        and (an.calendars is None or e.calendar in an.calendars)
        and not (an.exclude_title and an.exclude_title.search(e.title))
    ]
    return out[: an.max_items]


def decide(now: datetime, events: list[Event], cfg: Config) -> Decision:
    if cfg.quiet_hours and cfg.quiet_hours.contains(now.time()):
        return Decision(None, "quiet_hours")

    vars_ = time_vars(now)
    text = cfg.message.format(**vars_)
    reason = "default"

    ongoing = [e for e in events if e.is_ongoing(now)]
    for rule in cfg.rules:
        hit = next((e for e in ongoing if rule_matches(rule, e)), None)
        if hit is None:
            continue
        if rule.mute:
            return Decision(None, f"rule:{rule.name} ({hit.calendar}: {hit.title})")
        text = rule.message.format(**vars_, title=hit.title, calendar=hit.calendar)
        reason = f"rule:{rule.name} ({hit.calendar}: {hit.title})"
        break

    for e in upcoming(now, events, cfg):
        text += cfg.announce_next.template.format(
            **vars_,
            title=e.title,
            calendar=e.calendar,
            start=spoken_time(e.start),
            in_minutes=int((e.start - now).total_seconds() // 60),
        )
    return Decision(text, reason)
