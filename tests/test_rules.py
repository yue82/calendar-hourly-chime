from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from calendar_hourly_chime import countdown as cdm
from calendar_hourly_chime.config import load_config, parse_config
from calendar_hourly_chime.ical import Event, parse_events
from calendar_hourly_chime.main import collect, grid_floor, next_target, window_of
from calendar_hourly_chime.rules import plan_event, plan_hour, resolve

TZ = ZoneInfo("Asia/Tokyo")

RAW = (
    {
        "calendars": [
            {"name": "roo", "url": "x"},
            {"name": "tai", "url": "y", "busy_only": True},
            {"name": "holiday", "url": "z"},
            {"name": "cd", "url": "w", "countdown": True},
        ],
        "hour_chime": {"weekday_hours": list(range(8, 21)), "holiday_hours": [8, 12, 16, 20]},
        "holiday": {
            "weekdays": ["sat", "sun"],
            "all_day": [
                {"calendar": "holiday", "description": "^祝日"},
                {"calendar": "roo", "title": "有休|休暇|休み"},
            ],
        },
    }
)
CFG = parse_config(RAW)


def at(h: int, m: int = 0, s: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 9, day, h, m, s, tzinfo=TZ)  # 2026-09-28 は月曜


def ev(cal: str, title: str, s: datetime, e: datetime, all_day: bool = False, desc: str = "") -> Event:
    return Event(cal, title, s, e, all_day, desc)


def hour(target: datetime, events: list[Event]) -> tuple[str | None, str | None]:
    c = plan_hour(target, events, CFG)
    return c.sound, c.text


def audible_cues(start: datetime, end: datetime, events: list[Event], cfg=CFG):
    return [c for c in collect(start, end, events, cfg, None) if not c.silent]


def audible(start: datetime, end: datetime, events: list[Event], cfg=CFG) -> list[tuple]:
    return [(c.at, c.label, c.sound, c.text) for c in collect(start, end, events, cfg, None) if not c.silent]


# --- 平日の時報 ---


def test_weekday_hour_chime():
    assert hour(at(14), []) == ("pipipipoon", "14時です。")


def test_weekday_announces_until_next_chime():
    events = [
        ev("roo", "ジム", at(14, 10), at(14, 30)),
        ev("tai", "予定あり", at(14, 30), at(14, 45)),  # 時間枠のみ → 「予定があります」
        ev("roo", "次の時報ちょうど", at(15), at(16)),  # 次の時報 (15時) ちょうど → 読む
        ev("roo", "その後", at(15, 1), at(16)),  # 次の時報より後 → 読まない
        ev("roo", "連休", at(0), at(0, day=29), all_day=True),  # 終日 → 無関係
    ]
    assert hour(at(14), events) == (
        "pipipipoon",
        "14時です。14時10分から、ジムです。14時30分から、予定があります。15時から、次の時報ちょうどです。",
    )
    # 20時の次の時報は翌朝 8 時
    night = [ev("roo", "夜", at(22), at(23)), ev("roo", "朝", at(8, day=29), at(9, day=29))]
    assert hour(at(20), night) == ("pipipipoon", "20時です。22時から、夜です。8時から、朝です。")


def test_hour_chime_next_chime_block_with_titled_event_inside():
    # 18時の時報で、19時からの tai の枠の中にある 19:25 のジムを読む
    events = [ev("tai", "予定あり", at(19), at(20)), ev("roo", "ジム", at(19, 25), at(19, 45))]
    assert hour(at(18), events)[1] == "18時です。19時25分から、ジムです。"


def test_hour_chime_sound_only_when_busy():
    assert hour(at(14), [ev("roo", "作業", at(13), at(15))]) == ("pipipipoon", None)
    assert hour(at(14), [ev("roo", "会議", at(14), at(15))]) == ("pipipipoon", None)  # ちょうど開始も予定中
    assert hour(at(14), [ev("roo", "会議", at(13), at(14))]) == ("pipipipoon", "14時です。")  # 終わっている


def test_weekday_hours():
    assert plan_hour(at(21), [], CFG).silent
    assert plan_hour(at(3), [], CFG).silent
    assert hour(at(20), []) == ("pipipipoon", "20時です。")
    assert hour(at(8), []) == ("pipipipoon", "8時です。")


def test_hour_chime_settings():
    cfg = parse_config({"hour_chime": {"sound": "pipoon", "text": "{hour}時。", "announce": False, "when_busy": "skip"}})
    events = [ev("x", "予定", at(14, 30), at(15))]
    c = plan_hour(at(14), events, cfg)
    assert (c.sound, c.text) == ("pipoon", "14時。")
    assert plan_hour(at(14), [ev("x", "会議", at(14), at(15))], cfg).silent  # when_busy: skip


