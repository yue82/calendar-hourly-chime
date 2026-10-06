"""指定時刻に向けたカウントダウン (鳴らし方は countdown.cues)。

指定方法は 2 つ:
- コマンド (`calendar-hourly-chime countdown 15:30`): STATE_DIR/countdowns.json に保存する
- カレンダー: countdown: true のカレンダーの予定の開始時刻 (時報・予定通知とは独立)

夜間・休日に関係なく鳴らす。自分のタスク用なので予定名も読む。
予定中の扱いは countdown.when_busy、重なりは priority に従う。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import STATE_DIR, Config
from .ical import Event
from .rules import Cue, apply_busy, busy_at, format_spec, spoken_duration, spoken_time

STORE = STATE_DIR / "countdowns.json"


@dataclass(frozen=True)
class Countdown:
    at: datetime
    offsets: tuple[int, ...] | None = None  # 鳴らす「N分前」を絞る (None なら countdown.cues の全て)
    label: str | None = None  # 予定名 (無ければ時刻だけ)
    id: str = ""
    created: datetime | None = None  # コマンドで登録した時刻 (カレンダー由来は None)
    source: str = "command"


def cues_of(cd: Countdown, cfg: Config, events: list[Event] = ()) -> list[Cue]:
    """events: 予定中かどうかの判定に使う予定 (時報・予定通知と同じもの)。"""
    cc = cfg.countdown
    reason = f"カウントダウン ({cd.source}{' ' + cd.id if cd.id else ''})"
    titles = [cd.label] if cd.label else []
    out = []
    for spec in cc.cues:
        minutes = int(spec.before.total_seconds() // 60)
        if cd.offsets is not None and spec.before and minutes not in cd.offsets:
            continue
        at = cd.at - spec.before
        label = "CD時刻" if not spec.before else f"CD{spoken_duration(spec.before)}"
        text = format_spec(spec, titles, time=spoken_time(cd.at), n=minutes)
        cue = Cue(label, at, spec.sound, text, reason, "countdown")
        out.append(apply_busy(cue, cc.when_busy, busy_at(at, events), cc.busy_volume))
    return out


def from_events(events: list[Event], cfg: Config) -> list[Countdown]:
    """カウントダウン専用カレンダーの予定の開始時刻。"""
    return [
        Countdown(e.start, label=e.title or None, source=f"{e.calendar}: {e.title}")
        for e in events
        if not e.all_day and e.calendar in cfg.countdown.dedicated
    ]


# --- コマンドで登録したカウントダウンの保存 ---


def load(path: Path = STORE) -> list[Countdown]:
    if not path.exists():
        return []
    out = []
    for r in json.loads(path.read_text(encoding="utf-8")):
        out.append(
            Countdown(
                at=datetime.fromisoformat(r["at"]),
                offsets=tuple(r["offsets"]) if r.get("offsets") is not None else None,
                label=r.get("label"),
                id=r["id"],
                created=datetime.fromisoformat(r["created"]),
            )
        )
    return out


def save(items: list[Countdown], path: Path = STORE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "id": c.id,
            "at": c.at.isoformat(),
            "offsets": list(c.offsets) if c.offsets is not None else None,
            "label": c.label,
            "created": c.created.isoformat(),
        }
        for c in sorted(items, key=lambda c: c.at)
    ]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def add(
    at: datetime, offsets: tuple[int, ...] | None, now: datetime, path: Path = STORE, label: str | None = None
) -> Countdown:
    cd = Countdown(at=at, offsets=offsets, label=label, id=secrets.token_hex(3), created=now)
    save([c for c in load(path) if c.at > now] + [cd], path)
    return cd


def remove(ids: set[str] | None, path: Path = STORE) -> list[Countdown]:
    """ids=None なら全て消す。消したものを返す。"""
    items = load(path)
    gone = [c for c in items if ids is None or c.id in ids]
    save([c for c in items if c not in gone], path)
    return gone


def parse_time(s: str, now: datetime) -> datetime:
    """'15:30' / '1530' (今日、過ぎていれば明日) / '+45' (45 分後) / ISO 形式。"""
    s = s.strip()
    if s.startswith("+"):
        return (now + timedelta(minutes=int(s[1:]))).replace(second=0, microsecond=0)
    if len(s) in (3, 4) and s.isdigit():
        s = f"{s[:-2]}:{s[-2:]}"
    if len(s) <= 5 and ":" in s:
        h, m = map(int, s.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return t if t > now else t + timedelta(days=1)
    t = datetime.fromisoformat(s)
    return t.replace(tzinfo=now.tzinfo) if t.tzinfo is None else t
