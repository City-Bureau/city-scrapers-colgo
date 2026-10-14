from datetime import datetime
from os.path import dirname, join

from city_scrapers_core.constants import CANCELLED, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from parsel import Selector

from city_scrapers.spiders.colgo_hood_river_city import (
    ColgoHoodRiverEquityAdvisorySpider,
)

# Real EventON markup: the Sept 10, 2026 meeting (cancelled) and the
# Oct 21, 2026 meeting titled "Rescheduled from September 10, 2026".
test_response = file_response(
    join(dirname(__file__), "files", "colgo_hood_river_city_rescheduled.html"),
    url="https://cityofhoodriver.gov/?evo-ajax=eventon_get_events",
)
test_response.meta["skip_link_validation"] = True

with freeze_time("2026-08-01"):
    spider = ColgoHoodRiverEquityAdvisorySpider()
    spider.video_cache = {}
    items = list(spider._parse_events(Selector(text=test_response.text), test_response))
    by_start = {item["start"]: item for item in items}


def test_count():
    assert len(items) == 2


def test_original_date_is_cancelled():
    assert by_start[datetime(2026, 9, 10, 18, 0)]["status"] == CANCELLED


def test_rescheduled_meeting_is_not_cancelled():
    item = by_start[datetime(2026, 10, 21, 18, 0)]
    assert "Rescheduled from" in item["title"]
    assert item["status"] == TENTATIVE
