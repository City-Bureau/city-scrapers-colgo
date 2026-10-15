"""
Run a Hood River spider through the real Scrapy engine with canned responses,
for tests/test_colgo_hood_river_robustness.py (run in a subprocess because the
Twisted reactor can only start once per process).

Usage: python tests/hood_river_engine_run.py <scenario>

Scenarios:
  ok, bad_calendar, empty_calendar: the OMP video API reports 250 sessions
      and its second page returns HTTP 503. The calendar has one upcoming
      meeting, is not JSON, or has no events in any year.
  malformed_video_with_diff: OMP returns {"totalSize": 1, "results": 5} and
      a DiffPipeline holds that upcoming meeting from a previous run.

Prints the StatsLine output, one "SENTRY <ExceptionClass>" line per exception
the scrapy_sentry_errors extension would have sent, and (with the diff
pipeline) one "ITEM <status> <_id>" line per published item.
"""

import json
import sys
from datetime import datetime, timedelta, timezone

import scrapy_sentry_errors.extensions as sentry_extensions
from city_scrapers_core.items import Meeting
from city_scrapers_core.pipelines.diff import DiffPipeline
from scrapy.crawler import CrawlerProcess
from scrapy.http import HtmlResponse, TextResponse

from city_scrapers.spiders.colgo_hood_river_city import (
    ColgoHoodRiverEquityAdvisorySpider,
)

SCENARIO = sys.argv[1]
TITLE = "Mayor's Equity Advisory Group"
START_TS = int((datetime.now() + timedelta(days=10)).timestamp())
START = datetime.fromtimestamp(START_TS, tz=timezone.utc).replace(tzinfo=None)
MEETING_HTML = (
    f'<div class="eventon_list_event" data-event_id="1"'
    f' data-time="{START_TS}-{START_TS + 3600}">'
    f'<span class="evcal_event_title">{TITLE}</span></div>'
)
NO_EVENTS_HTML = "<div class='eventon_list_event no_events'></div>"


def json_response(request, payload):
    body = json.dumps(payload).encode()
    return TextResponse(request.url, body=body, encoding="utf-8", request=request)


class CannedResponses:
    def process_request(self, request, spider):
        if "ompnetwork" in request.url:
            if SCENARIO == "malformed_video_with_diff":
                return json_response(request, {"totalSize": 1, "results": 5})
            if "start=100&" in request.url:
                return HtmlResponse(request.url, status=503, request=request)
            return json_response(request, {"totalSize": 250, "results": []})
        if SCENARIO == "bad_calendar":
            return HtmlResponse(request.url, body=b"<html></html>", request=request)
        has_meeting = SCENARIO != "empty_calendar" and (
            request.meta["year"] == START.year
        )
        html = MEETING_HTML if has_meeting else NO_EVENTS_HTML
        return json_response(request, {"html": html})


class PreviousRunDiffPipeline(DiffPipeline):
    """The previous run published the upcoming meeting as tentative."""

    def load_previous_results(self):
        meeting_id = ColgoHoodRiverEquityAdvisorySpider()._get_id(
            Meeting(title=TITLE, start=START)
        )
        return [
            {
                "_id": "prev-uid",
                "start": START.isoformat(),
                "status": "tentative",
                "extras": {"cityscrapers.org/id": meeting_id},
            }
        ]


class PrintItems:
    def process_item(self, item, spider):
        print(f"ITEM {item.get('status')} {item.get('_id')}", flush=True)
        return item


class FakeSentry:
    @staticmethod
    def capture_exception(exc):
        print(f"SENTRY {exc.__class__.__name__}", flush=True)


class RecordingErrors(sentry_extensions.Errors):
    def get_client(self, dsn, **options):
        return FakeSentry


if __name__ == "__main__":
    settings = {
        "LOG_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
        "RETRY_ENABLED": False,
        "SENTRY_DSN": "",
        "DOWNLOADER_MIDDLEWARES": {f"{__name__}.CannedResponses": 1},
        "EXTENSIONS": {
            f"{__name__}.RecordingErrors": 10,
            "city_scrapers.extensions.StatsLine": 900,
        },
    }
    if SCENARIO == "malformed_video_with_diff":
        settings["ITEM_PIPELINES"] = {
            f"{__name__}.PreviousRunDiffPipeline": 200,
            "city_scrapers_core.pipelines.OpenCivicDataPipeline": 400,
            f"{__name__}.PrintItems": 900,
        }
    process = CrawlerProcess(settings)
    process.crawl(ColgoHoodRiverEquityAdvisorySpider)
    process.start()
