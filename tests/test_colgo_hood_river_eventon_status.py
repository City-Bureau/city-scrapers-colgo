import json
from datetime import datetime
from os.path import dirname, join

from city_scrapers_core.constants import CANCELLED, PASSED, TENTATIVE
from freezegun import freeze_time
from scrapy.http import Request, TextResponse

from city_scrapers.spiders.colgo_hood_river_city import (
    ColgoHoodRiverUrbanRenewalAdvisorySpider,
)

URL = "https://cityofhoodriver.gov/?evo-ajax=eventon_get_events"

# Real EventON response (trimmed to the Urban Renewal Advisory Committee),
# including the "json" entries that carry each event's event_pmv._status.
with open(join(dirname(__file__), "files", "colgo_hood_river_city_eventon.json")) as f:
    fixture = json.load(f)


def make_response(data):
    request = Request(url=URL, meta={"skip_link_validation": True})
    return TextResponse(
        url=URL, body=json.dumps(data), encoding="utf-8", request=request
    )


def parse(data):
    with freeze_time("2026-10-15"):
        spider = ColgoHoodRiverUrbanRenewalAdvisorySpider()
        spider.video_cache = {}
        return {item["start"]: item for item in spider.parse(make_response(data))}


def test_status_follows_eventon_status():
    items = parse(fixture)
    assert len(items) == len(fixture["json"])
    cancelled = {
        k
        for k, e in fixture["json"].items()
        if e["event_pmv"]["_status"] == ["cancelled"]
    }
    assert sum(item["status"] == CANCELLED for item in items.values()) == len(cancelled)


def test_scheduled_future_meeting_is_tentative():
    items = parse(fixture)
    assert items[datetime(2026, 11, 19, 17, 30)]["status"] == TENTATIVE


def test_cancelled_meeting_is_cancelled():
    items = parse(fixture)
    assert items[datetime(2026, 10, 15, 17, 30)]["status"] == CANCELLED


def test_scheduled_past_meeting_is_passed():
    items = parse(fixture)
    past = [
        item
        for start, item in items.items()
        if start < datetime(2026, 10, 15) and item["status"] != CANCELLED
    ]
    assert past and all(item["status"] == PASSED for item in past)


def test_cancel_text_in_markup_does_not_override_scheduled_status():
    # Oct 14, 2026: one run published every upcoming Hood River meeting as
    # cancelled while the calendar's structured status said "scheduled".
    # Whatever the markup says, the structured status must win.
    data = json.loads(json.dumps(fixture))
    data["html"] = (
        data["html"]
        .replace('class="eventon_list_event', 'class="eventon_list_event cancelled')
        .replace("</span>", " Cancelled</span>", 1)
    )
    items = parse(data)
    assert items[datetime(2026, 11, 19, 17, 30)]["status"] == TENTATIVE
    assert items[datetime(2026, 10, 15, 17, 30)]["status"] == CANCELLED


def test_falls_back_to_markup_without_json():
    data = {"status": fixture["status"], "html": fixture["html"]}
    items = parse(data)
    assert items[datetime(2026, 10, 15, 17, 30)]["status"] == CANCELLED
    assert items[datetime(2026, 11, 19, 17, 30)]["status"] == TENTATIVE
