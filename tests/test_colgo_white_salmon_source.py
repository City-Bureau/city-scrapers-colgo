"""
Tests for the White Salmon calendar host and failure reporting.

A finished run with 0 meetings is an error when a request failed, the calendar
listed no events, or followed meeting pages could not be parsed. When the
calendar lists events but none match the spider (the Civil Service Commission
has none), it is only a warning that does not reach Sentry.

colgo_white_salmon_legacy_calendar_2026_10.html is
https://legacywhitesalmonwa.teammunicode.com/calendar/month/2026-10
?field_microsite_tid=All&field_microsite_tid_1=27 fetched on 2026-10-19. The same
path on www.whitesalmonwa.gov now returns 404.

The spiders now request field_microsite_tid_1=All and filter by meeting_keyword.
The City Council filter (27) already lists the council, board and committee
meetings, so the fixture still covers those spiders, but it has no Planning or
Civil Service meetings, which only the "All" calendar adds.
"""

from os.path import dirname, join
from unittest.mock import MagicMock

from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from scrapy import signals
from scrapy.http import HtmlResponse

from city_scrapers.mixins.colgo_white_salmon import WhiteSalmonSourceError
from city_scrapers.spiders.colgo_white_salmon import (
    ColgoWhiteSalmonCityCouncilSpider,
    ColgoWhiteSalmonCivilServiceSpider,
)

LEGACY_HOST = "https://legacywhitesalmonwa.teammunicode.com"
CALENDAR_URL = (
    f"{LEGACY_HOST}/calendar/month/2026-10"
    "?field_microsite_tid=All&field_microsite_tid_1=All"
)
ALL_CALENDAR = "colgo_white_salmon_all_calendar_2026_10.html"


def _spider(spider_class=ColgoWhiteSalmonCityCouncilSpider):
    spider = spider_class()
    spider.crawler = MagicMock()
    return spider


def _calendar(filename, url=CALENDAR_URL):
    return file_response(join(dirname(__file__), "files", filename), url=url)


def _warned(spider):
    return [
        c.args[0]
        for c in spider.crawler.stats.inc_value.call_args_list
        if c.args[0].startswith("white_salmon/")
    ]


def _reported(spider):
    return [
        c.kwargs["failure"].value
        for c in spider.crawler.signals.send_catch_log.call_args_list
        if c.kwargs.get("signal") is signals.spider_error
    ]


def test_start_requests_use_legacy_host_with_errback():
    spider = _spider()
    with freeze_time("2026-10-19"):
        requests = list(spider.start_requests())
    assert len(requests) == 49
    assert all(r.url.startswith(f"{LEGACY_HOST}/calendar/month/") for r in requests)
    assert all(r.url.endswith("field_microsite_tid_1=All") for r in requests)
    assert all(r.errback is not None for r in requests)
    assert CALENDAR_URL in [r.url for r in requests]


def test_live_legacy_calendar_yields_council_meetings():
    spider = _spider()
    response = file_response(
        join(
            dirname(__file__),
            "files",
            "colgo_white_salmon_legacy_calendar_2026_10.html",
        ),
        url=CALENDAR_URL,
    )
    requests = list(spider.parse(response))
    assert [r.url for r in requests] == [
        f"{LEGACY_HOST}/citycouncil/page/city-council-meeting-146",
        f"{LEGACY_HOST}/citycouncil/page/city-council-workshop-1",
        f"{LEGACY_HOST}/citycouncil/page/city-council-meeting-157",
    ]
    assert all(r.errback is not None for r in requests)
    assert all("calendar_start" in r.meta for r in requests)


def test_all_requests_failing_reports_error():
    spider = _spider()
    for _ in range(3):
        spider._request_failed(MagicMock(value=Exception("HTTP 404")))
    spider._on_spider_closed(spider, "finished")
    errors = _reported(spider)
    assert len(errors) == 1
    assert isinstance(errors[0], WhiteSalmonSourceError)
    assert "3 failed request(s)" in str(errors[0])
    assert "0 meetings parsed" in str(errors[0])


def test_good_run_reports_nothing():
    spider = _spider()
    spider._meetings_parsed = 5
    spider._on_spider_closed(spider, "finished")
    assert _reported(spider) == []
    assert _warned(spider) == []


def test_no_matching_meetings_is_only_a_warning():
    """The calendar lists events, but none for this spider."""
    spider = _spider(ColgoWhiteSalmonCivilServiceSpider)
    assert list(spider.parse(_calendar(ALL_CALENDAR))) == []
    spider._on_spider_closed(spider, "finished")
    assert _reported(spider) == []
    assert _warned(spider) == ["white_salmon/no_matching_meetings"]


def test_calendar_without_events_reports_error():
    """Calendar pages loaded but listed nothing: the layout or URL changed."""
    spider = _spider()
    empty = HtmlResponse(url=CALENDAR_URL, body=b"<html><body></body></html>")
    assert list(spider.parse(empty)) == []
    spider._on_spider_closed(spider, "finished")
    errors = _reported(spider)
    assert len(errors) == 1
    assert "calendar pages listed no events" in str(errors[0])
    assert _warned(spider) == []


def test_unparsed_meeting_pages_report_error():
    """Meeting links were followed but no meeting could be parsed."""
    spider = _spider()
    assert len(list(spider.parse(_calendar(ALL_CALENDAR)))) == 3
    spider._on_spider_closed(spider, "finished")
    errors = _reported(spider)
    assert len(errors) == 1
    assert "0 meetings parsed from 3 meeting page(s)" in str(errors[0])
    assert _warned(spider) == []


def test_failed_request_with_no_matching_meetings_reports_error():
    """A failed page might have held this spider's meetings."""
    spider = _spider(ColgoWhiteSalmonCivilServiceSpider)
    list(spider.parse(_calendar(ALL_CALENDAR)))
    spider._request_failed(MagicMock(value=Exception("HTTP 500")))
    spider._on_spider_closed(spider, "finished")
    errors = _reported(spider)
    assert len(errors) == 1
    assert "1 failed request(s)" in str(errors[0])
    assert "0 meetings parsed" in str(errors[0])
    assert _warned(spider) == []
