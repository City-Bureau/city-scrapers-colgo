"""
Run a Hood River spider through the real Scrapy engine with canned responses,
for tests/test_colgo_hood_river_robustness.py (run in a subprocess because the
Twisted reactor can only start once per process).

Usage: python tests/hood_river_engine_run.py <ok|bad_calendar|empty_calendar>

The OMP video API reports 250 sessions and its second page returns HTTP 503.
Prints the StatsLine output, then one "SENTRY <ExceptionClass>" line per
exception the scrapy_sentry_errors extension would have sent.
"""

import json
import sys

import scrapy_sentry_errors.extensions as sentry_extensions
from scrapy.crawler import CrawlerProcess
from scrapy.http import HtmlResponse, TextResponse

from city_scrapers.spiders.colgo_hood_river_city import (
    ColgoHoodRiverEquityAdvisorySpider,
)

SCENARIO = sys.argv[1]
CALENDAR_HTML = {
    "ok": '<div class="eventon_list_event" data-event_id="1"></div>',
    "empty_calendar": "<div class='eventon_list_event no_events'></div>",
}


def json_response(request, payload):
    body = json.dumps(payload).encode()
    return TextResponse(request.url, body=body, encoding="utf-8", request=request)


class CannedResponses:
    def process_request(self, request, spider):
        if "ompnetwork" in request.url:
            if "start=100&" in request.url:
                return HtmlResponse(request.url, status=503, request=request)
            return json_response(request, {"totalSize": 250, "results": []})
        if SCENARIO == "bad_calendar":
            return HtmlResponse(request.url, body=b"<html></html>", request=request)
        return json_response(request, {"html": CALENDAR_HTML[SCENARIO]})


class FakeSentry:
    @staticmethod
    def capture_exception(exc):
        print(f"SENTRY {exc.__class__.__name__}", flush=True)


class RecordingErrors(sentry_extensions.Errors):
    def get_client(self, dsn, **options):
        return FakeSentry


if __name__ == "__main__":
    process = CrawlerProcess(
        {
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
    )
    process.crawl(ColgoHoodRiverEquityAdvisorySpider)
    process.start()
