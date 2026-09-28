from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hourly_chime import countdown as cdm
from hourly_chime.config import load_config, parse_config
from hourly_chime.ical import Event, parse_events
from hourly_chime.main import collect, grid_floor, next_target, window_of
from hourly_chime.rules import plan_event, plan_hour, plan_window

TZ = ZoneInfo("Asia/Tokyo")

CFG = parse_config(
    {
        "calendars": [
            {"name": "roo", "url": "x"},
            {"name": "tai", "url": "y", "busy_only": True},
            {"name": "holiday", "url": "z"},
            {"name": "cd", "url": "w", "countdown": True},
        ],
        "quiet_hours": {"start": "21:00", "end": "08:00"},
        "holiday": {
            "hours": [8, 12, 16, 20],
            "weekdays": ["sat", "sun"],
            "all_day": [
                {"calendar": "holiday", "description": "^祝日"},
                {"calendar": "roo", "title": "有休|休暇|休み"},
            ],
        },
        "countdown": {"offsets": [30, 20, 10, 5, 2, 1]},
    }
)


def at(h: int, m: int = 0, s: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 9, day, h, m, s, tzinfo=TZ)  # 2026-09-28 は月曜


def ev(cal: str, title: str, s: datetime, e: datetime, all_day: bool = False, desc: str = "") -> Event:
    return Event(cal, title, s, e, all_day, desc)


def hour(target: datetime, events: list[Event]) -> tuple[str | None, str | None]:
    c = plan_hour(target, events, CFG)
    return c.sound, c.text


def audible(start: datetime, end: datetime, events: list[Event], cfg=CFG) -> list[tuple]:
    return [(c.at, c.label, c.sound, c.text) for c in collect(start, end, events, cfg, None) if not c.silent]


# --- 平日の時報 ---


def test_weekday_hour_chime():
    assert hour(at(14), []) == ("pipipipoon", "14時です。")


def test_weekday_announces_next_hour_events():
    events = [
        ev("roo", "今の時間台", at(14, 30), at(15)),  # 14時台 → 読まない
        ev("roo", "ジム", at(15, 10), at(16)),
        ev("tai", "予定あり", at(15, 30), at(16)),  # 時間枠のみ → 「予定があります」
        ev("roo", "遠い予定", at(16), at(17)),
        ev("roo", "連休", at(0), at(0, day=29), all_day=True),  # 終日 → 無関係
    ]
    assert hour(at(14), events) == ("pipipipoon", "14時です。15時10分から、ジムです。15時30分から、予定があります。")


def test_hour_chime_sound_only_when_busy():
    assert hour(at(14), [ev("roo", "作業", at(13), at(15))]) == ("pipipipoon", None)
    assert hour(at(14), [ev("roo", "会議", at(14), at(15))]) == ("pipipipoon", None)  # ちょうど開始も予定中
    assert hour(at(14), [ev("roo", "会議", at(13), at(14))]) == ("pipipipoon", "14時です。")  # 終わっている


def test_quiet_hours():
    assert plan_hour(at(21), [], CFG).silent
    assert plan_hour(at(3), [], CFG).silent
    assert hour(at(20), []) == ("pipipipoon", "20時です。")
    assert hour(at(8), []) == ("pipipipoon", "8時です。")


def test_quiet_hours_from_unquoted_yaml():
    # YAML で 21:00 をクォートし忘れると int (1260) になる
    cfg = parse_config({"quiet_hours": {"start": 1260, "end": 480}})
    assert plan_hour(at(21), [], cfg).silent


def test_untitled_skipped_when_titled_event_at_same_time():
    events = [
        ev("roo", "会議", at(15, 30), at(16)),
        ev("tai", "予定あり", at(15, 30), at(16)),
        ev("roo", "", at(15, 45), at(16)),
    ]
    assert hour(at(14), events)[1] == "14時です。15時30分から、会議です。15時45分から、予定があります。"


# --- 休日 (曜日・祝日・休み予定を同じ扱い) ---

HOLIDAY = ev("holiday", "文化の日", at(0, day=3), at(0, day=4), True, "祝日")  # 9/3 は木曜


def hat(h: int, m: int = 0, day: int = 3) -> datetime:
    return at(h, m, day=day)


def test_holiday_kinds_are_identical():
    sat = []  # 9/26 は土曜
    vacation = [ev("roo", "会社年末有休取得日", at(0, day=29), at(0, day=30), all_day=True)]  # 9/29 火曜
    for day, events in [(3, [HOLIDAY]), (26, sat), (29, vacation)]:
        got = [(h, hour(at(h, day=day), events)) for h in range(24)]
        assert [h for h, (snd, _) in got if snd] == [8, 12, 16, 20], day
        assert dict(got)[20] == ("pipipipoon", "20時です。")  # quiet_hours より優先


