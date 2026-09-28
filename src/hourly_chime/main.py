"""CLI 入口。"""

from __future__ import annotations

import argparse
import os
import subprocess
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import countdown, ical, player, rules, sounds, tts
from .config import DEFAULT_CONFIG_PATH, STATE_DIR, Config, load_config

log = logging.getLogger("hourly_chime")

# スケジューラは 5 分ごと (毎時 x4:00, x9:00) に起動し、起動 30 秒後からの 5 分間の Cue を受け持つ
# (例: 14:54:00 起動 → 14:54:30〜14:59:30)。30 秒は合成・PowerShell 起動の余裕
INTERVAL = timedelta(minutes=5)
PHASE = timedelta(minutes=4)  # 起動時刻の分 (5 で割った余り)
LEAD = timedelta(seconds=30)
GRACE = timedelta(seconds=30)  # 起動時刻の揺れの許容


def setup_logging(verbose: bool) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(STATE_DIR / "chime.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)


def next_target(now: datetime) -> datetime:
    """now - GRACE 以降で最初の正時。"""
    t = now - GRACE
    floor = t.replace(minute=0, second=0, microsecond=0)
    return floor if floor == t else floor + timedelta(hours=1)


def grid_floor(t: datetime) -> datetime:
    """t 以前で最後のスケジューラ起動時刻 (x4:00 / x9:00)。"""
    u = t - PHASE
    return u.replace(minute=u.minute - u.minute % 5, second=0, microsecond=0) + PHASE


def window_of(now: datetime) -> tuple[datetime, datetime]:
    """now に起動したときに受け持つ [start, end)。"""
    base = grid_floor(now + GRACE)
    return base + LEAD, base + LEAD + INTERVAL


def chime_events(events: list[ical.Event], cfg: Config) -> list[ical.Event]:
    """時報の判定に使う予定。カウントダウン専用カレンダーの予定は時報と独立させるので除く。"""
    return [e for e in events if e.calendar not in cfg.countdown.dedicated]


def collect(start: datetime, end: datetime, events: list[ical.Event], cfg: Config, registered_before: datetime | None) -> list[rules.Cue]:
    """時報 + カウントダウン (コマンド登録分は registered_before より前に登録されたもの)。"""
    cds = countdown.from_events(events, cfg) + [
        c for c in countdown.load() if registered_before is None or c.created < registered_before
    ]
    cd_cues = [q for cd in cds for q in countdown.cues_of(cd) if start <= q.at < end]
    return countdown.merge(rules.plan_window(start, end, chime_events(events, cfg), cfg), cd_cues)


def play_cues(cues: list[rules.Cue], cfg: Config) -> None:
    items = []
    for c in cues:
        if c.silent:
            continue
        wav, anchor = render(c, cfg)
        items.append(player.Scheduled(wav, c.at.timestamp() - anchor, c.label))
    items.sort(key=lambda i: i.at)
    player.play_scheduled(items, cfg.player)


def get_events(cfg: Config, around: datetime, hours_after: float, refresh: bool) -> list[ical.Event]:
    return ical.load_events(
        cfg.calendars,
        around - timedelta(days=1),  # 前日からの終日・長時間予定も拾う
        around + timedelta(hours=hours_after),
        cfg.timezone,
        max_age_minutes=0 if refresh else cfg.refresh_minutes,
    )


def render(cue: rules.Cue, cfg: Config) -> tuple[Path, float]:
    """(wav, アンカー秒) を返す。"""
    voice = tts.synthesize(cue.text, cfg.tts) if cue.text else None
    return sounds.compose(cue.sound, voice)


def describe(cue: rules.Cue) -> str:
    what = " + ".join(x for x in (cue.sound, cue.text and f"「{cue.text}」") if x) or "(鳴らさない)"
    return f"{cue.at:%m/%d %H:%M:%S} {cue.label:<6} {what}  [{cue.reason}]"


def cmd_chime(args: argparse.Namespace, cfg: Config) -> int:
    now = datetime.fromisoformat(args.at).replace(tzinfo=cfg.timezone) if args.at else datetime.now(cfg.timezone)
    start, end = window_of(now)
    events = get_events(cfg, start, 3, refresh=False)
    cues = [c for c in collect(start, end, events, cfg, start - LEAD) if args.all or not c.silent]
    log.debug("window %s-%s: %d cues", f"{start:%H:%M:%S}", f"{end:%H:%M:%S}", len(cues))
    for c in cues:
        log.info("%s", describe(c))
        if args.dry_run:
            print(describe(c))
    if args.dry_run:
        return 0
    play_cues(cues, cfg)
    tts.prune_cache(sounds.SLOT_CACHE)
    return 0


def cmd_countdown(args: argparse.Namespace, cfg: Config) -> int:
    now = datetime.now(cfg.timezone)
    if args.list:
        for c in countdown.load():
            if c.at > now:
                print(f"{c.id}  {c.at:%m/%d %H:%M}  {','.join(map(str, c.offsets))}分前")
        for c in countdown.from_events(get_events(cfg, now, 24, refresh=False), cfg):
            if c.at > now:
                print(f"{'(cal)':<6}  {c.at:%m/%d %H:%M}  {c.source}")
        return 0
    if args.cancel:
        gone = countdown.remove(None if args.cancel == ["all"] else set(args.cancel))
        for c in gone:
            print(f"取り消し: {c.id} {c.at:%m/%d %H:%M}")
        if gone:
            print("※ 既に鳴らす準備に入った直近 5 分以内の分は鳴ります")
        return 0 if gone else 1
    if not args.time:
        print("時刻を指定してください (例: 15:30 / 1530 / +45)", file=sys.stderr)
        return 2

    at = countdown.parse_time(args.time, now)
    offsets = tuple(int(x) for x in args.offsets.split(",")) if args.offsets else cfg.countdown.offsets
    cd = countdown.add(at, offsets, now)
    print(f"登録: {cd.id}  {at:%m/%d %H:%M} に向けて {','.join(map(str, offsets))}分前")

    # 既に起動済みのスケジューラはこの登録を知らないので、その受け持ち分は自分で鳴らす
    until = grid_floor(now) + LEAD + INTERVAL
    if any(now < q.at < until for q in countdown.cues_of(cd)):
        subprocess.Popen(
            [sys.executable, "-m", "hourly_chime.main", "-c", str(args.config), "countdown-play", cd.id, until.isoformat()],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=os.environ,
        )
    return 0


def cmd_countdown_play(args: argparse.Namespace, cfg: Config) -> int:
    """(内部用) 登録直後のカウントダウンのうち、until までの分を鳴らす。"""
    now = datetime.now(cfg.timezone)
    until = datetime.fromisoformat(args.until)
    cds = [c for c in countdown.load() if c.id == args.id]
    cues = [q for cd in cds for q in countdown.cues_of(cd) if now <= q.at < until]
    for c in cues:
        log.info("%s", describe(c))
    play_cues(cues, cfg)
    return 0


def cmd_demo(args: argparse.Namespace, cfg: Config) -> int:
    """次の正時の 4 つのタイミングを、間を詰めて今すぐ鳴らす (鳴らさない時間帯・休日は無視)。"""
    cfg = replace(cfg, quiet_hours=None, holiday=None, off=None)
    target = datetime.fromisoformat(args.at).replace(tzinfo=cfg.timezone) if args.at else next_target(
        datetime.now(cfg.timezone)
    )
    plans = rules.plan_hour(target, chime_events(get_events(cfg, target, 3, refresh=False), cfg), cfg)
    if args.sound_only:
        plans = [replace(p, sound=s.sound, text=None, reason="sound-only") for p, s in zip(plans, rules.HOUR_SLOTS)]
    rendered = []
    for p in plans:
        print(describe(p))
        rendered.append((render(p, cfg)[0], p.label))
    items = []
    t = time.time() + 2  # PowerShell の起動待ち
    for wav, name in rendered:
        items.append(player.Scheduled(wav, t, name))
        t += sounds.duration(wav) + 1.0
    player.play_scheduled(items, cfg.player)
    return 0


def cmd_events(args: argparse.Namespace, cfg: Config) -> int:
    now = datetime.now(cfg.timezone)
    events = get_events(cfg, now, args.hours, refresh=args.refresh)
    busy_only = {c.name for c in cfg.calendars if c.busy_only}
    for e in events:
        if e.end <= now or e.start > now + timedelta(hours=args.hours):
            continue
        when = f"{e.start:%m/%d} 終日" if e.all_day else f"{e.start:%m/%d %H:%M}-{e.end:%H:%M}"
        title = "(時間枠のみ)" if e.calendar in busy_only else e.title
        print(f"{when:<20} [{e.calendar}] {title}")
    return 0


def cmd_simulate(args: argparse.Namespace, cfg: Config) -> int:
    """この先 N 時間分の時報を一覧表示する。"""
    start = window_of(datetime.now(cfg.timezone))[0]
    end = start + timedelta(hours=args.hours)
    events = get_events(cfg, start, args.hours + 2, refresh=args.refresh)
    for c in collect(start, end, events, cfg, None):
        if args.all or not c.silent:
            print(describe(c))
    return 0


def cmd_say(args: argparse.Namespace, cfg: Config) -> int:
    player.play_now([tts.synthesize(args.text, cfg.tts)], cfg.player)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hourly-chime", description="カレンダー連動の音声時報")
    p.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chime", help="この先 5 分間の時報を鳴らす (スケジューラから 5 分ごとに呼ぶ)")
    c.add_argument("--dry-run", action="store_true", help="鳴らさずに計画だけ表示")
    c.add_argument("--all", action="store_true", help="鳴らさないものも表示")
    c.add_argument("--at", help="この時刻に起動したとして判定する (例: 2026-09-28T13:54)")
    c.set_defaults(func=cmd_chime)

    d = sub.add_parser("demo", help="次の正時の時報を間を詰めて今すぐ鳴らす")
    d.add_argument("--at", help="この正時として判定する")
    d.add_argument("--sound-only", action="store_true", help="予定中 (音のみ) の場合を鳴らす")
    d.set_defaults(func=cmd_demo)

    e = sub.add_parser("events", help="予定一覧を表示")
    e.add_argument("--hours", type=float, default=24)
    e.add_argument("--refresh", action="store_true", help="キャッシュを無視して取得")
    e.set_defaults(func=cmd_events)

    s = sub.add_parser("simulate", help="この先の時報を一覧表示")
    s.add_argument("--hours", type=int, default=24)
    s.add_argument("--all", action="store_true", help="鳴らさないものも表示")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_simulate)

    k = sub.add_parser("countdown", help="指定時刻に向けて 30/20/10/5/2/1 分前に読み上げる")
    k.add_argument("time", nargs="?", help="15:30 / 1530 (今日、過ぎていれば明日) / +45 (45 分後)")
    k.add_argument("--offsets", help="何分前に鳴らすか (例: 30,10,5)")
    k.add_argument("--list", action="store_true", help="登録済みの一覧")
    k.add_argument("--cancel", nargs="+", metavar="ID", help="取り消す (all で全て)")
    k.set_defaults(func=cmd_countdown)

    kp = sub.add_parser("countdown-play")  # 内部用
    kp.add_argument("id")
    kp.add_argument("until")
    kp.set_defaults(func=cmd_countdown_play)

    y = sub.add_parser("say", help="任意のテキストを喋る (音声確認用)")
    y.add_argument("text")
    y.set_defaults(func=cmd_say)

    args = p.parse_args(argv)
    setup_logging(args.verbose)
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print(f"設定ファイルがありません: {args.config}\nconfig.example.yaml をコピーしてください。", file=sys.stderr)
        return 2
    try:
        return args.func(args, cfg)
    except Exception:
        log.exception("失敗しました")
        return 1


if __name__ == "__main__":
    sys.exit(main())
