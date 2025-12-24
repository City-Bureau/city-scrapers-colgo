from datetime import datetime
from os.path import dirname, join

import pytest
from city_scrapers_core.constants import CITY_COUNCIL, COMMISSION, NOT_CLASSIFIED
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


def test_title():
    """Test meeting title"""
    assert parsed_items[0]["title"] is not None
    assert len(parsed_items[0]["title"]) > 0


def test_description():
    """Test meeting description"""
    # assert parsed_items[0]["description"] == "" or isinstance(
    #     parsed_items[0]["description"], str
    assert isinstance(parsed_items[0]["description"], str)


def test_classification_is_city_council():
    assert parsed_items[0]["classification"] == CITY_COUNCIL


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


def test_status():
    """Test meeting status"""
    assert parsed_items[0]["status"] in ["passed", "tentative", "cancelled", "upcoming"]


def test_location():
    """Test meeting location"""
    assert parsed_items[0]["location"]["name"] == "Stevenson City Hall Council Chambers"
    assert (
        parsed_items[0]["location"]["address"]
        == "7121 East Loop Road, Stevenson, WA 98648"
    )


def test_source():
    """Test meeting source URL"""
    assert (
        parsed_items[0]["source"]
        == "https://www.ci.stevenson.wa.us/meetings?field_microsite_tid_1=27"
    )


def test_links():
    """Test meeting links"""
    links = parsed_items[0]["links"]
    assert isinstance(links, list)

    for link in links:
        assert "href" in link
        assert "title" in link


def test_classification():
    """Test meeting classification"""
    classification = parsed_items[0]["classification"]
    assert classification is not None
    # Should be one of the valid constants
    assert classification in [CITY_COUNCIL, COMMISSION, NOT_CLASSIFIED]


@pytest.mark.parametrize("item", parsed_items)
def test_all_day(item):
    """Test that meetings are not all day"""
    assert item["all_day"] is False
