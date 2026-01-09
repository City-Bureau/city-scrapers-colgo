from datetime import datetime
from os.path import dirname, join

import pytest
from city_scrapers_core.constants import CITY_COUNCIL
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders import colgo_stevenson_city

ColgoStevensonCitySpider = colgo_stevenson_city.ColgoStevensonCitySpider

test_response = file_response(
    join(dirname(__file__), "files", "colgo_stevenson_city.html"),
    url="https://www.ci.stevenson.wa.us/meetings?field_microsite_tid_1=27",
)
spider = ColgoStevensonCitySpider()

freezer = freeze_time("2024-12-18")
freezer.start()

parsed_items = [item for item in spider.parse(test_response)]

freezer.stop()


def test_count():
    """Test that meetings are parsed"""
    assert len(parsed_items) > 0


def test_first_item_exact_values():
    """Test first meeting has expected values from fixture"""
    item = parsed_items[0]

    # Values from colgo_stevenson_city.html fixture
    # Title cleaned by spider (removes "December 18th, 2025")
    assert item["title"] == "Regular Council Meeting"
    assert item["start"] == datetime(2025, 12, 18, 18, 0)
    assert item["classification"] == CITY_COUNCIL
    assert item["location"] == {
        "name": "Stevenson City Hall Council Chambers",
        "address": "7121 East Loop Road, Stevenson, WA 98648",
    }
    assert (
        item["source"]
        == "https://www.ci.stevenson.wa.us/meetings?field_microsite_tid_1=27"
    )
    assert item["end"] is None
    assert item["all_day"] is False
    # Future meeting from perspective of frozen time (2024-12-18)
    assert item["status"] == "tentative"


def test_second_meeting_with_links():
    """Test second meeting which has complete documentation"""
    item = parsed_items[1]

    # Title cleaned by spider
    assert item["title"] == "Joint Council/Fire District 2 Meeting"
    assert item["start"] == datetime(2025, 12, 1, 17, 30)

    # This meeting has all link types
    link_titles = [link["title"] for link in item["links"]]
    assert "Agenda" in link_titles
    assert "Agenda Packet" in link_titles
    assert "Minutes" in link_titles
    assert "Video" in link_titles

    # Verify link structure
    for link in item["links"]:
        assert link["href"].startswith("http")
        assert "ci.stevenson.wa.us" in link["href"]


def test_all_titles_cleaned():
    """Test that date patterns are removed from all titles"""
    for item in parsed_items:
        # Titles should not start with dates after cleaning
        assert not item["title"][0].isdigit()
        # No leading/trailing whitespace
        assert item["title"].strip() == item["title"]
        # Should have meaningful content
        assert len(item["title"]) > 0


def test_title():
    """Test meeting title"""
    assert parsed_items[0]["title"] is not None
    assert len(parsed_items[0]["title"]) > 0


def test_description():
    """Test meeting description matches spider config"""
    expected_desc = (
        "With the exception of executive session meetings, Council meetings are open "
        "to the public, with opportunity for the public to speak. For all comments "
        "and testimony, speakers are asked to limit statements to about three minutes "
        "in order to allow as many people as possible the chance to address Council."
    )
    for item in parsed_items:
        assert isinstance(item["description"], str)
        assert item["description"] == expected_desc


def test_classification():
    """Test all meetings are City Council classification"""
    for item in parsed_items:
        assert item["classification"] == CITY_COUNCIL


def test_start():
    """Test meeting start datetime"""
    assert parsed_items[0]["start"] is not None
    assert isinstance(parsed_items[0]["start"], datetime)


def test_end():
    """Test meeting end datetime"""
    assert parsed_items[0]["end"] is None


def test_time_notes():
    """Test meeting time notes"""
    assert isinstance(parsed_items[0]["time_notes"], str)


def test_id():
    """Test meeting ID generation"""
    assert parsed_items[0]["id"] is not None
    assert isinstance(parsed_items[0]["id"], str)


def test_status_values():
    """Test meeting status values are valid"""
    valid_statuses = ["passed", "tentative", "cancelled"]
    for item in parsed_items:
        assert item["status"] in valid_statuses


def test_cancelled_meeting():
    """Test that cancelled meetings are properly identified"""
    # Last meeting in fixture is cancelled
    cancelled_meetings = [
        item for item in parsed_items if "cancelled" in item["title"].lower()
    ]

    assert len(cancelled_meetings) == 1
    cancelled = cancelled_meetings[0]

    assert "Q2 Joint City Council/Fire District II Meeting" in cancelled["title"]
    assert cancelled["status"] == "cancelled"
    assert cancelled["start"] == datetime(2025, 6, 11, 17, 30)


def test_rescheduled_status():
    """Test that meetings with 'Rescheduled' in title get correct status"""
    rescheduled_meetings = [
        item for item in parsed_items if "rescheduled" in item["title"].lower()
    ]

    for item in rescheduled_meetings:
        # If meeting date is in the past (before frozen time 2024-12-18)
        if item["start"] < datetime(2024, 12, 18):
            assert item["status"] == "passed"


def test_location():
    """Test meeting location is consistent"""
    expected_location = {
        "name": "Stevenson City Hall Council Chambers",
        "address": "7121 East Loop Road, Stevenson, WA 98648",
    }
    for item in parsed_items:
        assert item["location"] == expected_location


def test_source():
    """Test meeting source URL"""
    expected_source = "https://www.ci.stevenson.wa.us/meetings?field_microsite_tid_1=27"
    for item in parsed_items:
        assert item["source"] == expected_source


def test_links_structure():
    """Test meeting links have valid structure and titles"""
    valid_link_titles = ["Agenda", "Agenda Packet", "Minutes", "Video"]

    for item in parsed_items:
        links = item["links"]
        assert isinstance(links, list)

        for link in links:
            assert "href" in link
            assert "title" in link
            assert link["title"] in valid_link_titles
            # All links should be absolute URLs
            assert link["href"].startswith("http")


@pytest.mark.parametrize("item", parsed_items)
def test_all_day(item):
    """Test that meetings are not all day"""
    assert item["all_day"] is False
