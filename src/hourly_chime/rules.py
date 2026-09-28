"""正時 T に向けた各タイミング (5分前・2分前・15秒前・ちょうど) で何を鳴らすかを決める。副作用なし。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Config
from .ical import Event


@dataclass(frozen=True)
class Slot:
    name: str
    offset: timedelta  # 正時からのずれ
    sound: str  # sounds.py の音の名前 (予定中はこれだけ鳴らす)
    voice: str  # 予定が無いときの読み上げ
    announce_hour: int | None  # T + この時間 の 1 時間枠に始まる予定を読み上げる
    chime_before_voice: bool = False  # 読み上げ時も先に音を鳴らす


SLOTS = (
    Slot("5分前", timedelta(minutes=-5), "popopopopo", "{hour}時5分前です。", announce_hour=0),
    Slot("2分前", timedelta(minutes=-2), "popo", "2分前です。", announce_hour=None),
    Slot("15秒前", timedelta(seconds=-15), "poon", "15秒前です。", announce_hour=None),
    Slot("正時", timedelta(0), "pipipipoon", "{hour}時です。", announce_hour=1, chime_before_voice=True),
)


@dataclass(frozen=True)
class SlotPlan:
    slot: Slot
    at: datetime  # この時刻に合わせて鳴らす
    sound: str | None  # 鳴らす音 (None なら音なし)
    text: str | None  # 読み上げ (None なら読まない)
    reason: str

    @property
    def silent(self) -> bool:
        return self.sound is None and self.text is None


def spoken_time(t: datetime) -> str:
    return f"{t.hour}時" if t.minute == 0 else f"{t.hour}時{t.minute}分"


def busy_at(t: datetime, events: list[Event]) -> Event | None:
    """t に予定 (終日予定を除く) が入っていればそれを返す。"""
    return next((e for e in events if not e.all_day and e.is_ongoing(t)), None)


def events_starting_in(start: datetime, events: list[Event], cfg: Config) -> list[Event]:
    busy_only = {c.name for c in cfg.calendars if c.busy_only}
    end = start + timedelta(hours=1)
    out = [
        e for e in events if not e.all_day and e.calendar not in busy_only and start <= e.start < end
    ]
    return out[: cfg.announce_max]


def plan_hour(target: datetime, events: list[Event], cfg: Config) -> list[SlotPlan]:
    def skip_all(reason: str) -> list[SlotPlan]:
        return [SlotPlan(s, target + s.offset, None, None, reason) for s in SLOTS]

    if cfg.quiet_hours and cfg.quiet_hours.contains(target.time()):
        return skip_all("quiet_hours")

    mute_cals = {c.name for c in cfg.calendars if c.mute_all_day}
    for e in events:
        if e.all_day and e.calendar in mute_cals and e.is_ongoing(target):
            return skip_all(f"終日予定 ({e.calendar}: {e.title})")

    plans = []
    for s in SLOTS:
        at = target + s.offset
        if busy := busy_at(at, events):
            plans.append(SlotPlan(s, at, s.sound, None, f"予定中 ({busy.calendar}: {busy.title})"))
            continue
        text = s.voice.format(hour=target.hour)
        if s.announce_hour is not None:
            for e in events_starting_in(target + timedelta(hours=s.announce_hour), events, cfg):
                text += cfg.announce_template.format(title=e.title, calendar=e.calendar, start=spoken_time(e.start))
        plans.append(SlotPlan(s, at, s.sound if s.chime_before_voice else None, text, "読み上げ"))
    return plans
