from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hourly_chime.config import parse_config
from hourly_chime.ical import Event, parse_events
from hourly_chime.main import next_target
from hourly_chime.rules import plan_hour

TZ = ZoneInfo("Asia/Tokyo")

CFG = parse_config(
    {
        "calendars": [
            {"name": "roo", "url": "x"},
            {"name": "tai", "url": "y", "busy_only": True},
            {"name": "holiday", "url": "z", "mute_all_day": True},
        ],
        "quiet_hours": {"start": "20:00", "end": "08:00"},
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


def test_mute_all_day_calendar():
    events = [ev("holiday", "祝日", at(0), at(0, day=29), all_day=True)]
    assert all(p.silent for p in plan_hour(at(14), events, CFG))


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
