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
class Rule:
    """予定の最中の振る舞いを決めるルール。条件は全て AND、未指定は無条件。"""

    name: str
    calendars: tuple[str, ...] | None = None
    title: re.Pattern[str] | None = None
    all_day: bool | None = None
    mute: bool = False
    message: str | None = None


@dataclass(frozen=True)
class AnnounceNext:
    within_minutes: int = 0  # 0 なら無効
    max_items: int = 2
    template: str = "このあと{start}から、{title}です。"
    calendars: tuple[str, ...] | None = None
    exclude_title: re.Pattern[str] | None = None


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
    message: str = "{hour}時です。"
    rules: tuple[Rule, ...] = ()
    announce_next: AnnounceNext = field(default_factory=AnnounceNext)
    tts: TTSConfig = field(default_factory=TTSConfig)
    player: str = "windows"  # windows | paplay
    chime_wav: Path | None = None


def _names(v: Any) -> tuple[str, ...] | None:
    if v is None:
        return None
    return (v,) if isinstance(v, str) else tuple(v)


def _regex(v: Any) -> re.Pattern[str] | None:
    return re.compile(v) if v else None


def _time(v: Any) -> time:
    # YAML は 23:00 をクォート無しだと 60 進数として int にしてしまう
    if isinstance(v, int):
        return time(v // 60, v % 60)
    return time.fromisoformat(str(v))


def parse_config(raw: dict[str, Any]) -> Config:
    calendars = tuple(CalendarSource(name=c["name"], url=c["url"]) for c in raw.get("calendars") or [])
    names = [c.name for c in calendars]
    if len(set(names)) != len(names):
        raise ValueError(f"calendars の name が重複しています: {names}")

    qh = raw.get("quiet_hours")
    quiet = QuietHours(_time(qh["start"]), _time(qh["end"])) if qh else None

    rules = []
    for i, r in enumerate(raw.get("rules") or []):
        action = r.get("action", "mute")
        if action == "mute":
            mute, message = True, None
        elif isinstance(action, dict) and "message" in action:
            mute, message = False, action["message"]
        else:
            raise ValueError(f"rules[{i}].action は mute か {{message: ...}} です: {action!r}")
        rules.append(
            Rule(
                name=r.get("name", f"rule{i}"),
                calendars=_names(r.get("calendar")),
                title=_regex(r.get("title")),
                all_day=r.get("all_day"),
                mute=mute,
                message=message,
            )
        )

    an = raw.get("announce_next") or {}
    announce = AnnounceNext(
        within_minutes=an.get("within_minutes", 0),
        max_items=an.get("max_items", 2),
        template=an.get("template", AnnounceNext.template),
        calendars=_names(an.get("calendar")),
        exclude_title=_regex(an.get("exclude_title")),
    )

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

    chime = raw.get("chime_wav")
    return Config(
        timezone=ZoneInfo(raw.get("timezone", "Asia/Tokyo")),
        calendars=calendars,
        refresh_minutes=raw.get("refresh_minutes", 10),
        quiet_hours=quiet,
        message=raw.get("message", Config.message),
        rules=tuple(rules),
        announce_next=announce,
        tts=tts,
        player=raw.get("player", "windows"),
        chime_wav=Path(chime).expanduser() if chime else None,
    )


def load_config(path: Path) -> Config:
    with path.open(encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})
