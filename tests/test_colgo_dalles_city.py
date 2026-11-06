"""
dalles_city_calendar.json: Revize calendar feed fetched 2026-11-05.
dalles_city_council_sessions_sample.json: sample sessions API page (category 1).
"""

import json
from datetime import datetime
from os.path import dirname, join
from unittest.mock import MagicMock

import pytest
from city_scrapers_core.constants import (
    CANCELLED,
    CITY_COUNCIL,
    PASSED,
    TENTATIVE,
)
from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from scrapy.http import TextResponse

from city_scrapers.mixins.colgo_dalles_city import DallesApiError
from city_scrapers.spiders import colgo_dalles_city

ColgoDallesCityCouncilSpider = colgo_dalles_city.ColgoDallesCityCouncilSpider

FILES = join(dirname(__file__), "files")
API_URL = "https://thedalles-oregon.ompnetwork.org/api/sessions/?category=1&limit=100&offset=0"  # noqa
CALENDAR_URL = ColgoDallesCityCouncilSpider.calendar_url

calendar_response = file_response(
    join(FILES, "colgo_dalles_city_calendar.json"), url=CALENDAR_URL
)
sessions_response = file_response(
    join(FILES, "colgo_dalles_city_council_sessions_sample.json"), url=API_URL
)


def _spider():
    spider = ColgoDallesCityCouncilSpider()
    spider.crawler = MagicMock()
    return spider


def _by_start(items, *args):
    matches = [item for item in items if item["start"] == datetime(*args)]
    assert len(matches) == 1, matches
    return matches[0]


@pytest.fixture
def parsed_items():
    spider = _spider()
    with freeze_time("2026-11-05"):
        list(spider.parse_sessions(sessions_response))
        return list(spider.parse(calendar_response))


def test_start_url():
    requests = list(_spider().start_requests())
    assert [r.url for r in requests] == [API_URL]
    assert requests[0].headers.get("Accept") == b"application/json"


def test_sessions_then_calendar():
    requests = list(_spider().parse_sessions(sessions_response))
    assert [r.url for r in requests] == [CALENDAR_URL]


def test_sessions_failure_still_requests_calendar():
    spider = _spider()
    requests = list(spider._sessions_failed(MagicMock(value=Exception("HTTP 503"))))
    assert [r.url for r in requests] == [CALENDAR_URL]


def test_count(parsed_items):
    assert len(parsed_items) == 74


def test_meeting(parsed_items):
    item = _by_start(parsed_items, 2026, 10, 26, 17, 30)
    assert item["title"] == "City Council Meeting"
    assert item["end"] == datetime(2026, 10, 26, 20, 30)
    assert item["classification"] == CITY_COUNCIL
    assert item["status"] == PASSED
    assert item["location"] == {
        "name": "City Hall",
        "address": "313 Court Street, The Dalles, OR 97058",
    }
    assert item["description"].startswith(
        "City Council meetings are livestreamed and past video recordings are "
        "available on this page:\nCity Council Livestream Links and Video Recordings "
        "(https://www.thedalles.gov/government/meetings___agendas/index.php)"
    )
    assert item["source"] == (
        "https://www.thedalles.gov/calendar.php?view=month&month=10&day=01&year=2026"
    )
    assert item["id"] == "colgo_dalles_city_council/202610261730/x/city_council_meeting"


def test_recurring_meeting_is_expanded(parsed_items):
    # Generated from the 2nd Monday rrule; not an event in the feed
    item = _by_start(parsed_items, 2026, 11, 9, 17, 30)
    assert item["title"] == "City Council Meeting"
    assert item["status"] == TENTATIVE
    # The 2nd Monday record has a blank location; falls back to City Hall
    assert item["location"]["name"] == "City Hall"


def test_exdate_replaced_by_one_off_event(parsed_items):
    jan_12 = [
        m for m in parsed_items if m["start"].date() == datetime(2026, 1, 12).date()
    ]
    assert [m["title"] for m in jan_12] == [
        "City Council Meeting - Time & Location Change"
    ]


def test_cancelled(parsed_items):
    # "CANCELLED - City Council Meeting"
    item = _by_start(parsed_items, 2026, 10, 12, 17, 30)
    assert item["title"] == "City Council Meeting"
    assert item["status"] == CANCELLED
    assert item["description"] == (
        "This meeting is cancelled due to the Monday Holiday.\n"
        "A Special Meeting will be held Tuesday, October 13th."
    )


@pytest.mark.parametrize("order", [1, -1])
def test_cancelled_duplicate_wins(order):
    events = [
        {"title": title, "calendar_displays": ["1"], "start": "2026-11-16T17:30:00"}
        for title in ["City Council Meeting", "CANCELLED - City Council Meeting"]
    ][::order]
    response = TextResponse(url=CALENDAR_URL, body=json.dumps(events).encode())
    with freeze_time("2026-11-05"):
        items = list(_spider().parse(response))
    assert [item["status"] for item in items] == [CANCELLED]


def test_links(parsed_items):
    # Session 542 stores real UTC: "2026-10-27T00:30:00Z"
    assert _by_start(parsed_items, 2026, 10, 26, 17, 30)["links"] == [
        {
            "href": "https://dcwecje7bjjjc.cloudfront.net/media/16/documents/CC_2026-10-26_City_Council_Agenda.pdf",  # noqa
            "title": "Agenda",
        },
        {
            "href": "https://dcwecje7bjjjc.cloudfront.net/media/16/documents/CC_2026-10-26_City_Council_Agenda_Z4o6TlC.pdf",  # noqa
            "title": "Packet",
        },
    ]
    # Session 538 stores Pacific wall-clock time: "2026-09-28T17:30:00Z"
    assert _by_start(parsed_items, 2026, 9, 28, 17, 30)["links"][0] == {
        "href": "https://www.youtube.com/live/iKqgJNyA3-o?si=gEXPw3OpnOz1Pf0_",
        "title": "Video",
    }


def test_work_session_not_matched_to_regular_meeting():
    spider = _spider()
    spider._sessions = [
        {
            "title": "City Council Work Session",
            "start_date": "2026-11-09T16:00:00Z",
            "created_at": "2020-01-01T00:00:00Z",
            "video_url": "https://example.org/work-session",
        }
    ]
    meeting = {"title": "City Council Meeting", "start": datetime(2026, 11, 9, 17, 30)}
    meeting["links"] = []
    spider._attach_links([meeting])
    assert meeting["links"] == []
    assert spider._unmatched_sessions == 1


def test_calendar_not_json_raises():
    response = TextResponse(url=CALENDAR_URL, body=b"<html></html>")
    with pytest.raises(DallesApiError, match="calendar did not return JSON"):
        list(_spider().parse(response))


def test_zero_meetings_reports_error():
    spider = _spider()
    list(spider.parse(TextResponse(url=CALENDAR_URL, body=b"[]")))
    spider._on_spider_closed(spider, "finished")
    spider.crawler.signals.send_catch_log.assert_called_once()