def test_untitled_skipped_when_titled_event_at_same_time():
    events = [
        ev("roo", "会議", at(14, 30), at(15)),
        ev("tai", "予定あり", at(14, 30), at(15)),
        ev("roo", "", at(14, 45), at(15)),
    ]
    assert hour(at(14), events)[1] == "14時です。14時30分から、会議です。14時45分から、予定があります。"


def test_hour_chime_uses_titled_event_inside_untitled_block():
    block = ev("tai", "予定あり", at(14, 30), at(16, 30))
    lunch = ev("roo", "りうむめし", at(15, 30), at(16))  # tai の枠内、開始は次の時報より後でもよい
    assert hour(at(14), [block, lunch])[1] == "14時です。15時30分から、りうむめしです。"
    # 枠からはみ出す予定は使わない
    long = ev("roo", "長い", at(15, 30), at(17))
    assert hour(at(14), [block, long])[1] == "14時です。14時30分から、予定があります。"
    # 同じ時間の予定は名前のある方
    same = ev("roo", "会議", at(14, 30), at(16, 30))
    assert hour(at(14), [block, same])[1] == "14時です。14時30分から、会議です。"
    # 予定通知 (5分前) は今のまま
    assert plan_event(block.start, [block], [block, lunch], CFG)[0].text == "14時30分から、予定があります。"


def test_weekday_pre_cue():
    got = audible(at(13, 54, 30), at(14, 0, 30), [])
    assert [(t, lbl, snd, txt) for t, lbl, snd, txt in got] == [
        (at(13, 55), "時報5分前", "popopopopo", None),
        (at(14), "時報", "pipipipoon", "14時です。"),
    ]
    assert not audible(at(20, 54, 30), at(21, 0, 30), [])  # 21時は時報なし → 5分前もなし
    assert audible(at(7, 54, 30), at(7, 59, 30), [])[0][1] == "時報5分前"  # 8時の分は 7:55


def test_pre_cue_not_on_holiday():
    assert [g[1] for g in audible(at(11, 54, day=3), at(12, 0, 30, day=3), [HOLIDAY])] == ["時報"]


def test_event_notice_wins_over_pre_cue():
    e = ev("roo", "会議", at(14), at(15))
    got = [(t, lbl, txt) for t, lbl, _, txt in audible(at(13, 54, 30), at(13, 56), [e])]
    assert got == [(at(13, 55), "予定5分前", "14時から、会議です。")]


def test_priority_missing_kinds_appended():
    assert parse_config({"priority": ["event_notice"]}).priority == (
        "event_notice", "countdown", "hour_chime"
    )


def test_busy_volume():
    busy = [ev("roo", "作業", at(13), at(15))]
    assert plan_hour(at(14), busy, CFG).volume == 0.2  # 時報は予定中に音量 20%
    assert plan_hour(at(14), [], CFG).volume == 1.0
    pre = [c for c in audible_cues(at(13, 54, 30), at(13, 56), busy) if c.label == "時報5分前"]
    assert pre[0].volume == 0.2  # 5分前も時報の一部
    e = ev("roo", "B", at(14, 30), at(15))
    assert plan_event(e.start, [e], busy + [e], CFG)[0].volume == 1.0  # 予定通知は既定 1.0


def test_compose_volume(tmp_path, monkeypatch):
    import wave
    from array import array

    from calendar_hourly_chime import sounds

    monkeypatch.setattr(sounds, "SLOT_CACHE", tmp_path)

    def peak(p):
        with wave.open(str(p)) as w:
            return max(abs(x) for x in array("h", w.readframes(w.getnframes())))

    full, _ = sounds.compose("poon", None)
    low, _ = sounds.compose("poon", None, volume=0.3)
    assert full != low and abs(peak(low) / peak(full) - 0.3) < 0.01


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
        assert dict(got)[20] == ("pipipipoon", "20時です。")


