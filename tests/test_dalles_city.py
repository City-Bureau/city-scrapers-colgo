"""
Tests for The Dalles spiders using the Open Media (OmpNetwork) sessions API.

dalles_city_council.json is page 1 of
https://thedalles-oregon.ompnetwork.org/api/sessions/?category=1&limit=100&offset=0
fetched on 2026-10-19. dalles_api_cache_html.html is what the old
/api-cache/site/312/sessions endpoint returned the same day, and
dalles_drf_browsable.html is what /api/sessions/ returns to a browser-style
"Accept: text/html" request (Scrapy's default).
"""

import json
from datetime import datetime
from os.path import dirname, join
from unittest.mock import MagicMock

import pytest
from city_scrapers_core.constants import (
    BOARD,
    CANCELLED,
    CITY_COUNCIL,
    COMMISSION,
    PASSED,
    TENTATIVE,
)
from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from scrapy import signals
from scrapy.http import TextResponse

from city_scrapers.mixins.dalles_city import DallesApiError
from city_scrapers.spiders import dalles_city

ColgoDallesCityCouncilSpider = dalles_city.ColgoDallesCityCouncilSpider

FILES = join(dirname(__file__), "files")
API_URL = "https://thedalles-oregon.ompnetwork.org/api/sessions/?category=1&limit=100&offset=0"  # noqa

test_response = file_response(join(FILES, "dalles_city_council.json"), url=API_URL)


def _spider():
    spider = ColgoDallesCityCouncilSpider()
    spider.crawler = MagicMock()
    return spider


@pytest.fixture
def parse_output():
    spider = _spider()
    with freeze_time("2026-10-19"):
        return list(spider.parse(test_response))


@pytest.fixture
def parsed_items(parse_output):
    return [item for item in parse_output if not hasattr(item, "url")]


def test_count(parsed_items):
    assert len(parsed_items) == 100


def test_follows_next_page(parse_output):
    requests = [item for item in parse_output if hasattr(item, "url")]
    assert [r.url for r in requests] == [
        "https://thedalles-oregon.ompnetwork.org/api/sessions/?category=1&limit=100&offset=100"  # noqa
    ]
    assert requests[0].errback is not None


def test_start_url():
    spider = _spider()
    requests = list(spider.start_requests())
    assert [r.url for r in requests] == [API_URL]
    # Without it the API serves its HTML browsable page to Scrapy.
    assert requests[0].headers.get("Accept") == b"application/json"


def test_title(parsed_items):
    expected = "City Council Meeting October 26, 2026 - Livestream"
    assert parsed_items[0]["title"] == expected
    assert parsed_items[3]["title"] == "City Council Work Session - September 21, 2026"


def test_description(parsed_items):
    assert parsed_items[0]["description"] == ""


def test_classification(parsed_items):
    assert parsed_items[0]["classification"] == CITY_COUNCIL


def test_start_new_platform_session_is_utc(parsed_items):
    # Session 542 (created 2026-10-18): "2026-10-27T00:30:00Z"; the agenda
    # says "October 26, 2026 at 5:30 p.m."
    assert parsed_items[0]["start"] == datetime(2026, 10, 26, 17, 30)


def test_start_migrated_session_is_wall_clock(parsed_items):
    # Session 538 (created 2026-09-21): "2026-09-28T17:30:00Z"; the agenda
    # says "September 28, 2026 at 5:30 p.m."
    assert parsed_items[2]["start"] == datetime(2026, 9, 28, 17, 30)


def test_end(parsed_items):
    assert parsed_items[0]["end"] is None


def test_time_notes(parsed_items):
    assert parsed_items[0]["time_notes"] == ""


def test_id(parsed_items):
    assert parsed_items[2]["id"] == (
        "colgo_dalles_city_council/202609281730/x/"
        "city_council_meeting_september_28_2026_livestream"
    )


def test_status(parsed_items):
    assert parsed_items[0]["status"] == TENTATIVE
    assert parsed_items[1]["status"] == PASSED
    # Raw title "CANCELLED | City Council Meeting February 9, 2026"
    assert parsed_items[19]["title"] == "City Council Meeting February 9, 2026"
    assert parsed_items[19]["status"] == CANCELLED


def test_location(parsed_items):
    assert parsed_items[0]["location"] == {
        "name": "Location unavailable",
        "address": "Check source or agenda for location details",
    }


def test_source(parsed_items):
    assert (
        parsed_items[0]["source"]
        == "https://thedalles-oregon.ompnetwork.org/sessions/542"
    )


