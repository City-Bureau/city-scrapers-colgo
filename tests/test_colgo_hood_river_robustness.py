"""
Request-flow tests for the Hood River spiders: an OMP video API failure must
never stop the EventON calendar from being fetched, and an unreadable or empty
calendar must fail loudly instead of finishing "successfully" with no items.

``drive`` is a small stand-in for the Scrapy engine: it feeds canned responses
or failures to each request's callback/errback, the way Scrapy does (an HTTP
error status reaches the errback as ``HttpError``; a request with no errback
just disappears).
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy import FormRequest, Request, signals
from scrapy.http import HtmlResponse, TextResponse
from scrapy.spidermiddlewares.httperror import HttpError
from scrapy.statscollectors import MemoryStatsCollector
from scrapy.utils.test import get_crawler
from scrapy_sentry_errors.extensions import Errors
from twisted.python.failure import Failure

from city_scrapers.extensions import MAX_ERROR_LINES, StatsLine
from city_scrapers.mixins.colgo_hood_river_city import HoodRiverCalendarError
from city_scrapers.spiders.colgo_hood_river_city import (
    ColgoHoodRiverEquityAdvisorySpider,
)

OMP = "ompnetwork.org"
EVENTON = "evo-ajax=eventon_get_events"

EVENT_HTML = (
    '<div id="event_1_0" class="eventon_list_event scheduled" data-event_id="1">'
    "</div>"
)
NO_EVENTS_HTML = (
    "<div class='eventon_list_event no_events'><p class='no_events' >No Events</p>"
    "</div>"
)


def json_response(request, payload, status=200):
    return TextResponse(
        url=request.url,
        status=status,
        body=json.dumps(payload).encode(),
        encoding="utf-8",
        request=request,
        headers={"Content-Type": "application/json"},
    )


def html_response(request, body="<html><body>app</body></html>", status=200):
    return HtmlResponse(
        url=request.url,
        status=status,
        body=body.encode(),
        encoding="utf-8",
        request=request,
    )


def video_page(start, total, count):
    return {
        "totalSize": total,
        "results": [
            {
                "video_url": f"https://www.youtube.com/watch?v=v{start + i}",
                "title": "Mayor's Equity Advisory Group",
                "date": 1700000000 + 86400 * (start + i),
                "video_id": f"v{start + i}",
            }
            for i in range(count)
        ],
    }


def http_error(request, status=503):
    return Failure(HttpError(html_response(request, status=status)))


def drive(spider, responder):
    """
    Run the spider's request graph to completion. ``responder(request)``
    returns a Response, a Failure, or None (request dropped by the engine
    before any callback or errback, e.g. a scheduler drop).

    Returns (calendar_requests, other outputs).
    """
    queue = list(spider.start_requests())
    calendar, outputs = [], []
    seen = 0
    while True:
        while queue:
            out = queue.pop(0)
            if not isinstance(out, Request):
                outputs.append(out)
                continue
            seen += 1
            assert seen < 500, "request loop"
            if EVENTON in out.url:
                calendar.append(out)
            result = responder(out)
            if result is None:
                continue
            if isinstance(result, Failure):
                if out.errback is None:
                    continue
                produced = out.errback(result)
            else:
                produced = (out.callback or spider.parse)(result)
            queue.extend(produced or [])
        # Mirror Scrapy's spider_idle signal.
        idle = getattr(spider, "_on_spider_idle_requests", None)
        more = list(idle()) if idle else []
        if not more:
            break
        queue.extend(more)
    return calendar, outputs


def eventon_ok(request):
    return json_response(request, {"status": "GOOD", "html": EVENT_HTML})


def make_responder(video_handler, calendar_handler=eventon_ok):
    def responder(request):
        if OMP in request.url:
            return video_handler(request)
        if EVENTON in request.url:
            return calendar_handler(request)
        raise AssertionError(f"unexpected request {request.url}")

    return responder


@pytest.fixture
def spider():
    return ColgoHoodRiverEquityAdvisorySpider()


def first_page_then(later_pages):
    def handler(request):
        if "start=0&" in request.url:
            return json_response(request, video_page(0, 250, 100))
        return later_pages(request)

    return handler


def test_calendar_fetched_when_video_page_2_fails(spider):
    """
    The suspected cause of the empty production runs: the first OMP page says
    totalSize=250, a later page fails, and the calendar was never requested.
    """

    def later(request):
        if "start=100&" in request.url:
            return http_error(request)
        return json_response(request, video_page(200, 250, 50))

    calendar, _ = drive(spider, make_responder(first_page_then(later)))
    assert calendar, "no EventON calendar request was made"
    assert all(isinstance(r, FormRequest) for r in calendar)


def test_calendar_fetched_when_all_later_video_pages_fail(spider):
    calendar, _ = drive(spider, make_responder(first_page_then(http_error)))
    assert calendar


def test_calendar_fetched_when_video_page_returns_short_results(spider):
    """Pages that come back with fewer results than totalSize promised."""

    def later(request):
        return json_response(request, {"totalSize": 250, "results": []})

    calendar, _ = drive(spider, make_responder(first_page_then(later)))
    assert calendar


def test_calendar_fetched_when_video_request_silently_dropped(spider):
    """A request that never reaches a callback or errback at all."""
    calendar, _ = drive(spider, make_responder(first_page_then(lambda r: None)))
    assert calendar


def test_calendar_fetched_when_first_video_page_fails(spider):
    calendar, _ = drive(spider, make_responder(http_error))
    assert calendar


def test_calendar_fetched_when_video_api_returns_html(spider):
    """What the OMP endpoint returns today: an HTML app shell, not JSON."""
    calendar, _ = drive(spider, make_responder(html_response))
    assert calendar


def test_calendar_requested_exactly_once_per_year(spider):
    def later(request):
        start = int(request.url.split("start=")[1].split("&")[0])
        return json_response(request, video_page(start, 250, min(100, 250 - start)))

    calendar, _ = drive(spider, make_responder(first_page_then(later)))
    years = [r.meta["year"] for r in calendar]
    assert len(years) == len(set(years)) > 0


def test_all_video_pages_used_when_omp_works(spider):
    def later(request):
        start = int(request.url.split("start=")[1].split("&")[0])
        return json_response(request, video_page(start, 250, min(100, 250 - start)))

    drive(spider, make_responder(first_page_then(later)))
    assert len(spider.video_cache) == 250


# --- Failing loudly -------------------------------------------------------


def calendar_request(spider, year=2026):
    return next(r for r in spider._fetch_calendar() if r.meta["year"] == year)


def test_unreadable_calendar_response_raises(spider):
    request = calendar_request(spider)
    with pytest.raises(HoodRiverCalendarError, match="not JSON"):
        list(spider.parse(html_response(request)))


def test_calendar_response_without_html_raises(spider):
    request = calendar_request(spider)
    response = json_response(request, {"status": "GOOD", "json": []})
    with pytest.raises(HoodRiverCalendarError, match="no calendar HTML"):
        list(spider.parse(response))


def test_year_with_no_events_is_not_an_error(spider):
    request = calendar_request(spider, year=2019)
    response = json_response(request, {"status": "GOOD", "html": NO_EVENTS_HTML})
    assert list(spider.parse(response)) == []
    assert spider._calendar_events == 0


def test_calendar_events_are_counted(spider):
    request = calendar_request(spider)
    list(spider.parse(json_response(request, {"html": EVENT_HTML * 3})))
    assert spider._calendar_events == 3


def test_failed_calendar_request_raises(spider):
    request = calendar_request(spider)
    failure = http_error(request)
    failure.request = request
    with pytest.raises(HoodRiverCalendarError, match="2026 failed"):
        request.errback(failure)


@pytest.fixture
def crawled():
    """A spider bound to a crawler, with the real Sentry extension attached."""
    crawler = get_crawler(ColgoHoodRiverEquityAdvisorySpider, {"SENTRY_DSN": ""})
    crawler.stats = MemoryStatsCollector(crawler)
    spider = ColgoHoodRiverEquityAdvisorySpider.from_crawler(crawler)
    sentry = Errors.from_crawler(crawler)
    captured = []
    sentry.client = SimpleNamespace(capture_exception=captured.append)
    stats_line = StatsLine.from_crawler(crawler)
    crawler.signals.send_catch_log(signals.spider_opened, spider=spider)
    # Signal receivers are weak references: keep the extensions alive.
    return crawler, spider, captured, (sentry, stats_line)


def test_run_with_zero_events_reports_to_sentry(crawled, capsys):
    crawler, spider, captured, _ = crawled
    list(spider._fetch_calendar())
    spider._calendar_responses = 8
    crawler.signals.send_catch_log(
        signals.spider_closed, spider=spider, reason="finished"
    )
    assert len(captured) == 1
    assert isinstance(captured[0], HoodRiverCalendarError)
    assert "0 EventON events" in str(captured[0])
    stats = crawler.stats.get_stats()
    assert stats["spider_exceptions/HoodRiverCalendarError"] == 1
    out = capsys.readouterr().out
    assert "[error] colgo_hood_river_equity_advisory HoodRiverCalendarError" in out
    assert "spider_errors=1" in out


def test_run_with_events_is_quiet(crawled):
    crawler, spider, captured, _ = crawled
    spider._calendar_events = 5
    crawler.signals.send_catch_log(
        signals.spider_closed, spider=spider, reason="finished"
    )
    assert captured == []


def test_interrupted_run_does_not_report_zero_events(crawled):
    crawler, spider, captured, _ = crawled
    crawler.signals.send_catch_log(
        signals.spider_closed, spider=spider, reason="shutdown"
    )
    assert captured == []


def test_idle_spider_fetches_calendar_once(crawled):
    _, spider, _, _ = crawled
    spider._video_pages_pending = 1  # a video request that never came back
    assert len(list(spider._on_spider_idle_requests())) > 0
    assert list(spider._on_spider_idle_requests()) == []


# --- Stats line -----------------------------------------------------------


def test_stats_line_format():
    stats = {
        "item_scraped_count": 42,
        "downloader/response_status_count/200": 10,
        "downloader/response_status_count/204": 1,
        "downloader/response_status_count/503": 2,
        "downloader/exception_count": 3,
        "spider_exceptions/HoodRiverCalendarError": 1,
        "spider_exceptions/KeyError": 2,
        "log_count/ERROR": 4,
    }
    assert StatsLine.format_line("spider_x", stats, "finished") == (
        "[stats] spider_x items=42 responses=13 2xx=11 non_2xx=2"
        " download_errors=3 spider_errors=3 log_errors=4 finish_reason=finished"
    )


def test_stats_line_empty_run():
    assert StatsLine.format_line("spider_x", {}, "finished") == (
        "[stats] spider_x items=0 responses=0 2xx=0 non_2xx=0"
        " download_errors=0 spider_errors=0 log_errors=0 finish_reason=finished"
    )


def test_error_lines_are_capped(capsys):
    ext = StatsLine(stats=None)
    spider = SimpleNamespace(name="spider_x")
    for i in range(MAX_ERROR_LINES + 3):
        ext.spider_error(Failure(ValueError(f"bad\n{i}")), spider)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == MAX_ERROR_LINES + 1
    assert lines[0] == "[error] spider_x ValueError: bad 0"
    assert lines[-1] == "[error] spider_x further errors not shown"


# --- Real Scrapy engine ---------------------------------------------------


def run_engine(scenario):
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "tests" / "hood_river_engine_run.py"), scenario],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_engine_fetches_calendar_despite_video_503():
    lines = run_engine("ok")
    stats = [line for line in lines if line.startswith("[stats]")]
    # 3 OMP pages (one 503) + one calendar request per year, no errors.
    years = datetime.now().year + 2 - 2019
    assert stats == [
        "[stats] colgo_hood_river_equity_advisory items=0"
        f" responses={3 + years} 2xx={2 + years} non_2xx=1 download_errors=0"
        " spider_errors=0 log_errors=0 finish_reason=finished"
    ]
    assert not [line for line in lines if line.startswith("SENTRY")]


def test_engine_reports_unreadable_calendar_to_sentry():
    lines = run_engine("bad_calendar")
    years = datetime.now().year + 2 - 2019
    sentry = [line for line in lines if line.startswith("SENTRY")]
    # One per unreadable year, plus the zero-events check at close.
    assert sentry == ["SENTRY HoodRiverCalendarError"] * (years + 1)
    assert any("is not JSON" in line for line in lines if line.startswith("[error]"))
    assert f"spider_errors={years + 1}" in lines[-1]


def test_engine_reports_zero_event_run_to_sentry():
    lines = run_engine("empty_calendar")
    assert [line for line in lines if line.startswith("SENTRY")] == [
        "SENTRY HoodRiverCalendarError"
    ]
    errors = [line for line in lines if line.startswith("[error]")]
    assert len(errors) == 1
    assert "run finished with 0 EventON events" in errors[0]
    assert "spider_errors=1" in lines[-1]
