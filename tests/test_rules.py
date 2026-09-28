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


def test_no_events():
    assert summary(at(14), []) == [
        (None, "14時5分前です。"),
        (None, "2分前です。"),
        (None, "15秒前です。"),
        ("pipipipoon", "14時です。"),
    ]
    plans = plan_hour(at(14), [], CFG)
    assert [p.at for p in plans] == [at(13, 55), at(13, 58), at(13, 59, 45), at(14)]


def test_quiet_hours_by_target_hour():
    assert all(p.silent for p in plan_hour(at(20), [], CFG))
    assert all(p.silent for p in plan_hour(at(3), [], CFG))
    assert not any(p.silent for p in plan_hour(at(8), [], CFG))  # 7:55 からの 8 時の時報は鳴る


def test_quiet_hours_from_unquoted_yaml():
    # YAML で 20:00 をクォートし忘れると int (1200) になる
    cfg = parse_config({"quiet_hours": {"start": 1200, "end": 480}})
    assert all(p.silent for p in plan_hour(at(20), [], cfg))


def test_event_spanning_hour_makes_all_sound_only():
    events = [ev("roo", "作業", at(13), at(15))]
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
    events = [ev("roo", "会議", at(13), at(14))]
    s = summary(at(14), events)
    assert s[0] == ("popopopopo", None)  # 13:55 は予定中
    assert s[3] == ("pipipipoon", "14時です。")  # 14:00 は終わっている


def test_announce_this_hour_at_5min_and_next_hour_on_the_hour():
    events = [
        ev("roo", "歯医者", at(14, 30), at(15)),
        ev("roo", "ジム", at(15, 10), at(16)),
        ev("roo", "遠い予定", at(16), at(17)),
        ev("tai", "予定あり", at(15, 30), at(16)),  # 時間枠のみ → 読まない
        ev("roo", "連休", at(0), at(0, day=29), all_day=True),  # 終日 → 無関係
    ]
    s = summary(at(14), events)
    assert s[0] == (None, "14時5分前です。14時30分から、歯医者です。")
    assert s[3] == ("pipipipoon", "14時です。15時10分から、ジムです。")


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
    assert not any(p.silent for p in plan_hour(at(14, day=25), [xmas], CFG))


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
    assert plan_hour(at(15), events, CFG)[0].sound == "popopopopo"  # 14:55 は週次MTG中


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
    assert not any(p.silent for p in plan_hour(at(14), [other], CFG))


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
    assert plan_event(t.start, [t], [t], CFG)[2].text == "10時30分です。"


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
    events = [ev("roo", "ジム", at(14, 1), at(14, 30)), ev("roo", "会議", at(14, 57), at(15, 30))]
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

    cfg = load_config(Path(__file__).parent.parent / "config.example.yaml")
    assert cfg.off is not None and cfg.off.weekdays == {5, 6}
    assert cfg.holiday is not None and cfg.quiet_hours is not None