def test_links(parsed_items):
    assert parsed_items[0]["links"] == [
        {
            "href": "https://dcwecje7bjjjc.cloudfront.net/media/16/documents/CC_2026-10-26_City_Council_Agenda.pdf",  # noqa
            "title": "Agenda",
        },
        {
            "href": "https://dcwecje7bjjjc.cloudfront.net/media/16/documents/CC_2026-10-26_City_Council_Agenda_Z4o6TlC.pdf",  # noqa
            "title": "Packet",
        },
    ]
    assert parsed_items[1]["links"][0] == {
        "href": "https://www.youtube.com/live/iKqgJNyA3-o?si=gEXPw3OpnOz1Pf0_",
        "title": "Video",
    }


def test_all_day(parsed_items):
    assert parsed_items[0]["all_day"] is False


# Failure handling


def test_old_endpoint_html_raises():
    """The old api-cache URL now serves the app's HTML shell; it must raise."""
    response = file_response(
        join(FILES, "dalles_api_cache_html.html"),
        url="https://thedalles-oregon.ompnetwork.org/api-cache/site/312/sessions?category[]=214&start=0&limit=100",  # noqa
    )
    with pytest.raises(DallesApiError, match="did not return JSON"):
        list(_spider().parse(response))


def test_browsable_api_html_raises():
    """What the API returns for Scrapy's default Accept header."""
    response = file_response(join(FILES, "dalles_drf_browsable.html"), url=API_URL)
    with pytest.raises(DallesApiError, match="did not return JSON"):
        list(_spider().parse(response))


def test_json_without_results_raises():
    response = TextResponse(
        url=API_URL, body=b'{"detail": "Not found."}', encoding="utf-8"
    )
    with pytest.raises(DallesApiError, match="no 'results' list"):
        list(_spider().parse(response))


def test_other_category_items_are_skipped_and_reported():
    """If the API ignores the category filter, don't publish other agencies."""
    data = json.loads(test_response.text)
    data["results"][0]["category"]["id"] = 5
    data["next"] = None
    response = TextResponse(
        url=API_URL, body=json.dumps(data).encode(), encoding="utf-8"
    )
    spider = _spider()
    with freeze_time("2026-10-19"):
        items = list(spider.parse(response))
    assert len(items) == 99
    spider._on_spider_closed(spider, "finished")
    _assert_reported(spider, "1 item(s) from other categories skipped")


def _assert_reported(spider, text):
    calls = [
        c
        for c in spider.crawler.signals.send_catch_log.call_args_list
        if c.kwargs.get("signal") is signals.spider_error
    ]
    assert len(calls) == 1
    error = calls[0].kwargs["failure"].value
    assert isinstance(error, DallesApiError)
    assert text in str(error)


def test_zero_meetings_reports_error():
    spider = _spider()
    response = TextResponse(
        url=API_URL,
        body=b'{"count": 0, "next": null, "previous": null, "results": []}',
        encoding="utf-8",
    )
    assert list(spider.parse(response)) == []
    spider._on_spider_closed(spider, "finished")
    _assert_reported(spider, "0 meetings parsed")


def test_failed_request_reports_error():
    spider = _spider()
    with freeze_time("2026-10-19"):
        list(spider.parse(test_response))
    spider._request_failed(MagicMock(value=Exception("HTTP 503")))
    spider._on_spider_closed(spider, "finished")
    _assert_reported(spider, "1 failed sessions request(s)")


def test_good_run_reports_nothing():
    spider = _spider()
    with freeze_time("2026-10-19"):
        list(spider.parse(test_response))
    spider._on_spider_closed(spider, "finished")
    spider.crawler.signals.send_catch_log.assert_not_called()


# Test other spiders to ensure they are configured correctly


def test_informational_spider():
    """Test Informational/Town Hall spider configuration."""
    spider = dalles_city.ColgoDallesInformationalSpider()
    assert spider.name == "colgo_dalles_informational"
    assert spider.agency == "The Dalles Informational or Town Hall Meetings"
    assert spider.category_id == "2"


def test_planning_commission_spider():
    """Test Planning Commission spider configuration."""
    spider = dalles_city.ColgoDallesPlanningCommissionSpider()
    assert spider.name == "colgo_dalles_planning_commission"
    assert spider.agency == "The Dalles Planning Commission"
    assert spider.category_id == "3"
    assert spider._classification == COMMISSION


def test_urban_renewal_spider():
    """Test Urban Renewal Agency spider configuration."""
    spider = dalles_city.ColgoDallesUrbanRenewalSpider()
    assert spider.name == "colgo_dalles_urban_renewal"
    assert spider.agency == "The Dalles Urban Renewal Agency"
    assert spider.category_id == "5"
    assert spider._classification == BOARD


def test_historic_landmarks_spider():
    """Test Historic Landmarks Commission spider configuration."""
    spider = dalles_city.ColgoDallesHistoricLandmarksSpider()
    assert spider.name == "colgo_dalles_historic_landmarks"
    assert spider.agency == "The Dalles Historic Landmarks Commission"
    assert spider.category_id == "4"
    assert spider._classification == COMMISSION
