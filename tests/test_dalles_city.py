"""
Tests for The Dalles spiders using OmpNetwork API.
"""

from datetime import datetime
from os.path import dirname, join

import pytest
from city_scrapers_core.constants import BOARD, CITY_COUNCIL, COMMISSION, PASSED
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders import dalles_city

ColgoDallesCityCouncilSpider = dalles_city.ColgoDallesCityCouncilSpider

test_response = file_response(
    join(dirname(__file__), "files", "dalles_city_council.json"),
    url="https://thedalles-oregon.ompnetwork.org/api-cache/site/312/sessions?category[]=214&start=0&limit=100",  # noqa
)


@pytest.fixture
def parsed_items():
    spider = ColgoDallesCityCouncilSpider()
    with freeze_time("2025-12-16"):
        return [item for item in spider.parse(test_response)]


def test_count(parsed_items):
    assert len(parsed_items) == 101


def test_title(parsed_items):
    assert parsed_items[0]["title"] == "City Council Meeting January 12, 2026"


def test_description(parsed_items):
    assert parsed_items[0]["description"] == ""


def test_classification(parsed_items):
    assert parsed_items[0]["classification"] == CITY_COUNCIL


def test_start(parsed_items):
    assert parsed_items[0]["start"] == datetime(2026, 1, 12, 17, 30, 0)


def test_end(parsed_items):
    assert parsed_items[0]["end"] is None


def test_time_notes(parsed_items):
    assert parsed_items[0]["time_notes"] == ""


def test_id(parsed_items):
    expected_id = (
        "colgo_dalles_city_council/202601121730/x/"
        "city_council_meeting_january_12_2026"
    )
    assert parsed_items[0]["id"] == expected_id


def test_status(parsed_items):
    assert parsed_items[0]["status"] == "tentative"
    assert parsed_items[1]["status"] == PASSED


def test_location(parsed_items):
    assert parsed_items[0]["location"] == {
        "name": "The Dalles City Hall",
        "address": "313 Court St, The Dalles, OR 97058",
    }


def test_source(parsed_items):
    assert (
        parsed_items[0]["source"]
        == "https://thedalles-oregon.ompnetwork.org/sessions/332148/city-council-meeting-january-12-2026-live-stream"  # noqa
    )


def test_links(parsed_items):
    assert len(parsed_items[0]["links"]) == 2
    assert parsed_items[0]["links"][0]["title"] == "Agenda"
    assert parsed_items[0]["links"][1]["title"] == "Packet"
    assert (
        "cc_2026-01-12_city_council_agenda.pdf" in parsed_items[0]["links"][0]["href"]
    )


def test_all_day(parsed_items):
    assert parsed_items[0]["all_day"] is False


# Test other spiders to ensure they are configured correctly


def test_informational_spider():
    """Test Informational/Town Hall spider configuration."""
    spider = dalles_city.ColgoDallesInformationalSpider()
    assert spider.name == "colgo_dalles_informational"
    assert spider.agency == "The Dalles Informational or Town Hall Meetings"
    assert spider.category_id == "215"


def test_planning_commission_spider():
    """Test Planning Commission spider configuration."""
    spider = dalles_city.ColgoDallesPlanningCommissionSpider()
    assert spider.name == "colgo_dalles_planning_commission"
    assert spider.agency == "The Dalles Planning Commission"
    assert spider.category_id == "216"
    assert spider._classification == COMMISSION


def test_urban_renewal_spider():
    """Test Urban Renewal Agency spider configuration."""
    spider = dalles_city.ColgoDallesUrbanRenewalSpider()
    assert spider.name == "colgo_dalles_urban_renewal"
    assert spider.agency == "The Dalles Urban Renewal Agency"
    assert spider.category_id == "218"
    assert spider._classification == BOARD


def test_historic_landmarks_spider():
    """Test Historic Landmarks Commission spider configuration."""
    spider = dalles_city.ColgoDallesHistoricLandmarksSpider()
    assert spider.name == "colgo_dalles_historic_landmarks"
    assert spider.agency == "The Dalles Historic Landmarks Commission"
    assert spider.category_id == "217"
    assert spider._classification == COMMISSION
