"""設定ファイル (YAML) の読み込み。

ルールのタイミング・音・文面・優先順は全て設定で持ち、コードはその仕組みだけを持つ。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

log = logging.getLogger(__name__)

APP = "calendar-hourly-chime"
DEFAULT_CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP / "config.yaml"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP

WHEN_BUSY = ("sound_only", "skip", "normal")
KINDS = ("countdown", "event_notice", "hour_chime")  # 既定の優先順
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}


def parse_duration(v: Any) -> timedelta:
    """'5m' / '20s' / '1h' / 秒数 を timedelta に。"""
    if isinstance(v, (int, float)):
        return timedelta(seconds=v)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hms]?)\s*", str(v))
    if not m:
        raise ValueError(f"時間の書式が不正です: {v!r} (例: 5m, 20s)")
    unit = {"h": "hours", "m": "minutes", "s": "seconds", "": "seconds"}[m.group(2)]
    return timedelta(**{unit: float(m.group(1))})


@dataclass(frozen=True)
class CalendarSource:
    name: str
    url: str = field(repr=False)  # 非公開 URL を含むことがあるので repr に出さない
    busy_only: bool = False  # 予定名は読まず、時間枠だけ使う
    countdown: bool = False  # カウントダウン専用 (全予定をカウントダウンにし、時報・予定通知には使わない)


@dataclass(frozen=True)
class CueSpec:
    """基準時刻の before 前に sound を鳴らし text を読む。予定名が無いときは text_untitled。"""

    before: timedelta
    sound: str | None = None
    text: str | None = None
    text_untitled: str | None = None


def _c(before: str, sound: str | None, text: str | None = None, text_untitled: str | None = None) -> CueSpec:
    return CueSpec(parse_duration(before), sound, text, text_untitled)


@dataclass(frozen=True)
class HourChimeConfig:
    weekday_hours: tuple[int, ...] = tuple(range(8, 21))
    holiday_hours: tuple[int, ...] = (8, 12, 16, 20)
    sound: str | None = "pipipipoon"
    text: str = "{hour}時です。"
    announce: bool = True  # 次の時報までに始まる予定を続けて読む
    when_busy: str = "sound_only"
    busy_volume: float = 0.2  # 予定中の音量の倍率
    # 平日に正時より前に鳴らすもの (text では {hour} が使える)。予定中の扱いは when_busy
    weekday_pre_cues: tuple[CueSpec, ...] = (_c("5m", "popopopopo"),)


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
class HolidayConfig:
    weekdays: frozenset[int] = frozenset()
    all_day: tuple[AllDayMatcher, ...] = ()


@dataclass(frozen=True)
class EventNoticeConfig:
    cues: tuple[CueSpec, ...] = (
        _c("5m", "popopopopo", "{start}から、{title}です。", "{start}から、予定があります。"),
        _c("2m", "popo"),
        _c("20s", "poon", "{title}です。"),
        _c("0s", "pipoon"),
    )
    when_busy: str = "sound_only"
    busy_volume: float = 1.0  # 予定中の音量の倍率


_CD_WITH_TIME = ("{time}の{title}まで、あと{n}分です。", "{time}まで、あと{n}分です。")
_CD_PLAIN = ("{title}まで、あと{n}分です。", "あと{n}分です。")


@dataclass(frozen=True)
class CountdownConfig:
    cues: tuple[CueSpec, ...] = (
        _c("30m", "pin", *_CD_WITH_TIME),
        _c("20m", "pin", *_CD_PLAIN),
        _c("10m", "pinpin", *_CD_WITH_TIME),
        _c("5m", "pinpin", *_CD_PLAIN),
        _c("2m", "pinpinpin", *_CD_PLAIN),
        _c("1m", "pinpinpin", *_CD_PLAIN),
        _c("0s", "pipoon", "{time}、{title}の時間です。", "{time}です。"),
    )
    when_busy: str = "sound_only"
    busy_volume: float = 1.0  # 予定中の音量の倍率
    dedicated: frozenset[str] = frozenset()  # countdown: true のカレンダー


@dataclass(frozen=True)
class AnnounceConfig:
    """時報で読む予定案内。"""

    item: str = "{start}から、{title}です。"
    item_untitled: str = "{start}から、予定があります。"
    max_items: int = 3
    # その日最後の時報 (次の時報が翌日) では、翌日のこの時刻までに始まる予定も読む
    next_day_until: time = time(12, 0)
    next_day_prefix: str = "明日は、"


@dataclass(frozen=True)
class SoundOverride:
    path: Path
    anchor: float = 0.0  # この秒の位置を指定時刻ちょうどに合わせる


@dataclass(frozen=True)
class DndConfig:
    """予定中に Windows の応答不可をオンにする。"""

    enabled: bool = False
    first_run_off: bool = True  # その日最初の確認で、予定中でなければオフにする
    before: timedelta = timedelta(seconds=30)  # 予定の開始のこれだけ前にオン
    after: timedelta = timedelta(seconds=30)  # 予定の終了のこれだけ後にオフ


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
    hour_chime: HourChimeConfig = field(default_factory=HourChimeConfig)
    holiday: HolidayConfig = field(default_factory=HolidayConfig)
    event_notice: EventNoticeConfig = field(default_factory=EventNoticeConfig)
    countdown: CountdownConfig = field(default_factory=CountdownConfig)
    announce: AnnounceConfig = field(default_factory=AnnounceConfig)
    priority: tuple[str, ...] = KINDS
    conflict: timedelta = timedelta(seconds=10)
    sounds: dict[str, SoundOverride] = field(default_factory=dict)
    dnd: DndConfig = field(default_factory=DndConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    player: str = "windows"  # windows | paplay


def _time(v: Any) -> time:
    # YAML は 12:00 をクォート無しだと 60 進数として int にしてしまう
    if isinstance(v, int):
        return time(v // 60, v % 60)
    return time.fromisoformat(str(v))


def _when_busy(v: Any, where: str) -> str:
    v = v or "sound_only"
    if v not in WHEN_BUSY:
        raise ValueError(f"{where}.when_busy は {'/'.join(WHEN_BUSY)} のどれか: {v!r}")
    return v


def _volume(v: Any, where: str) -> float:
    v = float(v)
    if not 0 <= v <= 1:
        raise ValueError(f"{where}.busy_volume は 0〜1: {v}")
    return v


def _hours(v: Any, where: str) -> tuple[int, ...]:
    hours = tuple(sorted(int(h) for h in v))
    if any(not 0 <= h <= 23 for h in hours):
        raise ValueError(f"{where} は 0〜23: {hours}")
    return hours


def _cues(raw: Any, default: tuple[CueSpec, ...]) -> tuple[CueSpec, ...]:
    if raw is None:
        return default
    cues = tuple(
        CueSpec(parse_duration(c.get("before", 0)), c.get("sound"), c.get("text"), c.get("text_untitled"))
        for c in raw
    )
    return tuple(sorted(cues, key=lambda c: c.before, reverse=True))


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

    hc = raw.get("hour_chime") or {}
    d = HourChimeConfig()
    hour_chime = HourChimeConfig(
        weekday_hours=_hours(hc.get("weekday_hours", d.weekday_hours), "hour_chime.weekday_hours"),
        holiday_hours=_hours(hc.get("holiday_hours", d.holiday_hours), "hour_chime.holiday_hours"),
        sound=hc.get("sound", d.sound),
        text=hc.get("text", d.text),
        announce=bool(hc.get("announce", d.announce)),
        when_busy=_when_busy(hc.get("when_busy"), "hour_chime"),
        busy_volume=_volume(hc.get("busy_volume", d.busy_volume), "hour_chime"),
        weekday_pre_cues=_cues(hc.get("weekday_pre_cues"), d.weekday_pre_cues),
    )

    hd = raw.get("holiday") or {}
    try:
        weekdays = frozenset(WEEKDAYS[str(w).lower()] for w in hd.get("weekdays") or [])
    except KeyError as e:
        raise ValueError(f"holiday.weekdays に不明な曜日: {e}") from None
    all_day = tuple(
        AllDayMatcher(
            calendar=m["calendar"],
            title=re.compile(m["title"]) if m.get("title") else None,
            description=re.compile(m["description"]) if m.get("description") else None,
        )
        for m in hd.get("all_day") or []
    )
    if unknown := {m.calendar for m in all_day} - set(names):
        log.warning("holiday.all_day に読めないカレンダー: %s", sorted(unknown))
    holiday = HolidayConfig(weekdays=weekdays, all_day=all_day)

    en = raw.get("event_notice") or {}
    event_notice = EventNoticeConfig(
        cues=_cues(en.get("cues"), EventNoticeConfig.cues),
        when_busy=_when_busy(en.get("when_busy"), "event_notice"),
        busy_volume=_volume(en.get("busy_volume", 1.0), "event_notice"),
    )

    cd = raw.get("countdown") or {}
    countdown = CountdownConfig(
        cues=_cues(cd.get("cues"), CountdownConfig.cues),
        when_busy=_when_busy(cd.get("when_busy"), "countdown"),
        busy_volume=_volume(cd.get("busy_volume", 1.0), "countdown"),
        dedicated=frozenset(c.name for c in calendars if c.countdown),
    )

    an = raw.get("announce") or {}
    announce = AnnounceConfig(
        item=an.get("item", AnnounceConfig.item),
        item_untitled=an.get("item_untitled", AnnounceConfig.item_untitled),
        max_items=an.get("max_items", AnnounceConfig.max_items),
        next_day_until=_time(an.get("next_day_until", "12:00")),
        next_day_prefix=an.get("next_day_prefix", AnnounceConfig.next_day_prefix),
    )

    priority = tuple(raw.get("priority") or ())
    if unknown := set(priority) - set(KINDS):
        raise ValueError(f"priority に不明な種類: {sorted(unknown)} (使えるもの: {', '.join(KINDS)})")
    priority += tuple(k for k in KINDS if k not in priority)  # 書かれていない種類は既定の順で後ろに

    sounds = {}
    for name, v in (raw.get("sounds") or {}).items():
        if isinstance(v, dict):
            sounds[name] = SoundOverride(Path(v["path"]).expanduser(), float(v.get("anchor", 0)))
        else:
            sounds[name] = SoundOverride(Path(v).expanduser())

    dd = raw.get("do_not_disturb") or {}
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
        hour_chime=hour_chime,
        holiday=holiday,
        event_notice=event_notice,
        countdown=countdown,
        announce=announce,
        priority=priority,
        conflict=timedelta(seconds=float(raw.get("conflict_seconds", 10))),
        sounds=sounds,
        dnd=DndConfig(
            enabled=bool(dd.get("enabled", False)),
            first_run_off=bool(dd.get("first_run_off", True)),
            before=parse_duration(dd.get("before", "30s")),
            after=parse_duration(dd.get("after", "30s")),
        ),
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
