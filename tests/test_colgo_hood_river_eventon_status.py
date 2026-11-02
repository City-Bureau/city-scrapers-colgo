import json
from datetime import datetime, timezone
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


def parse(data, now="2026-10-15"):
    with freeze_time(now):
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
    # Cancellation words in markup outside the title must not override it.
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


def event_at(data, start):
    """The "json" entry of the event starting at start."""
    return next(
        e
        for e in data["json"].values()
        # EventON stores local wall-clock time as if it were UTC.
        if datetime.fromtimestamp(int(e["event_start_unix"]), timezone.utc).replace(
            tzinfo=None
        )
        == start
    )


def replace_in_event(data, start, old, new):
    """Replace the first old with new in the HTML of the event starting at start."""
    data = json.loads(json.dumps(data))
    target = event_at(data, start)
    marker = f'id="event_{target["event_id"]}_{target["ri"]}"'
    head, tail = data["html"].split(marker, 1)
    data["html"] = head + marker + tail.replace(old, new, 1)
    return data


def with_title_suffix(data, start, suffix):
    """Append a suffix to the calendar title of the event starting at start."""
    title = "Urban Renewal Advisory Committee Meeting</span>"
    return replace_in_event(data, start, title, title[:-7] + suffix + "</span>")


def test_cancelled_title_overrides_scheduled_status():
    # 63 Hood River events are cancelled only in their title ("... - Cancelled")
    # while EventON's status stays "scheduled".
    nov_19 = datetime(2026, 11, 19, 17, 30)
    data = with_title_suffix(fixture, nov_19, " – November 19, 2026 – Cancelled")
    items = parse(data)
    assert items[nov_19]["status"] == CANCELLED
    assert items[datetime(2026, 12, 17, 17, 30)]["status"] == TENTATIVE


def test_postponed_title_is_cancelled():
    nov_19 = datetime(2026, 11, 19, 17, 30)
    data = with_title_suffix(fixture, nov_19, " – Postponed")
    assert parse(data)[nov_19]["status"] == CANCELLED


def test_postponed_status_is_cancelled():
    nov_19 = datetime(2026, 11, 19, 17, 30)
    data = json.loads(json.dumps(fixture))
    event_at(data, nov_19)["event_pmv"]["_status"] = ["postponed"]
    assert parse(data)[nov_19]["status"] == CANCELLED


def test_cancel_note_only_on_cancelled_meetings():
    nov_19 = datetime(2026, 11, 19, 17, 30)
    youtube = "viewed live on YouTube."
    data = replace_in_event(
        fixture, nov_19, youtube, f"{youtube} The October 15 meeting is cancelled."
    )
    items = parse(data)
    assert items[nov_19]["status"] == TENTATIVE
    assert items[nov_19]["time_notes"].startswith("Please refer to the meeting")
    oct_15 = items[datetime(2026, 10, 15, 17, 30)]
    assert oct_15["time_notes"] == "Meeting has been cancelled"


def test_rescheduled_title_does_not_cancel():
    nov_19 = datetime(2026, 11, 19, 17, 30)
    data = with_title_suffix(fixture, nov_19, " – Rescheduled from November 5")
    assert parse(data)[nov_19]["status"] == TENTATIVE


def test_passed_uses_local_time_not_utc():
    # 00:30 UTC on Nov 20 is 16:30 on Nov 19 in Hood River: the 17:30 meeting
    # hasn't started yet, though a UTC clock would call it passed.
    items = parse(fixture, now="2026-11-20 00:30:00")
    assert items[datetime(2026, 11, 19, 17, 30)]["status"] == TENTATIVE
    items = parse(fixture, now="2026-11-20 02:00:00")
    assert items[datetime(2026, 11, 19, 17, 30)]["status"] == PASSED
