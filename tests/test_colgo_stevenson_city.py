"""
Tests for the Stevenson spiders, which read the CivicClerk Events API.

colgo_stevenson_city_council_events.json is
https://stevensonwa.api.civicclerk.com/v1/Events
?$filter=categoryId eq 26&$orderby=startDateTime desc
fetched on 2026-10-19 (page 1 of 2).
"""

import json
from datetime import datetime
from os.path import dirname, join
from unittest.mock import MagicMock

import pytest
from city_scrapers_core.constants import (
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

from city_scrapers.mixins.colgo_stevenson_city import StevensonApiError
from city_scrapers.spiders import colgo_stevenson_city

ColgoStevensonCitySpider = colgo_stevenson_city.ColgoStevensonCitySpider
ColgoStevensonPlanningSpider = colgo_stevenson_city.ColgoStevensonPlanningSpider

API_URL = (
    "https://stevensonwa.api.civicclerk.com/v1/Events"
    "?%24filter=categoryId+eq+26&%24orderby=startDateTime+desc"
)
FIXTURE = join(dirname(__file__), "files", "colgo_stevenson_city_council_events.json")
test_response = file_response(FIXTURE, url=API_URL)


def _spider(cls=ColgoStevensonCitySpider):
    spider = cls()
    spider.crawler = MagicMock()
    return spider


@pytest.fixture
def parse_output():
    with freeze_time("2026-10-19"):
        return list(_spider().parse(test_response))


@pytest.fixture
def parsed_items(parse_output):
    return [x for x in parse_output if not hasattr(x, "url")]


def _reported(spider):
    return [
        c.kwargs["failure"].value
        for c in spider.crawler.signals.send_catch_log.call_args_list
        if c.kwargs.get("signal") is signals.spider_error
    ]


def test_start_request():
    requests = list(_spider().start_requests())
    assert [r.url for r in requests] == [API_URL]
    assert requests[0].headers.get("Accept") == b"application/json"
    assert requests[0].errback is not None


def test_planning_uses_its_category():
    spider = _spider(ColgoStevensonPlanningSpider)
    assert "categoryId+eq+27" in next(spider.start_requests()).url


def test_count(parsed_items):
    assert len(parsed_items) == 15


def test_follows_next_link(parse_output):
    requests = [x for x in parse_output if hasattr(x, "url")]
    assert len(requests) == 1
    assert "skiptoken" in requests[0].url
    assert requests[0].errback is not None


def test_item_with_files(parsed_items):
    item = parsed_items[11]
    assert item["title"] == "Regular City Council Meeting"
    # Portal: "6:00 PM PDT"; agenda: "October 15, 2026 6:00 PM"
    assert item["start"] == datetime(2026, 10, 15, 18, 0)
    assert item["end"] is None
    assert item["all_day"] is False
    assert item["classification"] == CITY_COUNCIL
    assert item["status"] == PASSED
    assert item["source"] == "https://stevensonwa.portal.civicclerk.com/event/526/files"
    assert item["links"] == [
        {
            "href": "https://stevensonwa.api.civicclerk.com/v1/Meetings/GetMeetingFileStream(fileId=1442,plainText=false)",  # noqa
            "title": "Agenda",
        },
        {
            "href": "https://stevensonwa.api.civicclerk.com/v1/Meetings/GetMeetingFileStream(fileId=1443,plainText=false)",  # noqa
            "title": "Agenda Packet",
        },
        {"href": "https://vimeo.com/1236425571", "title": "Video"},
    ]
    assert item["id"] == (
        "colgo_stevenson_city_council/202610151800/x/regular_city_council_meeting"
    )


def test_upcoming_item(parsed_items):
    item = parsed_items[10]
    assert item["start"] == datetime(2026, 11, 19, 18, 0)
    assert item["status"] == TENTATIVE
    assert item["links"] == []


def test_location_and_description(parsed_items):
    assert parsed_items[0]["location"] == {
        "name": "Stevenson City Hall Council Chambers",
        "address": "7121 East Loop Road, Stevenson, WA 98648",
    }
    assert parsed_items[0]["description"].startswith(
        "With the exception of executive session meetings"
    )


def test_cancelled_title():
    data = json.loads(open(FIXTURE).read())
    data["value"] = data["value"][10:11]
    data["value"][0]["eventName"] = "CANCELED Regular City Council Meeting"
    data.pop("@odata.nextLink")
    response = TextResponse(url=API_URL, body=json.dumps(data), encoding="utf-8")
    with freeze_time("2026-10-19"):
        items = list(_spider().parse(response))
    assert items[0]["status"] == CANCELLED


def test_planning_classification():
    assert _spider(ColgoStevensonPlanningSpider)._parse_classification() == COMMISSION


def test_html_response_raises():
    response = TextResponse(
        url=API_URL, body=b"<!DOCTYPE html><html></html>", encoding="utf-8"
    )
    with pytest.raises(StevensonApiError, match="did not return JSON"):
        list(_spider().parse(response))


def test_json_without_value_raises():
    response = TextResponse(url=API_URL, body=b'{"error": "x"}', encoding="utf-8")
    with pytest.raises(StevensonApiError, match="no 'value' list"):
        list(_spider().parse(response))


def test_zero_meetings_reports_error():
    spider = _spider()
    response = TextResponse(url=API_URL, body=b'{"value": []}', encoding="utf-8")
    assert list(spider.parse(response)) == []
    spider._on_spider_closed(spider, "finished")
    errors = _reported(spider)
    assert len(errors) == 1
    assert isinstance(errors[0], StevensonApiError)
    assert "0 meetings parsed" in str(errors[0])


def test_failed_request_reports_error():
    spider = _spider()
    with freeze_time("2026-10-19"):
        list(spider.parse(test_response))
    spider._request_failed(MagicMock(value=Exception("HTTP 404")))
    spider._on_spider_closed(spider, "finished")
    errors = _reported(spider)
    assert len(errors) == 1
    assert "1 failed CivicClerk request(s)" in str(errors[0])


def test_good_run_reports_nothing():
    spider = _spider()
    with freeze_time("2026-10-19"):
        list(spider.parse(test_response))
    spider._on_spider_closed(spider, "finished")
    assert _reported(spider) == []
