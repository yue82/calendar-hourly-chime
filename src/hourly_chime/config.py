"""設定ファイル (YAML) の読み込み。"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hourly-chime" / "config.yaml"
)
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hourly-chime"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "hourly-chime"


@dataclass(frozen=True)
class CalendarSource:
    name: str
    url: str = field(repr=False)  # 非公開 URL を含むことがあるので repr に出さない
    busy_only: bool = False  # 予定名は読まず、時間枠 (予定中かどうか) だけ使う
    countdown: bool = False  # カウントダウン専用 (全予定をカウントダウンにし、時報・予定通知には使わない)


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t < self.end
        # 日付をまたぐ (例: 23:00-07:00)
        return t >= self.start or t < self.end


WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}


@dataclass(frozen=True)
class AllDayMatcher:
    """この条件に合う終日予定がある日を休日にする。条件は AND、省略は無条件。"""

    calendar: str
    title: re.Pattern[str] | None = None
    description: re.Pattern[str] | None = None

    def matches(self, e: Any) -> bool:
        return (
            e.calendar == self.calendar
            and (self.title is None or bool(self.title.search(e.title)))
            and (self.description is None or bool(self.description.search(e.description)))
        )


@dataclass(frozen=True)
class HolidayMode:
    """休日 (指定曜日、または matchers に合う終日予定がある日) は hours の正時だけ時報を鳴らす。"""

    hours: tuple[int, ...]
    weekdays: frozenset[int] = frozenset()
    matchers: tuple[AllDayMatcher, ...] = ()


@dataclass(frozen=True)
class CountdownConfig:
    offsets: tuple[int, ...] = (30, 20, 10, 5, 2, 1)  # 分前
    with_time: tuple[int, ...] = (30, 10)  # 時刻も読む「N分前」
    dedicated: frozenset[str] = frozenset()  # countdown: true のカレンダー (全予定が対象)


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
    countdown: CountdownConfig = field(default_factory=CountdownConfig)
    announce_template: str = "{start}から、{title}です。"
    event_title_template: str = "{title}です。"  # 予定通知 20 秒前
    announce_untitled_template: str = "{start}から、予定があります。"
    announce_max: int = 3
    tts: TTSConfig = field(default_factory=TTSConfig)
    player: str = "windows"  # windows | paplay


def _time(v: Any) -> time:
    # YAML は 23:00 をクォート無しだと 60 進数として int にしてしまう
    if isinstance(v, int):
        return time(v // 60, v % 60)
    return time.fromisoformat(str(v))


def parse_config(raw: dict[str, Any], secrets: dict[str, Any] | None = None) -> Config:
    """raw: config.yaml、secrets: secrets.yaml。カレンダーの url は secrets.calendars.<name> を優先する。"""
    secret_urls = (secrets or {}).get("calendars") or {}
    calendars = []
    for c in raw.get("calendars") or []:
        url = secret_urls.get(c["name"]) or c.get("url")
        if not url:
            # 時報自体は止めない
            log.warning("カレンダー %s の url がありません (secrets.yaml の calendars.%s に書く)。スキップします", c["name"], c["name"])
            continue
        calendars.append(
            CalendarSource(
                name=c["name"],
                url=url,
                busy_only=bool(c.get("busy_only", False)),
                countdown=bool(c.get("countdown", False)),
            )
        )
    calendars = tuple(calendars)
    names = [c.name for c in calendars]
    if len(set(names)) != len(names):
        raise ValueError(f"calendars の name が重複しています: {names}")

    qh = raw.get("quiet_hours")
    quiet = QuietHours(_time(qh["start"]), _time(qh["end"])) if qh else None

    hd = raw.get("holiday")
    holiday = None
    if hd:
        try:
            weekdays = frozenset(WEEKDAYS[str(w).lower()] for w in hd.get("weekdays") or [])
        except KeyError as e:
            raise ValueError(f"holiday.weekdays に不明な曜日: {e}") from None
        matchers = tuple(
            AllDayMatcher(
                calendar=m["calendar"],
                title=re.compile(m["title"]) if m.get("title") else None,
                description=re.compile(m["description"]) if m.get("description") else None,
            )
            for m in hd.get("all_day") or []
        )
        holiday = HolidayMode(hours=tuple(sorted(hd.get("hours") or [])), weekdays=weekdays, matchers=matchers)
        if not holiday.hours:
            raise ValueError("holiday.hours が空です")
        unknown = {m.calendar for m in matchers} - set(names)
        if unknown:
            log.warning("holiday.all_day に読めないカレンダー: %s", sorted(unknown))

    cd = raw.get("countdown") or {}
    countdown = CountdownConfig(
        offsets=tuple(cd.get("offsets") or CountdownConfig.offsets),
        with_time=tuple(cd.get("with_time") or CountdownConfig.with_time),
        dedicated=frozenset(c.name for c in calendars if c.countdown),
    )

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
        countdown=countdown,
        announce_template=an.get("template", Config.announce_template),
        event_title_template=an.get("event_title_template", Config.event_title_template),
        announce_untitled_template=an.get("untitled_template", Config.announce_untitled_template),
        announce_max=an.get("max_items", Config.announce_max),
        tts=tts,
        player=raw.get("player", "windows"),
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: Path, secrets_path: Path | None = None) -> Config:
    """secrets_path 省略時は config と同じディレクトリの secrets.yaml (無ければ使わない)。"""
    raw = _read_yaml(path)
    secrets_path = secrets_path or path.with_name("secrets.yaml")
    secrets = None
    if secrets_path.exists():
        if secrets_path.stat().st_mode & 0o077:
            log.warning("%s が他ユーザーから読めます。chmod 600 してください", secrets_path)
        secrets = _read_yaml(secrets_path)
    return parse_config(raw, secrets)
