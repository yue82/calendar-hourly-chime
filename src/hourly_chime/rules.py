"""いつ何を鳴らすか (Cue) を決める。副作用なし。

- 時報: 平日は毎正時、休日は holiday.hours の正時。ピピピポーン「N時です。」+ 次の時報までに始まる予定
- 予定通知: 予定の 5分前・2分前・20秒前・開始時 (夜間・休日も)。時報と重なったら時報を優先
鳴らす時刻に (他の) 予定が入っていれば、読み上げずに音だけ鳴らす。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Config
from .ical import Event

# 予定通知の Cue が時報からこの範囲にあれば鳴らさない (時報優先)
CONFLICT = timedelta(seconds=10)


@dataclass(frozen=True)
class Slot:
    name: str
    offset: timedelta  # 基準時刻からのずれ
    sound: str  # sounds.py の音の名前 (予定中はこれだけ鳴らす)
    speak: str | None = None  # 何を読むか: None=読まない / "announce"=「H時M分から、〇〇です。」 / "title"=「〇〇です。」


HOUR_SLOT = Slot("時報", timedelta(0), "pipipipoon")

# 音の回数で残り時間を表す (ポポポポポ=5分前、ポポ=2分前)。開始時は会議が始まっている可能性があるので言葉なし
EVENT_SLOTS = (
    Slot("予定5分前", timedelta(minutes=-5), "popopopopo", speak="announce"),
    Slot("予定2分前", timedelta(minutes=-2), "popo"),
    Slot("予定20秒前", timedelta(seconds=-20), "poon", speak="title"),
    Slot("予定開始", timedelta(0), "pipoon"),
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


def busy_at(t: datetime, events: list[Event], exclude: Sequence[Event] = ()) -> Event | None:
    """t に予定 (終日予定を除く) が入っていればそれを返す。"""
    return next((e for e in events if not e.all_day and e.is_ongoing(t) and e not in exclude), None)


def holiday_reason(t: datetime, events: list[Event], cfg: Config) -> str | None:
    """t の日が休日ならその理由。"""
    hd = cfg.holiday
    if hd is None:
        return None
    if t.weekday() in hd.weekdays:
        return "休日 (曜日)"
    for e in events:
        if e.all_day and e.is_ongoing(t) and any(m.matches(e) for m in hd.matchers):
            return f"休日 ({e.calendar}: {e.title})"
    return None


def readable_title(e: Event, cfg: Config) -> str | None:
    """読み上げてよい予定名。busy_only のカレンダーや名前の無い予定は None。"""
    if e.calendar in {c.name for c in cfg.calendars if c.busy_only}:
        return None
    return e.title or None


def _describe_group(t: datetime, group: list[Event], cfg: Config) -> list[str]:
    titles = [x for x in (readable_title(e, cfg) for e in group) if x]
    if titles:
        return [cfg.announce_template.format(title=x, start=spoken_time(t)) for x in titles]
    return [cfg.announce_untitled_template.format(start=spoken_time(t))]


def announce_text(start: datetime, end: datetime, events: list[Event], cfg: Config) -> str:
    """start <= 開始 < end の予定の案内文。予定名が無い/読めない予定は「予定があります」とだけ言う。
    同じ時刻に名前のある予定があれば、名前の無い方は省く。"""
    by_start: dict[datetime, list[Event]] = {}
    for e in events:
        if not e.all_day and start <= e.start < end:
            by_start.setdefault(e.start, []).append(e)
    lines = [x for t in sorted(by_start) for x in _describe_group(t, by_start[t], cfg)]
    return "".join(lines[: cfg.announce_max])


def chime_skip_reason(target: datetime, events: list[Event], cfg: Config) -> str | None:
    """正時 target に時報を鳴らさないならその理由 (予定中かどうかは見ない)。"""
    if hol := holiday_reason(target, events, cfg):
        # 休日: 指定の正時だけ (quiet_hours より優先)
        return None if target.hour in cfg.holiday.hours else hol
    if cfg.quiet_hours and cfg.quiet_hours.contains(target.time()):
        return "quiet_hours"
    return None


def next_chime(target: datetime, events: list[Event], cfg: Config) -> datetime:
    """target の次に実際に鳴る時報の時刻 (最大 2 日先まで探す)。"""
    t = target + timedelta(hours=1)
    for _ in range(48):
        if chime_skip_reason(t, events, cfg) is None:
            return t
        t += timedelta(hours=1)
    return t


def plan_hour(target: datetime, events: list[Event], cfg: Config) -> Cue:
    """正時 target の時報。ピピピポーン「N時です。」+ 次の時報までに始まる予定。"""
    s = HOUR_SLOT
    if reason := chime_skip_reason(target, events, cfg):
        return Cue(s.name, target, None, None, reason)
    if busy := busy_at(target, events):
        return Cue(s.name, target, s.sound, None, f"予定中 ({busy.calendar}: {busy.title})")
    reason = holiday_reason(target, events, cfg) or "平日"
    text = f"{target.hour}時です。" + announce_text(target, next_chime(target, events, cfg), events, cfg)
    return Cue(s.name, target, s.sound, text, reason)


def plan_event(start: datetime, group: list[Event], events: list[Event], cfg: Config) -> list[Cue]:
    """start に始まる予定 (group) の予定通知。夜間・休日も鳴らす。
    鳴らす時刻に他の予定が入っていれば音だけ。"""
    texts = {
        "announce": "".join(_describe_group(start, group, cfg)),
        # 予定名が無ければ 20 秒前は音だけ
        "title": "".join(
            cfg.event_title_template.format(title=t) for t in (readable_title(e, cfg) for e in group) if t
        ),
    }
    cues = []
    for s in EVENT_SLOTS:
        at = start + s.offset
        if other := busy_at(at, events, exclude=group):
            cues.append(Cue(s.name, at, s.sound, None, f"他の予定中 ({other.calendar}: {other.title})"))
            continue
        cues.append(Cue(s.name, at, s.sound, texts.get(s.speak) or None, "予定通知"))
    return cues


def plan_window(start: datetime, end: datetime, events: list[Event], cfg: Config) -> list[Cue]:
    """start <= at < end の Cue を時刻順に返す (鳴らさないものも含む)。"""
    hour_cues: list[Cue] = []
    t = start.replace(minute=0, second=0, microsecond=0)
    while t < end:
        if t >= start:
            hour_cues.append(plan_hour(t, events, cfg))
        t += timedelta(hours=1)

    groups: dict[datetime, list[Event]] = {}
    for e in events:
        if not e.all_day and start <= e.start <= end + timedelta(minutes=5):
            groups.setdefault(e.start, []).append(e)
    event_cues = [c for s, g in groups.items() for c in plan_event(s, g, events, cfg) if start <= c.at < end]

    # 時報優先: 鳴らす時報の近くの予定通知は鳴らさない
    audible = [h for h in hour_cues if not h.silent]
    event_cues = [
        c if c.silent or all(abs(c.at - h.at) >= CONFLICT for h in audible) else Cue(c.label, c.at, None, None, "時報優先")
        for c in event_cues
    ]
    return sorted(hour_cues + event_cues, key=lambda c: c.at)
