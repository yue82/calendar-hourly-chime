from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hourly_chime.config import parse_config
from hourly_chime.ical import Event, parse_events
from hourly_chime.rules import decide

TZ = ZoneInfo("Asia/Tokyo")

CFG = parse_config(
    {
        "calendars": [{"name": "private", "url": "x"}, {"name": "work", "url": "y"}],
        "quiet_hours": {"start": "23:00", "end": "07:00"},
        "rules": [
            {"name": "mtg", "title": "会議|MTG", "action": "mute"},
            {"name": "home", "calendar": "work", "title": "在宅", "action": {"message": "{hour}時。休憩({title})"}},
        ],
        "announce_next": {"within_minutes": 60, "exclude_title": "移動"},
    }
)


def at(h: int, m: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 9, day, h, m, tzinfo=TZ)


def ev(cal: str, title: str, s: datetime, e: datetime, all_day: bool = False) -> Event:
    return Event(cal, title, s, e, all_day)


def test_default():
    d = decide(at(14), [], CFG)
    assert d.text == "14時です。"


def test_quiet_hours_wraps_midnight():
    assert decide(at(23), [], CFG).text is None
    assert decide(at(3), [], CFG).text is None
    assert decide(at(7), [], CFG).text == "7時です。"


def test_quiet_hours_from_unquoted_yaml():
    # YAML で 23:00 をクォートし忘れると int (1380) になる
    cfg = parse_config({"quiet_hours": {"start": 1380, "end": 420}})
    assert decide(at(23), [], cfg).text is None


def test_mute_during_meeting():
    events = [ev("work", "定例会議", at(13, 30), at(14, 30))]
    assert decide(at(14), events, CFG).text is None


def test_meeting_starting_exactly_now_mutes_and_ended_does_not():
    assert decide(at(14), [ev("work", "MTG", at(14), at(15))], CFG).text is None
    assert decide(at(14), [ev("work", "MTG", at(13), at(14))], CFG).text == "14時です。"


def test_calendar_filter():
    # 「在宅」ルールは work カレンダーのみ
    home_private = [ev("private", "在宅", at(9), at(18))]
    assert decide(at(14), home_private, CFG).text == "14時です。"
    home_work = [ev("work", "在宅", at(9), at(18))]
    assert decide(at(14), home_work, CFG).text == "14時。休憩(在宅)"


def test_rule_order_first_match_wins():
    events = [ev("work", "在宅", at(9), at(18)), ev("work", "会議", at(14), at(15))]
    assert decide(at(14), events, CFG).text is None


def test_announce_next():
    events = [
        ev("private", "歯医者", at(14, 30), at(15, 30)),
        ev("private", "移動", at(14, 10), at(14, 30)),
        ev("private", "遠い予定", at(16), at(17)),
        ev("private", "連休", at(0), at(0) + timedelta(days=1), all_day=True),
    ]
    assert decide(at(14), events, CFG).text == "14時です。このあと14時30分から、歯医者です。"


def test_announce_disabled():
    cfg = parse_config({})
    events = [ev("private", "歯医者", at(14, 30), at(15))]
    assert decide(at(14), events, cfg).text == "14時です。"


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
    events = parse_events("work", ICS, at(0), at(0, day=29), TZ)
    titles = {e.title: e for e in events}
    assert set(titles) == {"週次MTG", "有給"}
    mtg = titles["週次MTG"]  # 2026-09-28 は月曜、05:00Z = 14:00 JST
    assert (mtg.start, mtg.end, mtg.all_day) == (at(14), at(15), False)
    off = titles["有給"]
    assert off.all_day and off.start == at(0) and off.end == at(0, day=29)
    assert decide(at(14), events, CFG).text is None