def test_holiday_announces_until_next_chime():
    events = [
        HOLIDAY,
        ev("roo", "ランチ", hat(12, 30), hat(13, 30)),
        ev("roo", "買い物", hat(15, 50), hat(17)),
        ev("roo", "夕飯", hat(16), hat(17)),  # 次の時報 (16時) 以降 → 読まない
    ]
    assert hour(hat(12), events) == ("pipipipoon", "12時です。12時30分から、ランチです。15時50分から、買い物です。")
    late = [HOLIDAY, ev("roo", "早朝", hat(7, day=4), hat(8, day=4)), ev("roo", "朝会", hat(8, day=4), hat(9, day=4))]
    assert hour(hat(20), late) == ("pipipipoon", "20時です。7時から、早朝です。")  # 20時の次は翌朝 8 時


def test_holiday_busy_is_sound_only():
    assert hour(hat(12), [HOLIDAY, ev("tai", "予定あり", hat(11), hat(13))]) == ("pipipipoon", None)


def test_holiday_matchers():
    # 祭日 (クリスマス等) や roo 以外の「休み」、時間指定の「昼休み」は休日にしない
    xmas = ev("holiday", "クリスマス", at(0, day=25), at(0, day=26), True, "祭日\n…")
    assert hour(at(14, day=25), [xmas]) == ("pipipipoon", "14時です。")
    other = ev("tai", "休み", at(0), at(0, day=29), all_day=True)
    lunch = ev("roo", "昼休み", at(11, 30), at(12, 30))
    assert hour(at(14), [other, lunch]) == ("pipipipoon", "14時です。")


# --- 予定通知 ---


def test_event_notifications():
    gym = ev("roo", "ジム", at(19, 25), at(19, 45))
    cues = plan_event(gym.start, [gym], [gym], CFG)
    assert [(c.at, c.sound, c.text) for c in cues] == [
        (at(19, 20), None, "5分前です。19時25分から、ジムです。"),
        (at(19, 23), None, "2分前です。"),
        (at(19, 24, 45), None, "15秒前です。"),
        (at(19, 25), "pipoon", "19時25分です。ジムです。"),
    ]


def test_event_notifications_untitled():
    t = ev("tai", "予定あり", at(10, 30), at(11))
    cues = plan_event(t.start, [t], [t], CFG)
    assert cues[0].text == "5分前です。10時30分から、予定があります。"
    assert cues[3].text == "10時30分です。予定の時間です。"


def test_event_notifications_sound_only_during_other_event():
    a = ev("roo", "A", at(14), at(14, 30))
    b = ev("roo", "B", at(14, 30), at(15))  # 14:25〜14:29:45 は A の最中、14:30 は A 終了
    cues = plan_event(b.start, [b], [a, b], CFG)
    assert [(c.sound, c.text) for c in cues] == [
        ("popopopopo", None),
        (None, None),  # 2分前・15秒前は予定中なら鳴らさない
        (None, None),
        ("pipoon", "14時30分です。Bです。"),
    ]
    c = ev("roo", "C", at(14), at(15))  # 開始時も予定中なら音のみ
    assert [(x.sound, x.text) for x in plan_event(b.start, [b], [c, b], CFG)][3] == ("pipoon", None)


def test_event_notifications_on_holiday_and_quiet_hours():
    hol = [HOLIDAY, ev("roo", "祝日の予定", hat(10, 30), hat(11))]
    assert not any(c.silent for c in plan_event(hol[1].start, [hol[1]], hol, CFG))
    late = ev("roo", "夜", at(22, 30), at(23))
    assert [c.text for c in plan_event(late.start, [late], [late], CFG)][3] == "22時30分です。夜です。"


def test_hour_chime_wins_over_event_notification():
    events = [ev("roo", "会議", at(15), at(16)), ev("roo", "打合せ", at(15, 5), at(15, 30))]
    got = audible(at(14, 54, 30), at(15, 6), events)
    labels = [(t, lbl) for t, lbl, _, _ in got]
    assert (at(15), "時報") in labels
    assert (at(15), "予定開始") not in labels  # 会議の開始は時報に負ける
    assert (at(15), "予定5分前") not in labels  # 打合せの 5 分前も時報に負ける
    assert (at(14, 55), "予定5分前") in labels and (at(14, 59, 45), "予定15秒前") in labels
    assert (at(15, 5), "予定開始") in labels


