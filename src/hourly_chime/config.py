"""設定ファイル (YAML) の読み込み。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

DEFAULT_CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hourly-chime" / "config.yaml"
)
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hourly-chime"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "hourly-chime"


@dataclass(frozen=True)
class CalendarSource:
    name: str
    url: str
    busy_only: bool = False  # 予定名は読まず、時間枠 (予定中かどうか) だけ使う


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t < self.end
        # 日付をまたぐ (例: 23:00-07:00)
        return t >= self.start or t < self.end


@dataclass(frozen=True)
class HolidayMode:
    """休日 (指定カレンダーに条件に合う終日予定がある日) は指定の正時だけ鳴らす。"""

    calendars: tuple[str, ...]
    hours: tuple[int, ...]
    title: re.Pattern[str] | None = None
    description: re.Pattern[str] | None = None


@dataclass(frozen=True)
class TTSConfig:
    engine: str = "sapi"  # sapi | voicevox
    sapi_voice: str = "Microsoft Haruka Desktop"
    sapi_rate: int = 0  # -10..10
    voicevox_url: str = "http://127.0.0.1:50021"
    voicevox_speaker: int = 3
    voicevox_speed: float = 1.0


@dataclass(frozen=True)
class Config:
    timezone: ZoneInfo
    calendars: tuple[CalendarSource, ...] = ()
    refresh_minutes: int = 10
    quiet_hours: QuietHours | None = None
    holiday: HolidayMode | None = None
    announce_template: str = "{start}から、{title}です。"
    announce_max: int = 3
    tts: TTSConfig = field(default_factory=TTSConfig)
    player: str = "windows"  # windows | paplay


def _time(v: Any) -> time:
    # YAML は 23:00 をクォート無しだと 60 進数として int にしてしまう
    if isinstance(v, int):
        return time(v // 60, v % 60)
    return time.fromisoformat(str(v))


def parse_config(raw: dict[str, Any]) -> Config:
    calendars = tuple(
        CalendarSource(
            name=c["name"],
            url=c["url"],
            busy_only=bool(c.get("busy_only", False)),
        )
        for c in raw.get("calendars") or []
    )
    names = [c.name for c in calendars]
    if len(set(names)) != len(names):
        raise ValueError(f"calendars の name が重複しています: {names}")

    qh = raw.get("quiet_hours")
    quiet = QuietHours(_time(qh["start"]), _time(qh["end"])) if qh else None

    hd = raw.get("holiday")
    holiday = None
    if hd:
        cals = hd["calendar"]
        holiday = HolidayMode(
            calendars=(cals,) if isinstance(cals, str) else tuple(cals),
            hours=tuple(sorted(hd["hours"])),
            title=re.compile(hd["title"]) if hd.get("title") else None,
            description=re.compile(hd["description"]) if hd.get("description") else None,
        )
        if not holiday.hours:
            raise ValueError("holiday.hours が空です")
        unknown = set(holiday.calendars) - set(names)
        if unknown:
            raise ValueError(f"holiday.calendar に未定義のカレンダー: {sorted(unknown)}")

    an = raw.get("announce") or {}
    t = raw.get("tts") or {}
    sapi = t.get("sapi") or {}
    vv = t.get("voicevox") or {}
    tts = TTSConfig(
        engine=t.get("engine", "sapi"),
        sapi_voice=sapi.get("voice", TTSConfig.sapi_voice),
        sapi_rate=sapi.get("rate", 0),
        voicevox_url=vv.get("url", TTSConfig.voicevox_url).rstrip("/"),
        voicevox_speaker=vv.get("speaker", TTSConfig.voicevox_speaker),
        voicevox_speed=vv.get("speed", 1.0),
    )

    return Config(
        timezone=ZoneInfo(raw.get("timezone", "Asia/Tokyo")),
        calendars=calendars,
        refresh_minutes=raw.get("refresh_minutes", 10),
        quiet_hours=quiet,
        holiday=holiday,
        announce_template=an.get("template", Config.announce_template),
        announce_max=an.get("max_items", Config.announce_max),
        tts=tts,
        player=raw.get("player", "windows"),
    )


def load_config(path: Path) -> Config:
    with path.open(encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})
