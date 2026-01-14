from datetime import datetime
from os.path import dirname, join

import pytest
from city_scrapers_core.constants import COMMITTEE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.colgo_columbia_commission import (
    ColgoColumbiaCommissionSpider,
)

upcoming_meetings = file_response(
    join(dirname(__file__), "files", "colgo_columbia_commission_upcoming.html"),
    url="https://www.gorgecommission.org/about-crgc/commission-meetings",
)

spider = ColgoColumbiaCommissionSpider()

freezer = freeze_time("2026-01-06")
freezer.start()

parsed_items = [item for item in spider.parse(upcoming_meetings)]

freezer.stop()


def test_title():
    assert parsed_items[0]["title"] == "January 2026 Monthly CRGC Meeting"


def test_description():
    assert parsed_items[0]["description"] == ""


def test_start():
    assert parsed_items[0]["start"] == datetime(2026, 1, 13, 8, 30)


def test_end():
    assert parsed_items[0]["end"] == datetime(2026, 1, 13, 12, 0)


def test_time_notes():
    assert (
        parsed_items[0]["time_notes"]
        == "For meeting time and registration details, please check the Agenda."
    )


def test_id():
    assert (
        parsed_items[0]["id"]
        == "colgo_columbia_commission/202601130830/x/january_2026_monthly_crgc_meeting"
    )


def test_status():
    assert parsed_items[0]["status"] == "tentative"


def test_location():
    assert parsed_items[0]["location"] == {
        "name": "via Zoom webinar",
        "address": "White Salmon, WA",
    }


def test_source():
    assert (
        parsed_items[0]["source"]
        == "https://www.gorgecommission.org/about-crgc/commission-meetings"
    )


def test_links():
    assert parsed_items[0]["links"] == []


def test_classification():
    assert parsed_items[0]["classification"] == COMMITTEE


@pytest.mark.parametrize("item", parsed_items)
def test_all_day(item):
    assert item["all_day"] is False
