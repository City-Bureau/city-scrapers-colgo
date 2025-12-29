from datetime import datetime
from os.path import dirname, join

from city_scrapers_core.constants import CANCELLED, COMMITTEE, PASSED, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from parsel import Selector

from city_scrapers.spiders.colgo_hood_river_city import (
    ColgoHoodRiverCityBudgetCommitteeSpider,
    ColgoHoodRiverCityCouncilSpider,
    ColgoHoodRiverPlanningCommissionSpider,
    ColgoHoodRiverUrbanRenewalAdvisorySpider,
)

test_response = file_response(
    join(dirname(__file__), "files", "colgo_hood_river_city.html"),
    url="https://cityofhoodriver.gov/?evo-ajax=eventon_get_events",
)

freezer = freeze_time("2025-12-15")
freezer.start()

# Parse HTML to get selector

selector = Selector(text=test_response.text)

# Test Budget Committee Spider
budget_spider = ColgoHoodRiverCityBudgetCommitteeSpider()
budget_spider.video_cache = {
    "2022-05-04_city_budget_committee": {
        "url": "http://www.youtube.com/watch?v=BUDGET_VIDEO_123",
        "title": "City Budget Committee Meeting",
        "date": "2022-05-04",
        "video_id": "BUDGET_VIDEO_123",
    }
}
budget_items = [item for item in budget_spider._parse_events(selector, test_response)]

# Test City Council Spider
council_spider = ColgoHoodRiverCityCouncilSpider()
council_spider.video_cache = {
    "2025-10-14_hood_river_city_council": {
        "url": "http://www.youtube.com/watch?v=COUNCIL_VIDEO_456",
        "title": "Hood River City Council Meeting",
        "date": "2025-10-14",
        "video_id": "COUNCIL_VIDEO_456",
    }
}
council_items = [item for item in council_spider._parse_events(selector, test_response)]

# Test Urban Renewal Advisory Spider
urban_spider = ColgoHoodRiverUrbanRenewalAdvisorySpider()
urban_spider.video_cache = {}  # Empty cache for testing
urban_items = [item for item in urban_spider._parse_events(selector, test_response)]

# Test Planning Commission Spider
planning_spider = ColgoHoodRiverPlanningCommissionSpider()
planning_spider.video_cache = {}  # Empty cache for testing
planning_items = [
    item for item in planning_spider._parse_events(selector, test_response)
]

freezer.stop()


def test_spider_configuration():
    """Test that spiders are properly configured with correct attributes"""
    assert budget_spider.name == "colgo_hood_river_city_budget_committee"
    assert budget_spider.agency == "Hood River City Budget Committee"
    assert budget_spider.title_filter == "Budget"

    assert council_spider.name == "colgo_hood_river_city_council"
    assert council_spider.agency == "Hood River City Council"
    assert council_spider.title_filter == "City Council"

    assert urban_spider.agency == "Hood River Urban Renewal Advisory Committee"
    assert urban_spider.title_filter == "Urban Renewal Advisory"


def test_title_filter():
    """Test that title filters correctly include/exclude meetings"""
    # Budget spider should only get budget meetings
    assert len(budget_items) == 1
    assert "budget" in budget_items[0]["title"].lower()

    # Council spider should get council meetings (excluding budget meetings)
    assert len(council_items) == 1
    for item in council_items:
        assert "council" in item["title"].lower()
        assert "budget" not in item["title"].lower()

    # Urban renewal spider should get urban renewal meetings
    assert len(urban_items) == 1  # Only Advisory (Agency doesn't match filter)
    assert "urban renewal" in urban_items[0]["title"].lower()

    # Planning spider should get planning commission meetings
    assert len(planning_items) == 1
    assert "planning" in planning_items[0]["title"].lower()


def test_title_removes_dates():
    """Test that dates are removed from meeting titles"""
    # Real data doesn't have dates in titles, verify they're clean
    assert budget_items[0]["title"] == "City Budget Committee Meeting"
    assert council_items[0]["title"] == "Hood River City Council Meeting"


def test_title_removes_meeting_numbers():
    """Test that meeting numbers (No. 1, No. 2, etc.) are removed from titles"""
    # The budget meeting has "No. 1" which should be stripped
    assert "No." not in budget_items[0]["title"]
    assert "No. 1" not in budget_items[0]["title"]


def test_start_time():
    """Test that start times are correctly parsed"""
    assert budget_items[0]["start"] == datetime(2022, 5, 4, 22, 0)
    assert council_items[0]["start"] == datetime(2025, 10, 14, 22, 0)
    assert urban_items[0]["start"] == datetime(2026, 5, 21, 21, 30)
    assert planning_items[0]["start"] == datetime(2024, 5, 20, 21, 30)


def test_end_time():
    """Test that end times are correctly parsed"""
    assert budget_items[0]["end"] == datetime(2022, 5, 5, 3, 50)
    assert council_items[0]["end"] == datetime(2025, 10, 15, 3, 50)
    assert urban_items[0]["end"] == datetime(2026, 5, 22, 3, 50)


