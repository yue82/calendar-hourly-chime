"""CLI 入口。"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import ical, player, rules, sounds, tts
from .config import DEFAULT_CONFIG_PATH, STATE_DIR, Config, load_config

log = logging.getLogger("hourly_chime")

# スケジューラは毎時 54:30 に起動する。正時を少し過ぎての起動 (スリープ復帰など) ならその正時を対象にする
GRACE = timedelta(seconds=30)


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


def get_events(cfg: Config, around: datetime, hours_after: float, refresh: bool) -> list[ical.Event]:
    return ical.load_events(
        cfg.calendars,
        around - timedelta(days=1),  # 前日からの終日・長時間予定も拾う
        around + timedelta(hours=hours_after),
        cfg.timezone,
        max_age_minutes=0 if refresh else cfg.refresh_minutes,
    )


def render(plan: rules.SlotPlan, cfg: Config) -> tuple[Path, float]:
    """(wav, アンカー秒) を返す。"""
    voice = tts.synthesize(plan.text, cfg.tts) if plan.text else None
    return sounds.compose(plan.sound, voice)


def describe(plan: rules.SlotPlan) -> str:
    what = " + ".join(x for x in (plan.sound, plan.text and f"「{plan.text}」") if x) or "(鳴らさない)"
    return f"{plan.at:%m/%d %H:%M:%S} {plan.slot.name:<5} {what}  [{plan.reason}]"


def cmd_chime(args: argparse.Namespace, cfg: Config) -> int:
    if args.at:
        target = datetime.fromisoformat(args.at).replace(tzinfo=cfg.timezone)
    else:
        target = next_target(datetime.now(cfg.timezone))
    events = get_events(cfg, target, 3, refresh=False)
    plans = rules.plan_hour(target, events, cfg)
    for p in plans:
        log.info("%s", describe(p))
        if args.dry_run:
            print(describe(p))
    if args.dry_run:
        return 0

    items = []
    for p in plans:
        if p.silent:
            continue
        wav, anchor = render(p, cfg)
        items.append(player.Scheduled(wav, p.at.timestamp() - anchor, p.slot.name))
    player.play_scheduled(items, cfg.player)
    tts.prune_cache(sounds.SLOT_CACHE)
    return 0


def cmd_demo(args: argparse.Namespace, cfg: Config) -> int:
    """次の正時の 4 つのタイミングを、間を詰めて今すぐ鳴らす (鳴らさない時間帯は無視)。"""
    cfg = replace(
        cfg, quiet_hours=None, calendars=tuple(replace(c, mute_all_day=False) for c in cfg.calendars)
    )
    target = datetime.fromisoformat(args.at).replace(tzinfo=cfg.timezone) if args.at else next_target(
        datetime.now(cfg.timezone)
    )
    plans = rules.plan_hour(target, get_events(cfg, target, 3, refresh=False), cfg)
    if args.sound_only:
        plans = [replace(p, sound=p.slot.sound, text=None, reason="sound-only") for p in plans]
    rendered = []
    for p in plans:
        print(describe(p))
        rendered.append((render(p, cfg)[0], p.slot.name))
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
    start = next_target(datetime.now(cfg.timezone))
    events = get_events(cfg, start, args.hours + 2, refresh=args.refresh)
    for i in range(args.hours):
        for p in rules.plan_hour(start + timedelta(hours=i), events, cfg):
            if args.all or not p.silent:
                print(describe(p))
    return 0


def cmd_say(args: argparse.Namespace, cfg: Config) -> int:
    player.play_now([tts.synthesize(args.text, cfg.tts)], cfg.player)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hourly-chime", description="カレンダー連動の音声時報")
    p.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chime", help="次の正時の時報を鳴らす (スケジューラから毎時 54:30 に呼ぶ)")
    c.add_argument("--dry-run", action="store_true", help="鳴らさずに計画だけ表示")
    c.add_argument("--at", help="この正時として判定する (例: 2026-09-28T14:00)")
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