# --- カウントダウン (専用カレンダー / コマンド、時報・予定通知と独立) ---


def test_countdown_cues():
    got = [(c.at, c.sound, c.text) for c in cdm.cues_of(cdm.Countdown(at(15, 30), (30, 20, 10, 5, 2, 1)))]
    assert got[0] == (at(15), None, "15時30分まで、あと30分です。")
    assert got[-2] == (at(15, 29), None, "15時30分まで、あと1分です。")
    assert got[-1] == (at(15, 30), "pipoon", "15時30分です。")
    assert len(got) == 7


def test_countdown_calendar_is_independent():
    e = ev("cd", "出発", at(15, 30), at(16, 30))
    assert [c.at for c in cdm.from_events([e], CFG)] == [at(15, 30)]
    got = audible(at(14, 54, 30), at(16, 5), [e])
    assert (at(15), "時報", "pipipipoon", "15時です。") not in got  # CD30分前 が 15:00 で優先
    assert (at(16), "時報", "pipipipoon", "16時です。") in got  # 予定中にならない
    assert not [g for g in got if g[1].startswith("予定")]  # 予定通知にならない
    assert (at(15, 25), "CD5分前", None, "15時30分まで、あと5分です。") in got
    # 普段のカレンダーの ⏰ は特別扱いしない
    assert cdm.from_events([ev("roo", "⏰出発", at(15, 30), at(15, 30))], CFG) == []


def test_countdown_ignores_quiet_and_holiday():
    cd = cdm.cues_of(cdm.Countdown(at(23), (5,)))
    merged = cdm.merge(plan_window(at(22, 50), at(23, 5), [], CFG), cd)
    assert [c.text for c in merged if not c.silent] == ["23時まで、あと5分です。", "23時です。"]


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


# --- 5 分ごとの受け持ち ---


def test_next_target():
    assert next_target(at(13, 54, 30)) == at(14)
    assert next_target(at(14, 0, 30)) == at(14)
    assert next_target(at(14, 1)) == at(15)


def test_grid_floor_and_window_of():
    assert grid_floor(at(14, 4)) == at(14, 4)
    assert grid_floor(at(14, 8, 59)) == at(14, 4)
    assert grid_floor(at(14, 3)) == at(13, 59)
    assert window_of(at(13, 54)) == (at(13, 54, 30), at(13, 59, 30))
    assert window_of(at(13, 53, 40)) == (at(13, 54, 30), at(13, 59, 30))  # 少し早い起動
    assert window_of(at(13, 59)) == (at(13, 59, 30), at(14, 4, 30))


def test_windows_partition_all_cues():
    events = [
        ev("roo", "ジム", at(14, 1), at(14, 30)),
        ev("roo", "会議", at(14, 57), at(15, 30)),
        ev("cd", "出発", at(14, 40), at(14, 40)),
    ]
    whole = audible(at(13, 54, 30), at(15, 4, 30), events)
    parts = []
    for i in range(14):
        s, e = window_of(at(13, 54) + timedelta(minutes=5 * i))
        parts += audible(s, e, events)
    assert parts == whole and len(parts) == len(set(parts))
    labels = {(t, lbl) for t, lbl, _, _ in whole}
    assert {(at(13, 56), "予定5分前"), (at(14, 1), "予定開始"), (at(14), "時報"), (at(14, 40), "CD時刻")} <= labels


# --- 設定・ICS ---


def test_example_config_parses():
    # YAML 1.1 の罠 (off/on/yes/no が bool になる等) を実ファイルで検出する
    root = Path(__file__).parent.parent
    cfg = load_config(root / "config.example.yaml", root / "secrets.example.yaml")
    assert cfg.holiday is not None and cfg.holiday.weekdays == {5, 6} and len(cfg.holiday.matchers) == 2
    assert cfg.quiet_hours is not None and cfg.countdown.dedicated == {"countdown"}
    assert len(cfg.calendars) == 4


def test_secret_url_overrides_and_missing_url_skipped():
    raw = {"calendars": [{"name": "a", "url": "http://inline"}, {"name": "b"}]}
    cfg = parse_config(raw, {"calendars": {"a": "http://secret", "b": "http://b"}})
    assert [c.url for c in cfg.calendars] == ["http://secret", "http://b"]
    assert [c.name for c in parse_config(raw).calendars] == ["a"]  # url 無しは警告してスキップ
    assert "http" not in repr(cfg.calendars)


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