def test_holiday_announces_until_next_chime():
    events = [
        HOLIDAY,
        ev("roo", "ランチ", hat(12, 30), hat(13, 30)),
        ev("roo", "買い物", hat(15, 50), hat(17)),
        ev("roo", "夕飯", hat(16), hat(17)),  # 次の時報 (16時) ちょうど → 読む
        ev("roo", "夜", hat(16, 30), hat(17)),  # 次の時報より後 → 読まない
    ]
    assert hour(hat(12), events) == (
        "pipipipoon",
        "12時です。12時30分から、ランチです。15時50分から、買い物です。16時から、夕飯です。",
    )
    late = [HOLIDAY, ev("roo", "早朝", hat(7, day=4), hat(8, day=4)), ev("roo", "朝会", hat(8, day=4), hat(9, day=4))]
    assert hour(hat(20), late) == ("pipipipoon", "20時です。7時から、早朝です。8時から、朝会です。")  # 20時の次は翌朝 8 時


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
        (at(19, 20), "popopopopo", "19時25分から、ジムです。"),
        (at(19, 23), "popo", None),
        (at(19, 24, 40), "poon", "ジムです。"),
        (at(19, 25), "pipoon", None),  # 開始時は言葉なし (会議が始まっているかもしれない)
    ]


def test_event_notifications_untitled():
    t = ev("tai", "予定あり", at(10, 30), at(11))
    cues = plan_event(t.start, [t], [t], CFG)
    assert [(c.sound, c.text) for c in cues] == [
        ("popopopopo", "10時30分から、予定があります。"),
        ("popo", None),
        ("poon", None),  # 予定名が無ければ 20 秒前は音だけ
        ("pipoon", None),
    ]


def test_event_notifications_sound_only_during_other_event():
    a = ev("roo", "A", at(14), at(14, 40))
    b = ev("roo", "B", at(14, 30), at(15))
    cues = plan_event(b.start, [b], [a, b], CFG)
    assert [(c.sound, c.text) for c in cues] == [("popopopopo", None), ("popo", None), ("poon", None), ("pipoon", None)]


def test_event_notifications_on_holiday_and_quiet_hours():
    hol = [HOLIDAY, ev("roo", "祝日の予定", hat(10, 30), hat(11))]
    assert plan_event(hol[1].start, [hol[1]], hol, CFG)[0].text == "10時30分から、祝日の予定です。"
    late = ev("roo", "夜", at(22, 30), at(23))
    assert plan_event(late.start, [late], [late], CFG)[2].text == "夜です。"


def test_event_notification_wins_over_hour_chime():
    events = [ev("roo", "会議", at(15), at(16))]
    got = [(t, lbl, snd, txt) for t, lbl, snd, txt in audible(at(14, 59, 30), at(15, 1), events)]
    assert got == [(at(14, 59, 40), "予定20秒前", "poon", "会議です。"), (at(15), "予定開始", "pipoon", None)]
    events = [ev("roo", "打合せ", at(15, 5), at(15, 30))]
    got = [(t, lbl, txt) for t, lbl, _, txt in audible(at(14, 59, 30), at(15, 1), events)]
    assert got == [(at(15), "予定5分前", "15時5分から、打合せです。")]  # 時報は鳴らない


def test_hour_chime_priority_configurable():
    cfg = parse_config({**RAW, "priority": ["countdown", "hour_chime", "event_notice"]})
    events = [ev("roo", "会議", at(15), at(16)), ev("roo", "打合せ", at(15, 5), at(15, 30))]
    got = audible(at(14, 54, 30), at(15, 6), events, cfg)
    labels = [(t, lbl) for t, lbl, _, _ in got]
    assert (at(15), "時報") in labels
    assert (at(15), "予定開始") not in labels
    assert (at(15), "予定5分前") not in labels
    assert (at(14, 55), "時報5分前") in labels and (at(14, 55), "予定5分前") not in labels  # 時報の一部として勝つ
    assert (at(14, 59, 40), "予定20秒前") in labels and (at(15, 5), "予定開始") in labels


# --- カウントダウン (専用カレンダー / コマンド、時報・予定通知と独立) ---


def test_countdown_cues_with_label():
    cd = cdm.Countdown(at(15, 30), label="出発")
    got = [(c.at, c.sound, c.text) for c in cdm.cues_of(cd, CFG)]
    assert got == [
        (at(15), "pin", "15時30分の出発まで、あと30分です。"),
        (at(15, 10), "pin", "出発まで、あと20分です。"),
        (at(15, 20), "pinpin", "15時30分の出発まで、あと10分です。"),
        (at(15, 25), "pinpin", "出発まで、あと5分です。"),
        (at(15, 28), "pinpinpin", "出発まで、あと2分です。"),
        (at(15, 29), "pinpinpin", "出発まで、あと1分です。"),
        (at(15, 30), "pipoon", "15時30分、出発の時間です。"),
    ]


