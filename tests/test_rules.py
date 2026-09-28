from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hourly_chime.config import parse_config
from hourly_chime.ical import Event, parse_events
from hourly_chime.main import next_target, window_of
from hourly_chime.rules import plan_event, plan_hour, plan_window

TZ = ZoneInfo("Asia/Tokyo")

CFG = parse_config(
    {
        "calendars": [
            {"name": "roo", "url": "x"},
            {"name": "tai", "url": "y", "busy_only": True},
            {"name": "holiday", "url": "z"},
        ],
        "quiet_hours": {"start": "20:00", "end": "08:00"},
        "holiday": {"calendar": "holiday", "description": "^祝日", "hours": [8, 12, 16, 20]},
        "off_days": {"weekdays": ["sat", "sun"], "calendar": "roo", "title": "有休|休暇|休み"},
    }
)


def at(h: int, m: int = 0, s: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 9, day, h, m, s, tzinfo=TZ)


def ev(cal: str, title: str, s: datetime, e: datetime, all_day: bool = False) -> Event:
    return Event(cal, title, s, e, all_day)


def summary(target: datetime, events: list[Event]) -> list[tuple[str | None, str | None]]:
    return [(p.sound, p.text) for p in plan_hour(target, events, CFG)]


def test_no_events_in_hour_only_on_the_hour():
    assert summary(at(14), []) == [(None, None), (None, None), (None, None), ("pipipipoon", "14時です。")]


def test_event_starting_on_the_hour_full_sequence():
    events = [ev("roo", "会議", at(14), at(15)), ev("roo", "歯医者", at(14, 40), at(15))]
    assert summary(at(14), events) == [
        (None, "14時5分前です。14時から、会議です。14時40分から、歯医者です。"),
        (None, "2分前です。"),
        (None, "15秒前です。"),
        ("pipipipoon", None),  # 14:00 は会議中
    ]
    plans = plan_hour(at(14), events, CFG)
    assert [p.at for p in plans] == [at(13, 55), at(13, 58), at(13, 59, 45), at(14)]
    # N:00 ちょうどでない予定・翌時台の予定では正時のみ
    assert summary(at(14), [ev("roo", "x", at(14, 40), at(15))])[0] == (None, None)
    assert summary(at(14), [ev("roo", "x", at(15), at(16))])[0] == (None, None)


def test_quiet_hours_by_target_hour():
    assert all(p.silent for p in plan_hour(at(20), [], CFG))
    assert all(p.silent for p in plan_hour(at(3), [], CFG))
    assert not plan_hour(at(8), [], CFG)[3].silent  # 8 時の時報は鳴る


def test_quiet_hours_from_unquoted_yaml():
    # YAML で 20:00 をクォートし忘れると int (1200) になる
    cfg = parse_config({"quiet_hours": {"start": 1200, "end": 480}})
    assert all(p.silent for p in plan_hour(at(20), [], cfg))


def test_event_spanning_hour_sound_only():
    # 長い予定の中では正時の音だけ
    events = [ev("roo", "作業", at(13), at(15))]
    assert summary(at(14), events) == [(None, None), (None, None), (None, None), ("pipipipoon", None)]
    # 14:00 に始まる予定もあれば、4 回とも音のみ
    events.append(ev("roo", "打合せ", at(14), at(15)))
    assert summary(at(14), events) == [
        ("popopopopo", None),
        ("popo", None),
        ("poon", None),
        ("pipipipoon", None),
    ]


def test_busy_only_calendar_counts_as_busy():
    events = [ev("tai", "予定あり", at(13, 30), at(14, 30))]
    assert all(p.text is None for p in plan_hour(at(14), events, CFG))


def test_event_starting_on_the_hour():
    # 13:55 には予定なし → 読み上げ + 予定案内、14:00 は予定開始 → 音のみ
    events = [ev("roo", "会議", at(14), at(15))]
    assert summary(at(14), events) == [
        (None, "14時5分前です。14時から、会議です。"),
        (None, "2分前です。"),
        (None, "15秒前です。"),
        ("pipipipoon", None),
    ]


