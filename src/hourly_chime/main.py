"""CLI 入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import ical, player, rules, tts
from .config import DEFAULT_CONFIG_PATH, STATE_DIR, Config, load_config

log = logging.getLogger("hourly_chime")

# 起動がこれ以上遅れたら鳴らさない (スリープ復帰直後などに古い時報を鳴らさないため)
MAX_DELAY = timedelta(minutes=5)


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


def nearest_hour(t: datetime) -> datetime:
    return (t + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)


def get_events(cfg: Config, around: datetime, hours_after: float, refresh: bool) -> list[ical.Event]:
    return ical.load_events(
        cfg.calendars,
        around - timedelta(days=1),  # 前日からの終日・長時間予定も拾う
        around + timedelta(hours=hours_after),
        cfg.timezone,
        max_age_minutes=0 if refresh else cfg.refresh_minutes,
    )


def speak(text: str, cfg: Config) -> None:
    paths = ([cfg.chime_wav] if cfg.chime_wav else []) + [tts.synthesize(text, cfg.tts)]
    player.play(paths, cfg.player)


def cmd_chime(args: argparse.Namespace, cfg: Config) -> int:
    now = datetime.fromisoformat(args.at).replace(tzinfo=cfg.timezone) if args.at else datetime.now(cfg.timezone)
    target = nearest_hour(now)
    if abs(now - target) > MAX_DELAY and not args.force:
        log.info("正時から %s ずれているのでスキップ", now - target)
        return 0

    horizon = max(cfg.announce_next.within_minutes / 60, 0) + 1
    events = get_events(cfg, target, horizon, refresh=False)
    d = rules.decide(target, events, cfg)
    log.info("%s -> %s (%s)", target.strftime("%Y-%m-%d %H:%M"), d.text or "(mute)", d.reason)
    if args.dry_run:
        print(f"{target:%Y-%m-%d %H:%M}  {d.text or '(鳴らさない)'}  [{d.reason}]")
        return 0
    if d.text:
        speak(d.text, cfg)
    tts.prune_cache()
    return 0


def cmd_events(args: argparse.Namespace, cfg: Config) -> int:
    now = datetime.now(cfg.timezone)
    events = get_events(cfg, now, args.hours, refresh=args.refresh)
    for e in events:
        if e.end <= now or e.start > now + timedelta(hours=args.hours):
            continue
        when = f"{e.start:%m/%d} 終日" if e.all_day else f"{e.start:%m/%d %H:%M}-{e.end:%H:%M}"
        print(f"{when:<20} [{e.calendar}] {e.title}")
    return 0


def cmd_simulate(args: argparse.Namespace, cfg: Config) -> int:
    """これから N 時間分の時報を dry-run で一覧表示する。"""
    start = nearest_hour(datetime.now(cfg.timezone))
    horizon = max(cfg.announce_next.within_minutes / 60, 0) + 1
    events = get_events(cfg, start, args.hours + horizon, refresh=args.refresh)
    for i in range(args.hours):
        t = start + timedelta(hours=i)
        d = rules.decide(t, events, cfg)
        print(f"{t:%m/%d %H:%M}  {d.text or '(鳴らさない)'}  [{d.reason}]")
    return 0


def cmd_say(args: argparse.Namespace, cfg: Config) -> int:
    speak(args.text, cfg)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hourly-chime", description="カレンダー連動の音声時報")
    p.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chime", help="時報を鳴らす (スケジューラから毎時呼ぶ)")
    c.add_argument("--dry-run", action="store_true", help="喋らずに判定結果だけ表示")
    c.add_argument("--force", action="store_true", help="正時から離れていても鳴らす")
    c.add_argument("--at", help="この時刻として判定する (例: 2026-09-28T14:00)")
    c.set_defaults(func=cmd_chime)

    e = sub.add_parser("events", help="予定一覧を表示")
    e.add_argument("--hours", type=float, default=24)
    e.add_argument("--refresh", action="store_true", help="キャッシュを無視して取得")
    e.set_defaults(func=cmd_events)

    s = sub.add_parser("simulate", help="この先の時報を一覧表示")
    s.add_argument("--hours", type=int, default=24)
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_simulate)

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
