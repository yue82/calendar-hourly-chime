"""いつ何を鳴らすか (Cue) を決める。副作用なし。

タイミング・音・文面は設定 (CueSpec) から組み立てる。ここにあるのは仕組みだけ:
- 時報: 平日は hour_chime.weekday_hours、休日は holiday_hours の正時。次の時報までに始まる予定を案内
  (正時より前の weekday_pre_cues / holiday_pre_cues も)
- 予定通知: 予定の開始時刻に向けた event_notice.cues
- カウントダウン: 指定時刻に向けた countdown.cues (countdown.py)
- 鳴らす時刻に (他の) 予定が入っていれば when_busy に従う
- 重なった Cue は priority の順に残す
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .config import Config, CueSpec
from .ical import Event


@dataclass(frozen=True)
class Cue:
    label: str
    at: datetime  # この時刻に合わせて鳴らす
    sound: str | None  # 鳴らす音 (None なら音なし)
    text: str | None  # 読み上げ (None なら読まない)
    reason: str
    kind: str = ""  # hour_chime / event_notice / countdown

    @property
    def silent(self) -> bool:
        return self.sound is None and self.text is None


def spoken_time(t: datetime) -> str:
    return f"{t.hour}時" if t.minute == 0 else f"{t.hour}時{t.minute}分"


def spoken_duration(d: timedelta) -> str:
    s = int(d.total_seconds())
    if s == 0:
        return "ちょうど"
    if s % 60 == 0:
        return f"{s // 60}分前"
    return f"{s}秒前"


def busy_at(t: datetime, events: list[Event], exclude: Sequence[Event] = ()) -> Event | None:
    """t に予定 (終日予定を除く) が入っていればそれを返す。"""
    return next((e for e in events if not e.all_day and e.is_ongoing(t) and e not in exclude), None)


def apply_busy(cue: Cue, when_busy: str, busy: Event | None) -> Cue:
    """予定中なら when_busy (sound_only / skip / normal) に従って変える。"""
    if busy is None or when_busy == "normal":
        return cue
    reason = f"{cue.reason} / 予定中 ({busy.calendar}: {busy.title})"
    if when_busy == "skip":
        return replace(cue, sound=None, text=None, reason=reason)
    return replace(cue, text=None, reason=reason)


def holiday_reason(t: datetime, events: list[Event], cfg: Config) -> str | None:
    """t の日が休日ならその理由。"""
    hd = cfg.holiday
    if t.weekday() in hd.weekdays:
        return "休日 (曜日)"
    for e in events:
        if e.all_day and e.is_ongoing(t) and any(m.matches(e) for m in hd.all_day):
            return f"休日 ({e.calendar}: {e.title})"
    return None


def readable_title(e: Event, cfg: Config) -> str | None:
    """読み上げてよい予定名。busy_only のカレンダーや名前の無い予定は None。"""
    if e.calendar in {c.name for c in cfg.calendars if c.busy_only}:
        return None
    return e.title or None


def format_spec(spec: CueSpec, titles: list[str], **kw: object) -> str | None:
    """予定名があれば text を予定ごとに、無ければ text_untitled を 1 回。"""
    if titles and spec.text:
        return "".join(spec.text.format(title=t, **kw) for t in titles)
    if not titles and spec.text_untitled:
        return spec.text_untitled.format(**kw)
    return None


# --- 時報 ---


def announce_text(start: datetime, end: datetime, events: list[Event], cfg: Config) -> str:
    """start < 開始 <= end の予定の案内文 (時報用。start の正時ちょうどに始まる予定はその最中なので除き、
    次の時報 end ちょうどに始まる予定は含める)。
    予定名が無い/読めない予定は、その時間内に収まる予定名の分かる予定があればそちらを読み
    (開始が end より後でもよい)、無ければ「予定があります」とだけ言う。
    同じ時刻に名前のある予定があれば、名前の無い方は省く。"""
    an = cfg.announce
    by_start: dict[datetime, list[Event]] = {}
    for e in events:
        if not e.all_day and start < e.start <= end:
            by_start.setdefault(e.start, []).append(e)
    titled = [e for e in events if not e.all_day and readable_title(e, cfg)]

    said: set[tuple[datetime, str]] = set()
    lines = []

    def say(t: datetime, title: str) -> None:
        if (t, title) not in said:
            said.add((t, title))
            lines.append(an.item.format(title=title, start=spoken_time(t)))

    for t in sorted(by_start):
        group = by_start[t]
        names = [e for e in group if readable_title(e, cfg)]
        if not names:
            names = sorted(
                (x for x in titled if any(u.start <= x.start and x.end <= u.end for u in group)),
                key=lambda x: x.start,
            )
        if names:
            for e in names:
                say(e.start, readable_title(e, cfg))
        else:
            lines.append(an.item_untitled.format(start=spoken_time(t)))
    return "".join(lines[: an.max_items])


def chime_skip_reason(target: datetime, events: list[Event], cfg: Config) -> str | None:
    """正時 target に時報を鳴らさないならその理由 (予定中かどうかは見ない)。"""
    hc = cfg.hour_chime
    if hol := holiday_reason(target, events, cfg):
        return None if target.hour in hc.holiday_hours else hol
    return None if target.hour in hc.weekday_hours else "時報の時間外"


def next_chime(target: datetime, events: list[Event], cfg: Config) -> datetime:
    """target の次に実際に鳴る時報の時刻 (最大 2 日先まで探す)。"""
    t = target + timedelta(hours=1)
    for _ in range(48):
        if chime_skip_reason(t, events, cfg) is None:
            return t
        t += timedelta(hours=1)
    return t


def plan_hour(target: datetime, events: list[Event], cfg: Config) -> Cue:
    """正時 target の時報。音 + 「N時です。」+ 次の時報までに始まる予定。"""
    hc = cfg.hour_chime
    if reason := chime_skip_reason(target, events, cfg):
        return Cue("時報", target, None, None, reason, "hour_chime")
    text = hc.text.format(hour=target.hour)
    if hc.announce:
        text += announce_text(target, next_chime(target, events, cfg), events, cfg)
    cue = Cue("時報", target, hc.sound, text, holiday_reason(target, events, cfg) or "平日", "hour_chime")
    return apply_busy(cue, hc.when_busy, busy_at(target, events))


def plan_hour_pre(target: datetime, events: list[Event], cfg: Config) -> list[Cue]:
    """正時 target より前に鳴らす時報 (平日は weekday_pre_cues、休日は holiday_pre_cues)。
    その正時の時報を鳴らさないなら鳴らさない。"""
    hc = cfg.hour_chime
    if chime_skip_reason(target, events, cfg):
        return []
    hol = holiday_reason(target, events, cfg)
    cues = []
    for spec in hc.holiday_pre_cues if hol else hc.weekday_pre_cues:
        at = target - spec.before
        text = spec.text.format(hour=target.hour) if spec.text else None
        cue = Cue(f"時報{spoken_duration(spec.before)}", at, spec.sound, text, hol or "平日", "hour_chime_pre")
        cues.append(apply_busy(cue, hc.when_busy, busy_at(at, events)))
    return cues


# --- 予定通知 ---


def plan_event(start: datetime, group: list[Event], events: list[Event], cfg: Config) -> list[Cue]:
    """start に始まる予定 (group) の予定通知。夜間・休日も鳴らす。"""
    en = cfg.event_notice
    titles = [t for t in (readable_title(e, cfg) for e in group) if t]
    cues = []
    for spec in en.cues:
        at = start - spec.before
        label = "予定開始" if not spec.before else f"予定{spoken_duration(spec.before)}"
        cue = Cue(label, at, spec.sound, format_spec(spec, titles, start=spoken_time(start)), "予定通知", "event_notice")
        cues.append(apply_busy(cue, en.when_busy, busy_at(at, events, exclude=group)))
    return cues


def plan_window(start: datetime, end: datetime, events: list[Event], cfg: Config) -> list[Cue]:
    """start <= at < end の時報・予定通知 (重なりの解決前、鳴らさないものも含む)。"""
    cues: list[Cue] = []
    hc = cfg.hour_chime
    pre_lead = max((s.before for s in (*hc.weekday_pre_cues, *hc.holiday_pre_cues)), default=timedelta(0))
    t = start.replace(minute=0, second=0, microsecond=0)
    while t < end + pre_lead:
        if start <= t < end:
            cues.append(plan_hour(t, events, cfg))
        cues += [c for c in plan_hour_pre(t, events, cfg) if start <= c.at < end]
        t += timedelta(hours=1)

    lead = max((s.before for s in cfg.event_notice.cues), default=timedelta(0))
    groups: dict[datetime, list[Event]] = {}
    for e in events:
        if not e.all_day and start <= e.start <= end + lead:
            groups.setdefault(e.start, []).append(e)
    cues += [c for s, g in groups.items() for c in plan_event(s, g, events, cfg) if start <= c.at < end]
    return sorted(cues, key=lambda c: c.at)


# --- 重なりの解決 ---


def resolve(cues: list[Cue], cfg: Config) -> list[Cue]:
    """cfg.conflict 以内に重なった Cue は、priority の高い方だけ残す。"""
    rank = {k: i for i, k in enumerate(cfg.priority)}
    kept: list[Cue] = []
    out = []
    for c in sorted(cues, key=lambda c: (rank.get(c.kind, len(rank)), c.at)):
        if not c.silent:
            winner = next(
                (k for k in kept if rank.get(k.kind) < rank.get(c.kind) and abs(k.at - c.at) < cfg.conflict), None
            )
            if winner:
                c = replace(c, sound=None, text=None, reason=f"{winner.label} 優先")
            else:
                kept.append(c)
        out.append(c)
    return sorted(out, key=lambda c: c.at)