def test_event_ending_on_the_hour_is_not_busy():
    events = [ev("roo", "会議", at(13), at(14)), ev("roo", "リマインド", at(14), at(14))]  # 長さ 0 の予定
    s = summary(at(14), events)
    assert s[0] == ("popopopopo", None)  # 13:55 は予定中
    assert s[3] == ("pipipipoon", "14時です。")  # 14:00 は終わっている


def test_announce_this_hour_at_5min_and_next_hour_on_the_hour():
    events = [
        ev("roo", "リマインド", at(14), at(14)),
        ev("roo", "歯医者", at(14, 30), at(15)),
        ev("roo", "ジム", at(15, 10), at(16)),
        ev("roo", "遠い予定", at(16), at(17)),
        ev("tai", "予定あり", at(15, 30), at(16)),  # 時間枠のみ → 「予定があります」
        ev("roo", "連休", at(0), at(0, day=29), all_day=True),  # 終日 → 無関係
    ]
    s = summary(at(14), events)
    assert s[0] == (None, "14時5分前です。14時から、リマインドです。14時30分から、歯医者です。")
    assert s[3] == ("pipipipoon", "14時です。15時10分から、ジムです。15時30分から、予定があります。")


def test_untitled_skipped_when_titled_event_at_same_time():
    events = [
        ev("tai", "予定あり", at(14), at(14)),
        ev("roo", "会議", at(14, 30), at(15)),
        ev("tai", "予定あり", at(14, 30), at(15)),
        ev("roo", "", at(14, 45), at(15)),
    ]
    assert summary(at(14), events)[0] == (
        None,
        "14時5分前です。14時から、予定があります。14時30分から、会議です。14時45分から、予定があります。",
    )


HOLIDAY = Event("holiday", "文化の日", at(0, day=3), at(0, day=4), True, "祝日")


def hat(h: int, m: int = 0, day: int = 3) -> datetime:
    return at(h, m, day=day)


def test_holiday_only_on_listed_hours_and_on_the_hour():
    assert all(p.silent for p in plan_hour(hat(9), [HOLIDAY], CFG))
    assert all(p.silent for p in plan_hour(hat(13), [HOLIDAY], CFG))
    s = summary(hat(12), [HOLIDAY])
    assert s == [(None, None), (None, None), (None, None), ("pipipipoon", "12時です。")]


def test_holiday_overrides_quiet_hours():
    assert summary(hat(20), [HOLIDAY])[3] == ("pipipipoon", "20時です。")


def test_holiday_announces_until_next_chime():
    events = [
        HOLIDAY,
        ev("roo", "ランチ", hat(12, 30), hat(13, 30)),
        ev("roo", "買い物", hat(15, 50), hat(17)),
        ev("roo", "夕飯", hat(16), hat(17)),  # 次の時報 (16時) 以降 → 読まない
    ]
    assert summary(hat(12), events)[3] == ("pipipipoon", "12時です。12時30分から、ランチです。15時50分から、買い物です。")
    # 20時の次は翌朝 8 時まで
    late = [HOLIDAY, ev("roo", "早朝", hat(7, day=4), hat(8, day=4)), ev("roo", "朝会", hat(8, day=4), hat(9, day=4))]
    assert summary(hat(20), late)[3] == ("pipipipoon", "20時です。7時から、早朝です。")


def test_holiday_busy_is_sound_only():
    events = [HOLIDAY, ev("tai", "予定あり", hat(11), hat(13))]
    assert summary(hat(12), events)[3] == ("pipipipoon", None)


def test_holiday_description_filter():
    # 祭日 (クリスマス等) は休日扱いしない
    xmas = ev("holiday", "クリスマス", at(0, day=25), at(0, day=26), all_day=True)
    xmas = Event(xmas.calendar, xmas.title, xmas.start, xmas.end, True, "祭日\n祭日を非表示にするには…")
    assert plan_hour(at(14, day=25), [xmas], CFG)[3].text == "14時です。"