def test_countdown_cues_without_label():
    got = [c.text for c in cdm.cues_of(cdm.Countdown(at(15, 30), (30, 20, 1)), CFG)]  # offsets で絞る
    assert got == ["15時30分まで、あと30分です。", "あと20分です。", "あと1分です。", "15時30分です。"]


def test_countdown_calendar_is_independent():
    e = ev("cd", "出発", at(15, 30), at(16, 30))
    assert [(c.at, c.label) for c in cdm.from_events([e], CFG)] == [(at(15, 30), "出発")]
    got = audible(at(14, 54, 30), at(16, 5), [e])
    assert (at(15), "CD30分前", "pin", "15時30分の出発まで、あと30分です。") in got  # 15:00 の時報より優先
    assert not [g for g in got if g[1] == "時報" and g[0] == at(15)]
    assert (at(16), "時報", "pipipipoon", "16時です。") in got  # 予定中にならない
    assert not [g for g in got if g[1].startswith("予定")]  # 予定通知にならない
    assert cdm.from_events([ev("roo", "⏰出発", at(15, 30), at(15, 30))], CFG) == []  # ⏰ は特別扱いしない


def test_countdown_sound_only_during_other_event():
    cd = ev("cd", "出発", at(15, 30), at(15, 30))
    mtg = ev("roo", "会議", at(15, 20), at(15, 29))
    got = {g[1]: (g[2], g[3]) for g in audible(at(15, 4, 30), at(15, 34, 30), [cd, mtg])}
    assert got["CD10分前"] == ("pinpin", None)
    assert got["CD5分前"] == ("pinpin", None)
    assert got["CD1分前"] == ("pinpinpin", "出発まで、あと1分です。")  # 15:29 は会議終了


def test_countdown_ignores_night_and_holiday():
    got = [c.text for c in resolve(cdm.cues_of(cdm.Countdown(at(23), (5,)), CFG), CFG) if not c.silent]
    assert got == ["あと5分です。", "23時です。"]


def test_custom_cues_and_priority():
    cfg = parse_config(
        {
            "event_notice": {"cues": [{"before": "1m", "sound": "pin", "text": "{title}、1分前。"}], "when_busy": "normal"},
            "countdown": {"cues": [{"before": "0s", "sound": "popo", "text": "{title}!"}]},
            "priority": ["event_notice", "countdown", "hour_chime"],
        }
    )
    e = ev("x", "打合せ", at(15, 1), at(15, 30))
    other = ev("x", "作業", at(14), at(16))
    cues = plan_event(e.start, [e], [e, other], cfg)
    assert [(c.at, c.sound, c.text) for c in cues] == [(at(15), "pin", "打合せ、1分前。")]  # when_busy: normal
    cd = cdm.cues_of(cdm.Countdown(at(15), label="出発"), cfg)
    assert [(c.sound, c.text) for c in cd] == [("popo", "出発!")]
    hour_cue = plan_hour(at(15), [], cfg)
    got = {c.kind: c.silent for c in resolve([hour_cue, *cues, *cd], cfg)}
    assert got == {"event_notice": False, "countdown": True, "hour_chime": True}  # event_notice が最優先


def test_parse_duration():
    from calendar_hourly_chime.config import parse_duration

    assert parse_duration("5m") == timedelta(minutes=5)
    assert parse_duration("20s") == timedelta(seconds=20)
    assert parse_duration("1h") == timedelta(hours=1)
    assert parse_duration(0) == timedelta(0)


def test_parse_time():
    now = at(14, 10, 30)
    assert cdm.parse_time("15:30", now) == at(15, 30)
    assert cdm.parse_time("1530", now) == at(15, 30)
    assert cdm.parse_time("930", now) == at(9, 30, day=29)  # 過ぎていれば明日
    assert cdm.parse_time("+45", now) == at(14, 55)
    assert cdm.parse_time("2026-09-30T08:00", now) == at(8, day=30)


def test_store_roundtrip(tmp_path):
    p = tmp_path / "cd.json"
    a = cdm.add(at(15, 30), (10, 5), at(14), p, label="出発")
    cdm.add(at(16), (5,), at(14), p)
    assert [(c.at, c.label) for c in cdm.load(p)] == [(at(15, 30), "出発"), (at(16), None)]
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
    assert cfg.holiday.weekdays == {5, 6} and len(cfg.holiday.all_day) == 2
    assert cfg.hour_chime.weekday_hours == tuple(range(8, 21)) and cfg.hour_chime.holiday_hours == (8, 12, 16, 20)
    assert cfg.countdown.dedicated == {"countdown"} and len(cfg.countdown.cues) == 7
    assert [s.before.total_seconds() for s in cfg.event_notice.cues] == [300, 120, 20, 0]
    assert cfg.priority == ("countdown", "event_notice", "hour_chime")
    assert cfg.hour_chime == parse_config({}).hour_chime
    assert len(cfg.calendars) == 4
    # 組み込みの既定値と同じ内容を書いている
    assert cfg.event_notice.cues == parse_config({}).event_notice.cues
    assert cfg.countdown.cues == parse_config({}).countdown.cues


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
TRANSP:TRANSPARENT
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
    assert off.transparent and not mtg.transparent