def test_all_day():
    """Test that all_day is correctly set to False"""
    assert budget_items[0]["all_day"] is False
    assert council_items[0]["all_day"] is False


def test_location():
    """Test that location is correctly parsed"""
    expected_location = {
        "name": "Hood River City Hall",
        "address": "211 2nd Street, Hood River, OR 97031",
    }
    assert budget_items[0]["location"] == expected_location
    assert council_items[0]["location"] == expected_location


def test_links():
    """Test that links are correctly parsed"""
    # Budget meeting should have agenda, packet, minutes, and video
    budget_links = budget_items[0]["links"]
    assert len(budget_links) == 4

    # Check for agenda
    agenda_link = next(
        (link for link in budget_links if "agenda" in link["title"].lower()), None
    )
    assert agenda_link is not None
    assert "Budget-Agenda-05-04-2022" in agenda_link["href"]

    # Check for packet
    packet_link = next(
        (link for link in budget_links if "packet" in link["title"].lower()), None
    )
    assert packet_link is not None
    assert "Packet-City-Budget-Meeting-May-4-2022" in packet_link["href"]

    # Check for minutes
    minutes_link = next(
        (link for link in budget_links if "minutes" in link["title"].lower()), None
    )
    assert minutes_link is not None
    assert "City-Budget-Meeting-Minutes-05-04-2022.pdf" in minutes_link["href"]

    # Check for video
    video_link = next(
        (link for link in budget_links if "video" in link["title"].lower()), None
    )
    assert video_link is not None
    assert "BUDGET_VIDEO_123" in video_link["href"]

    # Council meeting should have agenda, packet, minutes, and video
    # (presentations link filtered out if it returns 404)
    council_links = council_items[0]["links"]
    assert len(council_links) >= 4

    # Verify we have the main document types
    link_types = [link["title"].lower() for link in council_links]
    assert any("agenda" in t for t in link_types)
    assert any("packet" in t for t in link_types)
    assert any("minutes" in t for t in link_types)
    assert any("video" in t for t in link_types)

    # Verify council video link
    council_video = next(
        (link for link in council_links if "video" in link["title"].lower()), None
    )
    assert council_video is not None
    assert "COUNCIL_VIDEO_456" in council_video["href"]


def test_status_passed():
    """Test that passed status is correctly detected"""
    # Budget meeting and council meeting should be passed (in the past)
    assert budget_items[0]["status"] == PASSED
    assert council_items[0]["status"] == PASSED


def test_status_cancelled():
    """Test that cancelled status is correctly detected"""
    # Planning commission meeting is cancelled
    assert planning_items[0]["status"] == CANCELLED


def test_status_tentative():
    """Test that tentative status is correctly detected for future meetings"""
    # Urban Renewal Advisory meeting is in 2026 (future)
    assert urban_items[0]["status"] == TENTATIVE


def test_classification():
    """Test that classification is correctly assigned"""
    assert budget_items[0]["classification"] == COMMITTEE
    # City Council meetings get "City Council" classification
    assert council_items[0]["classification"] == "City Council"


def test_source():
    """Test that source URL is correctly set"""
    expected_source = "https://cityofhoodriver.gov/administration/meetings/"
    assert budget_items[0]["source"] == expected_source
    assert council_items[0]["source"] == expected_source


def test_id_generation():
    """Test that unique IDs are generated for each meeting"""
    # Each meeting should have a unique ID
    assert budget_items[0]["id"].startswith("colgo_hood_river_city_budget_committee/")
    assert council_items[0]["id"].startswith("colgo_hood_river_city_council/")

    # ID should contain timestamp
    assert "202205042200" in budget_items[0]["id"]
    assert "202510142200" in council_items[0]["id"]


#     assert parsed_items[0]["description"] == "EXPECTED DESCRIPTION"


# def test_start():
#     assert parsed_items[0]["start"] == datetime(2019, 1, 1, 0, 0)


# def test_end():
#     assert parsed_items[0]["end"] == datetime(2019, 1, 1, 0, 0)


# def test_time_notes():
#     assert parsed_items[0]["time_notes"] == "EXPECTED TIME NOTES"


# def test_id():
#     assert parsed_items[0]["id"] == "EXPECTED ID"


# def test_status():
#     assert parsed_items[0]["status"] == "EXPECTED STATUS"


# def test_location():
#     assert parsed_items[0]["location"] == {
#         "name": "EXPECTED NAME",
#         "address": "EXPECTED ADDRESS"
#     }


# def test_source():
#     assert parsed_items[0]["source"] == "EXPECTED URL"


# def test_links():
#     assert parsed_items[0]["links"] == [{
#       "href": "EXPECTED HREF",
#       "title": "EXPECTED TITLE"
#     }]


# def test_classification():
#     assert parsed_items[0]["classification"] == NOT_CLASSIFIED


# @pytest.mark.parametrize("item", parsed_items)
# def test_all_day(item):
#     assert item["all_day"] is False