def test_next_target():
    assert next_target(at(13, 54, 30)) == at(14)
    assert next_target(at(13, 58)) == at(14)
    assert next_target(at(14, 0, 5)) == at(14)
    assert next_target(at(14, 0, 30)) == at(14)
    assert next_target(at(14, 1)) == at(15)
    assert next_target(at(14, 45)) == at(15)


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:1
DTSTART:20260901T050000Z
DTEND:20260901T060000Z
RRULE:FREQ=WEEKLY;BYDAY=MO
SUMMARY:週次MTG
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20260928
DTEND;VALUE=DATE:20260929
SUMMARY:有給
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART:20260928T010000Z
DTEND:20260928T020000Z
STATUS:CANCELLED
SUMMARY:中止
END:VEVENT
END:VCALENDAR
""".encode()


def test_parse_ics_recurring_allday_cancelled():
    events = parse_events("roo", ICS, at(0), at(0, day=29), TZ)
    titles = {e.title: e for e in events}
    assert set(titles) == {"週次MTG", "有給"}
    mtg = titles["週次MTG"]  # 2026-09-28 は月曜、05:00Z = 14:00 JST
    assert (mtg.start, mtg.end, mtg.all_day) == (at(14), at(15), False)
    off = titles["有給"]
    assert off.all_day and off.start == at(0) and off.end == at(0, day=29)
    assert plan_hour(at(14), events, CFG)[3].sound == "pipipipoon"  # 14:00 は週次MTG中
    assert plan_hour(at(14), events, CFG)[3].text is None


# --- 休み (曜日・タイトル) ---


def test_weekend_is_off_even_on_holiday():
    sat = at(12, day=26)  # 2026-09-26 は土曜
    assert all(p.silent for p in plan_hour(sat, [], CFG))
    hol_sat = Event("holiday", "祝日", at(0, day=26), at(0, day=27), True, "祝日")
    assert all(p.silent for p in plan_hour(sat, [hol_sat], CFG))


def test_off_title_on_roo_only():
    vac = ev("roo", "会社年末有休取得日", at(0), at(0, day=29), all_day=True)
    assert all(p.silent for p in plan_hour(at(14), [vac], CFG))
    other = ev("tai", "休み", at(0), at(0, day=29), all_day=True)  # roo 以外は見ない
    assert not plan_hour(at(14), [other], CFG)[3].silent


def test_off_title_only_all_day():
    # 時間指定の「昼休み」などは休み扱いしない (普通の予定として音のみ)
    lunch = ev("roo", "昼休み", at(11, 30), at(12, 30))
    assert summary(at(12), [lunch])[3] == ("pipipipoon", None)


# --- 正時始まりでない予定 ---


def test_event_cues():
    gym = ev("roo", "ジム", at(19, 25), at(19, 45))
    cues = plan_event(gym.start, [gym], [gym], CFG)
    assert [(c.at, c.sound, c.text) for c in cues] == [
        (at(19, 23), None, "2分前です。"),
        (at(19, 24, 45), None, "15秒前です。"),
        (at(19, 25), "pipipipoon", "19時25分です。ジムです。"),
    ]


def test_event_cues_skipped_when_other_event_ongoing():
    a = ev("roo", "A", at(14), at(14, 30))
    b = ev("roo", "B", at(14, 30), at(15))  # 14:28, 14:29:45 は A の最中、14:30 は A 終了
    cues = plan_event(b.start, [b], [a, b], CFG)
    assert [c.silent for c in cues] == [True, True, False]
    assert cues[2].text == "14時30分です。Bです。"


def test_event_cues_busy_only_no_title():
    t = ev("tai", "予定あり", at(10, 30), at(11))
    assert plan_event(t.start, [t], [t], CFG)[2].text == "10時30分です。予定の時間です。"


def test_event_cues_suppressed_in_quiet_holiday_and_off():
    late = ev("roo", "夜", at(22, 30), at(23))
    assert all(c.silent for c in plan_event(late.start, [late], [late], CFG))
    sat = ev("roo", "土曜", at(10, 30, day=26), at(11, day=26))
    assert all(c.silent for c in plan_event(sat.start, [sat], [sat], CFG))
    hol = [HOLIDAY, ev("roo", "祝日の予定", hat(10, 30), hat(11))]
    assert all(c.silent for c in plan_event(hol[1].start, [hol[1]], hol, CFG))


# --- 5 分ごとの受け持ち ---


def test_window_of():
    assert window_of(at(13, 54)) == (at(13, 54, 30), at(13, 59, 30))
    assert window_of(at(13, 54, 3)) == (at(13, 54, 30), at(13, 59, 30))
    assert window_of(at(13, 53, 40)) == (at(13, 54, 30), at(13, 59, 30))  # 少し早い起動
    assert window_of(at(13, 59)) == (at(13, 59, 30), at(14, 4, 30))


def test_plan_window_partitions_all_cues():
    events = [
        ev("roo", "朝会", at(14), at(14)),
        ev("roo", "ジム", at(14, 1), at(14, 30)),
        ev("roo", "会議", at(14, 57), at(15, 30)),
    ]
    start = at(13, 54, 30)
    got = []
    for i in range(14):  # 13:54:30 から 70 分
        s, e = window_of(start + timedelta(minutes=5 * i) - timedelta(seconds=30))
        got += [(c.at, c.label) for c in plan_window(s, e, events, CFG) if not c.silent]
    assert got == sorted(got) and len(got) == len(set(got))
    assert (at(13, 59, 45), "15秒前") in got and (at(14), "正時") in got
    assert (at(13, 59), "予定2分前") in got and (at(14, 1), "予定開始") in got
    assert (at(14, 55), "予定2分前") in got and (at(14, 57), "予定開始") in got


def test_example_config_parses_with_all_sections():
    # YAML 1.1 の罠 (off/on/yes/no が bool になる等) を実ファイルで検出する
    from pathlib import Path

    from hourly_chime.config import load_config

    root = Path(__file__).parent.parent
    cfg = load_config(root / "config.example.yaml", root / "secrets.example.yaml")
    hosts = [c.url.split("/")[2] for c in cfg.calendars]
    assert hosts == ["calendar.google.com", "script.google.com", "calendar.google.com", "calendar.google.com"]
    assert cfg.countdown.dedicated == {"countdown"}
    assert cfg.off is not None and cfg.off.weekdays == {5, 6}
    assert cfg.holiday is not None and cfg.quiet_hours is not None


def test_secret_url_overrides_and_missing_url_errors():
    raw = {"calendars": [{"name": "a", "url": "http://inline"}, {"name": "b"}]}
    cfg = parse_config(raw, {"calendars": {"a": "http://secret", "b": "http://b"}})
    assert [c.url for c in cfg.calendars] == ["http://secret", "http://b"]
    # url が無いカレンダーは警告してスキップ (時報は止めない)
    assert [c.name for c in parse_config(raw).calendars] == ["a"]
    assert "http" not in repr(cfg.calendars)


# --- カウントダウン ---

from hourly_chime import countdown as cdm  # noqa: E402
from hourly_chime.main import grid_floor  # noqa: E402

CD_CFG = parse_config(
    {
        "calendars": [{"name": "roo", "url": "x"}],
        "quiet_hours": {"start": "21:00", "end": "08:00"},
        "countdown": {"title": "^⏰"},
    }
)


def test_countdown_cues():
    cd = cdm.Countdown(at(15, 30), (30, 20, 10, 5, 2, 1))
    got = [(c.at, c.sound, c.text) for c in cdm.cues_of(cd)]
    assert got[0] == (at(15), None, "15時30分まで、あと30分です。")
    assert got[-2] == (at(15, 29), None, "15時30分まで、あと1分です。")
    assert got[-1] == (at(15, 30), "pipipipoon", "15時30分です。")
    assert len(got) == 7


def test_countdown_overrides_colliding_hour_cue():
    cd = cdm.cues_of(cdm.Countdown(at(15, 30), (30,)))  # 30分前 = 15:00 ちょうど
    base = plan_window(at(14, 54, 30), at(15, 4, 30), [], CD_CFG)
    merged = cdm.merge(base, cd)
    at15 = [c for c in merged if c.at == at(15)]
    assert [c.label for c in at15] == ["CD30分前"]
    assert any(c.label == "15秒前" for c in merged)  # 14:59:45 は 15 秒離れているので残る


def test_countdown_from_calendar_event_and_not_event_cues():
    e = ev("roo", "⏰出発", at(15, 30), at(15, 30))
    cds = cdm.from_events([e], CD_CFG)
    assert [c.at for c in cds] == [at(15, 30)]
    # 通常の予定 Cue (2分前等) は出さない
    assert not [c for c in plan_window(at(15, 20), at(15, 35), [e], CD_CFG) if not c.silent]
    # 案内では印を外して読む (14時の正時で 15時台の予定として)
    assert summary_cfg(at(14), [e], CD_CFG)[3][1] == "14時です。15時30分から、出発です。"


def test_countdown_ignores_quiet_hours():
    cd = cdm.cues_of(cdm.Countdown(at(23, 0), (5,)))
    assert [c.text for c in cdm.merge(plan_window(at(22, 50), at(23, 5), [], CD_CFG), cd) if not c.silent] == [
        "23時まで、あと5分です。",
        "23時です。",
    ]


def summary_cfg(target, events, cfg):
    return [(p.sound, p.text) for p in plan_hour(target, events, cfg)]


def test_parse_time():
    now = at(14, 10, 30)
    assert cdm.parse_time("15:30", now) == at(15, 30)
    assert cdm.parse_time("1530", now) == at(15, 30)
    assert cdm.parse_time("930", now) == at(9, 30, day=29)  # 過ぎていれば明日
    assert cdm.parse_time("+45", now) == at(14, 55)
    assert cdm.parse_time("2026-09-30T08:00", now) == at(8, day=30)


def test_store_roundtrip(tmp_path):
    p = tmp_path / "cd.json"
    a = cdm.add(at(15, 30), (10, 5), at(14), p)
    cdm.add(at(16), (5,), at(14), p)
    assert [c.at for c in cdm.load(p)] == [at(15, 30), at(16)]
    assert [c.id for c in cdm.remove({a.id}, p)] == [a.id]
    assert [c.at for c in cdm.load(p)] == [at(16)]
    cdm.add(at(17), (5,), at(16, 30), p)  # 過去のものは掃除される
    assert [c.at for c in cdm.load(p)] == [at(17)]


def test_grid_floor():
    assert grid_floor(at(14, 4)) == at(14, 4)
    assert grid_floor(at(14, 8, 59)) == at(14, 4)
    assert grid_floor(at(14, 9, 0)) == at(14, 9)
    assert grid_floor(at(14, 3)) == at(13, 59)


def test_dedicated_countdown_calendar_is_independent_of_chimes():
    from hourly_chime.main import collect

    cfg = parse_config(
        {
            "calendars": [{"name": "roo", "url": "x"}, {"name": "cd", "url": "y", "countdown": True}],
            "countdown": {"title": "^⏰", "offsets": [5]},
        }
    )
    e = ev("cd", "出発", at(15, 30), at(16, 30))
    assert [c.at for c in cdm.from_events([e], cfg)] == [at(15, 30)]
    # 時報は専用カレンダーの予定を一切見ない (予定中にも案内にも使わない)
    cues = [(c.at, c.label, c.sound, c.text) for c in collect(at(14, 54, 30), at(16, 5), [e], cfg, None) if not c.silent]
    # 専用カレンダーの 15:30 の予定は「15時台の予定」にならない → 15時は正時のみ
    assert not [c for c in cues if c[1] == "5分前"]
    assert (at(15), "正時", "pipipipoon", "15時です。") in cues
    assert (at(16), "正時", "pipipipoon", "16時です。") in cues
    assert (at(15, 25), "CD5分前", None, "15時30分まで、あと5分です。") in cues
    assert not [c for c in cues if c[1].startswith("予定")]