def test_windows_script_one_item_per_add():
    # 要素 1 つでも配列が展開されないよう、1 件ずつ ArrayList に足している
    from calendar_hourly_chime.player import Scheduled, windows_script

    one = windows_script([Scheduled(Path("a.wav"), 1.5, "時報")], [r"\\wsl\a.wav"])
    assert "[void]$items.Add(@('\\\\wsl\\a.wav', 1500, '時報'))" in one
    assert "$items = @(" not in one
    two = windows_script([Scheduled(Path("a"), 1, "x"), Scheduled(Path("b"), 2, "y")], ["a", "b"])
    assert two.count("[void]$items.Add(") == 2


# --- 応答不可 ---


def test_dnd_decide():
    from calendar_hourly_chime.dnd import DndState, decide

    mtg = ev("roo", "会議", at(14), at(15))
    day = at(9).date().isoformat()
    st = DndState(last_day=day)

    # 予定開始でオフならオンにし、自分がオンにしたと記録
    act, st1, _ = decide(at(14, 0, 1), mtg, False, st, True)
    assert act is True and st1.managed
    # 予定中でオンのままなら何もしない
    assert decide(at(14, 30), mtg, True, st1, True)[0] is None
    # 予定終了で、自分がオンにしたものはオフに戻す
    act, st2, _ = decide(at(15, 0, 1), None, True, st1, True)
    assert act is False and not st2.managed
    # 手動でオンにしたもの (managed でない) は予定が無くても触らない
    assert decide(at(16), None, True, st, True)[0] is None
    # 予定中に手動でオフにされたら、その予定の間はオンにしない
    act, st3, _ = decide(at(14, 20), mtg, False, st1, True)
    assert act is None and st3.override_until == mtg.end.isoformat()
    assert decide(at(14, 40), mtg, False, st3, True)[0] is None
    nxt = ev("roo", "次", at(15, 30), at(16))
    assert decide(at(15, 30, 1), nxt, False, st3, True)[0] is True  # 次の予定ではまたオンにする


def test_dnd_first_run_of_day():
    from calendar_hourly_chime.dnd import DndState, decide

    yesterday = DndState(managed=False, last_day=at(9, day=27).date().isoformat())
    # その日最初の確認で予定が無ければ、手動オンでもオフにする
    act, st, _ = decide(at(9), None, True, yesterday, True)
    assert act is False and st.last_day == at(9).date().isoformat()
    assert decide(at(9, 5), None, True, st, True)[0] is None  # 2 回目以降は手動オンに触らない
    # 予定中なら通常どおり (オンにする)
    assert decide(at(9), ev("roo", "朝会", at(9), at(10)), False, yesterday, True)[0] is True
    # first_run_off が無効なら手動オンに触らない
    assert decide(at(9), None, True, yesterday, False)[0] is None


def test_dnd_window_margins_and_back_to_back():
    from calendar_hourly_chime.main import dnd_events
    from calendar_hourly_chime.rules import busy_at

    cfg = parse_config({**RAW, "do_not_disturb": {"enabled": True}})
    evs = dnd_events([ev("roo", "A", at(10), at(11)), ev("roo", "B", at(11), at(12)), ev("cd", "CD", at(13), at(14))], cfg)
    assert busy_at(at(9, 59, 30), evs) and not busy_at(at(9, 59, 29), evs)  # 30 秒前からオン
    assert busy_at(at(11), evs) and busy_at(at(11, 0, 30), evs)  # 連続する予定の間も途切れない
    assert busy_at(at(12, 0, 29), evs) and not busy_at(at(12, 0, 30), evs)  # 30 秒後にオフ
    assert not busy_at(at(13, 30), evs)  # カウントダウン専用カレンダーは対象外
    free = Event("roo", "断水", at(16), at(17), False, "", transparent=True)
    assert not busy_at(at(16, 30), dnd_events([free], cfg))  # 「予定なし」は対象外
