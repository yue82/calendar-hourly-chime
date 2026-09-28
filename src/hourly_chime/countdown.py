"""指定時刻に向けたカウントダウン (30/20/10/5/2/1分前 + ちょうど)。

指定方法は 2 つ:
- コマンド (`hourly-chime countdown 15:30`): STATE_DIR/countdowns.json に保存する
- カレンダー: countdown: true のカレンダーの予定の開始時刻 (時報・予定通知とは独立)

quiet_hours / 休日 / off_days に関係なく鳴らし、重なった時報より優先する。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import STATE_DIR, Config
from .ical import Event
from .rules import Cue, spoken_time

STORE = STATE_DIR / "countdowns.json"
# カウントダウンの Cue からこの範囲にある他の Cue は鳴らさない
CONFLICT = timedelta(seconds=10)


@dataclass(frozen=True)
class Countdown:
    at: datetime
    offsets: tuple[int, ...]  # 分
    id: str = ""
    created: datetime | None = None  # コマンドで登録した時刻 (カレンダー由来は None)
    source: str = "command"


def cues_of(cd: Countdown) -> list[Cue]:
    t = spoken_time(cd.at)
    reason = f"カウントダウン ({cd.source}{' ' + cd.id if cd.id else ''})"
    out = [
        Cue(f"CD{m}分前", cd.at - timedelta(minutes=m), None, f"{t}まで、あと{m}分です。", reason)
        for m in sorted(set(cd.offsets), reverse=True)
    ]
    out.append(Cue("CD時刻", cd.at, "pipoon", f"{t}です。", reason))
    return out


def from_events(events: list[Event], cfg: Config) -> list[Countdown]:
    """カウントダウン専用カレンダーの予定の開始時刻。"""
    return [
        Countdown(e.start, cfg.countdown.offsets, source=f"{e.calendar}: {e.title}")
        for e in events
        if not e.all_day and e.calendar in cfg.countdown.dedicated
    ]


def merge(base: list[Cue], countdown: list[Cue]) -> list[Cue]:
    """カウントダウン優先で合わせて時刻順に並べる。"""
    kept = [c for c in base if c.silent or all(abs(c.at - d.at) >= CONFLICT for d in countdown)]
    return sorted(kept + countdown, key=lambda c: c.at)


# --- コマンドで登録したカウントダウンの保存 ---


def load(path: Path = STORE) -> list[Countdown]:
    if not path.exists():
        return []
    out = []
    for r in json.loads(path.read_text(encoding="utf-8")):
        out.append(
            Countdown(
                at=datetime.fromisoformat(r["at"]),
                offsets=tuple(r["offsets"]),
                id=r["id"],
                created=datetime.fromisoformat(r["created"]),
            )
        )
    return out


def save(items: list[Countdown], path: Path = STORE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": c.id, "at": c.at.isoformat(), "offsets": list(c.offsets), "created": c.created.isoformat()}
        for c in sorted(items, key=lambda c: c.at)
    ]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def add(at: datetime, offsets: tuple[int, ...], now: datetime, path: Path = STORE) -> Countdown:
    cd = Countdown(at=at, offsets=offsets, id=secrets.token_hex(3), created=now)
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
