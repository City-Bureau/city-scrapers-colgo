"""
Tests for the White Salmon calendar host and failure reporting.

colgo_white_salmon_legacy_calendar_2026_10.html is
https://legacywhitesalmonwa.teammunicode.com/calendar/month/2026-10?field_microsite_tid=All&field_microsite_tid_1=27
fetched on 2026-10-19. The same path on www.whitesalmonwa.gov now returns 404.
"""

from os.path import dirname, join
from unittest.mock import MagicMock

from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from scrapy import signals

from city_scrapers.mixins.white_salmon import WhiteSalmonSourceError
from city_scrapers.spiders.colgo_white_salmon import ColgoWhiteSalmonCityCouncilSpider

LEGACY_HOST = "https://legacywhitesalmonwa.teammunicode.com"
CALENDAR_URL = (
    f"{LEGACY_HOST}/calendar/month/2026-10"
    "?field_microsite_tid=All&field_microsite_tid_1=27"
)


def _spider():
    spider = ColgoWhiteSalmonCityCouncilSpider()
    spider.crawler = MagicMock()
    return spider


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
