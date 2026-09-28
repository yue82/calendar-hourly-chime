"""いつ何を鳴らすか (Cue) を決める。副作用なし。

- 正時 N:00 に向けて 5分前・2分前・15秒前・ちょうど (HOUR_SLOTS)。N:00 に始まる予定が無ければちょうどのみ
- 正時始まりでない予定に向けて 2分前・15秒前・ちょうど (EVENT_SLOTS)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Config
from .ical import Event


@dataclass(frozen=True)
class Slot:
    name: str
    offset: timedelta  # 基準時刻からのずれ
    sound: str  # sounds.py の音の名前
    voice: str  # 読み上げ ({hour} {start} が使える)
    announce_hour: int | None = None  # 正時 T + この時間 の 1 時間枠に始まる予定を読み上げる
    chime_before_voice: bool = False  # 読み上げ時も先に音を鳴らす


HOUR_SLOTS = (
    Slot("5分前", timedelta(minutes=-5), "popopopopo", "{hour}時5分前です。", announce_hour=0),
    Slot("2分前", timedelta(minutes=-2), "popo", "2分前です。"),
    Slot("15秒前", timedelta(seconds=-15), "poon", "15秒前です。"),
    Slot("正時", timedelta(0), "pipipipoon", "{hour}時です。", announce_hour=1, chime_before_voice=True),
)

EVENT_SLOTS = (
    Slot("予定2分前", timedelta(minutes=-2), "popo", "2分前です。"),
    Slot("予定15秒前", timedelta(seconds=-15), "poon", "15秒前です。"),
    Slot("予定開始", timedelta(0), "pipipipoon", "{start}です。", chime_before_voice=True),
)


@dataclass(frozen=True)
class Cue:
    label: str
    at: datetime  # この時刻に合わせて鳴らす
    sound: str | None  # 鳴らす音 (None なら音なし)
    text: str | None  # 読み上げ (None なら読まない)
    reason: str

    @property
    def silent(self) -> bool:
        return self.sound is None and self.text is None


def spoken_time(t: datetime) -> str:
    return f"{t.hour}時" if t.minute == 0 else f"{t.hour}時{t.minute}分"


def _busy_only(cfg: Config) -> set[str]:
    return {c.name for c in cfg.calendars if c.busy_only}


def busy_at(t: datetime, events: list[Event], exclude: Sequence[Event] = ()) -> Event | None:
    """t に予定 (終日予定を除く) が入っていればそれを返す。"""
    return next((e for e in events if not e.all_day and e.is_ongoing(t) and e not in exclude), None)


def off_reason(t: datetime, events: list[Event], cfg: Config) -> str | None:
    """t が休み (鳴らさない) ならその理由。"""
    off = cfg.off
    if off is None:
        return None
    if t.weekday() in off.weekdays:
        return "曜日"
    if off.title:
        for e in events:
            if (
                e.all_day
                and (off.calendars is None or e.calendar in off.calendars)
                and e.is_ongoing(t)
                and off.title.search(e.title)
            ):
                return f"休み ({e.calendar}: {e.title})"
    return None


def holiday_of(t: datetime, events: list[Event], cfg: Config) -> Event | None:
    hd = cfg.holiday
    if hd is None:
        return None
    for e in events:
        if (
            e.all_day
            and e.calendar in hd.calendars
            and e.is_ongoing(t)
            and (hd.title is None or hd.title.search(e.title))
            and (hd.description is None or hd.description.search(e.description))
        ):
            return e
    return None


def readable_title(e: Event, cfg: Config) -> str | None:
    """読み上げてよい予定名。busy_only のカレンダーや名前の無い予定は None。"""
    if e.calendar in _busy_only(cfg):
        return None
    title = cfg.countdown.strip(e.title) if cfg.countdown.matches(e) else e.title
    return title or None


def announce_text(start: datetime, end: datetime, events: list[Event], cfg: Config) -> str:
    """start <= 開始 < end の予定の案内文。予定名が無い/読めない予定は「予定があります」とだけ言う。
    同じ時刻に名前のある予定があれば、名前の無い方は省く。"""
    by_start: dict[datetime, list[Event]] = {}
    for e in events:
        if not e.all_day and start <= e.start < end:
            by_start.setdefault(e.start, []).append(e)
    lines = []
    for t in sorted(by_start):
        titles = [x for x in (readable_title(e, cfg) for e in by_start[t]) if x]
        if titles:
            lines += [cfg.announce_template.format(title=x, start=spoken_time(t)) for x in titles]
        else:
            lines.append(cfg.announce_untitled_template.format(start=spoken_time(t)))
    return "".join(lines[: cfg.announce_max])


def next_holiday_chime(target: datetime, hours: tuple[int, ...]) -> datetime:
    later = [h for h in hours if h > target.hour]
    if later:
        return target.replace(hour=later[0])
    return (target + timedelta(days=1)).replace(hour=hours[0])


def _hour_cue(
    s: Slot, target: datetime, events: list[Event], cfg: Config, announce: tuple[datetime, datetime] | None
) -> Cue:
    at = target + s.offset
    if reason := off_reason(at, events, cfg):
        return Cue(s.name, at, None, None, reason)
    if busy := busy_at(at, events):
        return Cue(s.name, at, s.sound, None, f"予定中 ({busy.calendar}: {busy.title})")
    text = s.voice.format(hour=target.hour)
    if announce:
        text += announce_text(*announce, events, cfg)
    return Cue(s.name, at, s.sound if s.chime_before_voice else None, text, "読み上げ")


def plan_hour(target: datetime, events: list[Event], cfg: Config) -> list[Cue]:
    """正時 target に向けた 4 つの Cue。"""

    def skip(s: Slot, reason: str) -> Cue:
        return Cue(s.name, target + s.offset, None, None, reason)

    if hol := holiday_of(target, events, cfg):
        # 休日: 指定の正時だけ、次の時報までの予定を読む (quiet_hours より優先)
        hours = cfg.holiday.hours
        reason = f"休日 ({hol.title})"
        if target.hour not in hours:
            return [skip(s, reason) for s in HOUR_SLOTS]
        window = (target, next_holiday_chime(target, hours))
        return [
            _hour_cue(s, target, events, cfg, window) if s.offset == timedelta(0) else skip(s, reason)
            for s in HOUR_SLOTS
        ]

    if cfg.quiet_hours and cfg.quiet_hours.contains(target.time()):
        return [skip(s, "quiet_hours") for s in HOUR_SLOTS]

    # N:00 に始まる予定が無ければ、正時だけ鳴らす (途中の予定は予定 Cue、長い予定の中は正時の音のみ)
    has_events = any(not e.all_day and e.start == target for e in events)

    cues = []
    for s in HOUR_SLOTS:
        if not has_events and s.offset != timedelta(0):
            cues.append(skip(s, "N:00 開始の予定なし"))
            continue
        window = None
        if s.announce_hour is not None:
            start = target + timedelta(hours=s.announce_hour)
            window = (start, start + timedelta(hours=1))
        cues.append(_hour_cue(s, target, events, cfg, window))
    return cues


def plan_event(start: datetime, group: list[Event], events: list[Event], cfg: Config) -> list[Cue]:
    """start に始まる予定 (group) に向けた 3 つの Cue。鳴らす時刻に他の予定が入っていれば鳴らさない。"""
    titles = [x for x in (readable_title(e, cfg) for e in group) if x]

    def skip(s: Slot, reason: str) -> Cue:
        return Cue(s.name, start + s.offset, None, None, reason)

    if hol := holiday_of(start, events, cfg):
        return [skip(s, f"休日 ({hol.title})") for s in EVENT_SLOTS]
    if cfg.quiet_hours and cfg.quiet_hours.contains(start.time()):
        return [skip(s, "quiet_hours") for s in EVENT_SLOTS]

    cues = []
    for s in EVENT_SLOTS:
        at = start + s.offset
        if reason := off_reason(at, events, cfg):
            cues.append(skip(s, reason))
        elif other := busy_at(at, events, exclude=group):
            cues.append(skip(s, f"他の予定中 ({other.calendar}: {other.title})"))
        else:
            text = s.voice.format(start=spoken_time(start))
            if s.offset == timedelta(0):
                if titles:
                    text += "".join(cfg.event_start_template.format(title=t) for t in titles)
                else:
                    text += cfg.event_start_untitled_template
            cues.append(Cue(s.name, at, s.sound if s.chime_before_voice else None, text, "予定"))
    return cues


def plan_window(start: datetime, end: datetime, events: list[Event], cfg: Config) -> list[Cue]:
    """start <= at < end の Cue を時刻順に返す (鳴らさないものも含む)。"""
    cues: list[Cue] = []

    # 正時: Cue は T-5分..T にあるので、T が [start, end + 5分] の範囲の正時を見る
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= end + timedelta(minutes=5):
        cues += plan_hour(t, events, cfg)
        t += timedelta(hours=1)

    # 正時始まりでない予定 (開始時刻でまとめる)
    groups: dict[datetime, list[Event]] = {}
    for e in events:
        if cfg.countdown.matches(e):
            continue  # カウントダウン側で鳴らす
        if not e.all_day and (e.start.minute, e.start.second) != (0, 0):
            if start <= e.start <= end + timedelta(minutes=2):
                groups.setdefault(e.start, []).append(e)
    for s, group in groups.items():
        cues += plan_event(s, group, events, cfg)

    return sorted((c for c in cues if start <= c.at < end), key=lambda c: c.at)
