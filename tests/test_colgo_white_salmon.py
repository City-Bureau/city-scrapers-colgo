"""
Tests for all White Salmon spiders (city_scrapers/spiders/colgo_white_salmon.py).
The calendar host, start requests and failure reporting are tested in
test_colgo_white_salmon_source.py.

Every spider requests the same unfiltered calendar
(field_microsite_tid_1=All) and keeps only the meeting links that contain its
meeting_keyword.

Calendar fixtures:
    colgo_white_salmon_all_calendar_2026_10.html
        https://legacywhitesalmonwa.teammunicode.com/calendar/month/2026-10
        ?field_microsite_tid=All&field_microsite_tid_1=All fetched on
        2026-11-09. This is the calendar the spiders read.
    colgo_white_salmon_legacy_calendar_2026_10.html
        The same month with the City Council filter (field_microsite_tid_1=27),
        fetched on 2026-10-19.
    colgo_white_salmon_city_council_calendar.html
        2025-01 with the City Council filter, from www.whitesalmonwa.gov.
    colgo_white_salmon_planning_calendar.html
        Hand-written page with one Planning Commission meeting.

The City Council filter also lists the Tree Board, CityLAB Board and committee
meetings, but not Planning Commission meetings.

The calendar has no Civil Service Commission meetings: a check on 2026-11-09 of
every month the spider requests (2023-11 through 2027-11) found 378 meetings,
none of them Civil Service. The commission only appears as a filter option.
None of the fixtures has a Lodging Tax or Wildfire and Emergency Preparedness
meeting that matches those spiders' keywords.
"""

from datetime import datetime
from os.path import dirname, join

import pytest
from city_scrapers_core.constants import (
    BOARD,
    CITY_COUNCIL,
    COMMISSION,
    COMMITTEE,
    PASSED,
)
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders import colgo_white_salmon
from city_scrapers.spiders.colgo_white_salmon import spider_configs

LEGACY_HOST = "https://legacywhitesalmonwa.teammunicode.com"

LOCATION = {
    "name": "City's Council Chambers",
    "address": "119 NE Church Ave, White Salmon, WA 98672",
}


def _path(filename):
    return join(dirname(__file__), "files", filename)


def _calendar_url(month):
    return (
        f"{LEGACY_HOST}/calendar/month/{month}"
        "?field_microsite_tid=All&field_microsite_tid_1=All"
    )


def _spider(class_name):
    return getattr(colgo_white_salmon, class_name)()


CALENDARS = {
    "all_2026_10": ("colgo_white_salmon_all_calendar_2026_10.html", "2026-10"),
    "council_2026_10": ("colgo_white_salmon_legacy_calendar_2026_10.html", "2026-10"),
    "council_2025_01": ("colgo_white_salmon_city_council_calendar.html", "2025-01"),
    "planning_2025_01": ("colgo_white_salmon_planning_calendar.html", "2025-01"),
}


def _calendar(key):
    filename, month = CALENDARS[key]
    return file_response(_path(filename), url=_calendar_url(month))


def _followed(class_name, calendar_key):
    return [r.url for r in _spider(class_name).parse(_calendar(calendar_key))]


# ============================================================================
# Spider configuration
# ============================================================================
CLASSIFICATIONS = {
    "ColgoWhiteSalmonCityCouncilSpider": CITY_COUNCIL,
    "ColgoWhiteSalmonCivilServiceSpider": COMMISSION,
    "ColgoWhiteSalmonPlanningSpider": COMMISSION,
    "ColgoWhiteSalmonPersonnelFinanceSpider": COMMITTEE,
    "ColgoWhiteSalmonCityOperationsSpider": COMMITTEE,
    "ColgoWhiteSalmonCommunityDevelopmentSpider": COMMITTEE,
    "ColgoWhiteSalmonLodgingTaxSpider": COMMITTEE,
    "ColgoWhiteSalmonTreeBoardSpider": BOARD,
    "ColgoWhiteSalmonCityLABBoardSpider": BOARD,
    "ColgoWhiteSalmonWildfireEmergencyPreparednessSpider": COMMITTEE,
}


def test_every_configured_spider_is_tested():
    assert set(CLASSIFICATIONS) == {c["class_name"] for c in spider_configs}


@pytest.mark.parametrize("class_name", sorted(CLASSIFICATIONS))
def test_spider_attributes(class_name):
    spider = _spider(class_name)
    assert spider.name.startswith("colgo_white_salmon_")
    assert spider.classification == CLASSIFICATIONS[class_name]
    assert spider.meeting_keyword
    assert not hasattr(spider, "agency_id")


# ============================================================================
# Calendar filtering by meeting_keyword
# ============================================================================
# Meeting detail paths each spider follows, per calendar fixture
FOLLOWED = {
    "ColgoWhiteSalmonCityCouncilSpider": {
        "all_2026_10": [
            "/citycouncil/page/city-council-meeting-146",
            "/citycouncil/page/city-council-workshop-1",
            "/citycouncil/page/city-council-meeting-157",
        ],
        "council_2026_10": [
            "/citycouncil/page/city-council-meeting-146",
            "/citycouncil/page/city-council-workshop-1",
            "/citycouncil/page/city-council-meeting-157",
        ],
        "council_2025_01": [
            "/citycouncil/page/city-council-meeting-121",
            "/citycouncil/page/special-city-council-meeting-10",
            "/citycouncil/page/city-council-meeting-122",
        ],
    },
    "ColgoWhiteSalmonCivilServiceSpider": {},
    "ColgoWhiteSalmonPlanningSpider": {
        "all_2026_10": [
            "/bc-pc/page/planning-commission-meeting-97",
            "/bc-pc/page/planning-commission-meeting-106",
        ],
        "planning_2025_01": ["/planning/page/planning-commission-meeting-1"],
    },
    "ColgoWhiteSalmonPersonnelFinanceSpider": {
        "all_2026_10": ["/citycouncil/page/personnel-and-finance-committee-34"],
        "council_2026_10": ["/citycouncil/page/personnel-and-finance-committee-34"],
        "council_2025_01": [
            "/citycouncil/page/personnel-and-finance-committee-cancelled-2",
            "/citycouncil/page/personnel-and-finance-committee-29",
        ],
    },
    "ColgoWhiteSalmonCityOperationsSpider": {
        "all_2026_10": ["/citycouncil/page/city-operations-committee-meeting-39"],
        "council_2026_10": ["/citycouncil/page/city-operations-committee-meeting-39"],
    },
    "ColgoWhiteSalmonCommunityDevelopmentSpider": {
        "all_2026_10": ["/citycouncil/page/community-development-committee-meeting-49"],
        "council_2026_10": [
            "/citycouncil/page/community-development-committee-meeting-49"
        ],
        "council_2025_01": [
            "/citycouncil/page/community-development-committee-meeting-38"
        ],
    },
    "ColgoWhiteSalmonLodgingTaxSpider": {},
    "ColgoWhiteSalmonTreeBoardSpider": {
        "all_2026_10": ["/citycouncil/page/tree-board-meeting-56"],
        "council_2026_10": ["/citycouncil/page/tree-board-meeting-56"],
        "council_2025_01": ["/citycouncil/page/tree-board-meeting-canceled-15"],
    },
    "ColgoWhiteSalmonCityLABBoardSpider": {
        "all_2026_10": ["/citycouncil/page/citylab-board-15"],
        "council_2026_10": ["/citycouncil/page/citylab-board-15"],
        "council_2025_01": ["/citycouncil/page/citylab-board-meeting-20"],
    },
    "ColgoWhiteSalmonWildfireEmergencyPreparednessSpider": {},
}


@pytest.mark.parametrize("class_name", sorted(FOLLOWED))
@pytest.mark.parametrize("calendar_key", sorted(CALENDARS))
def test_calendar_filtering(class_name, calendar_key):
    expected = FOLLOWED[class_name].get(calendar_key, [])
    assert _followed(class_name, calendar_key) == [
        f"{LEGACY_HOST}{path}" for path in expected
    ]


@pytest.mark.parametrize("calendar_key", sorted(CALENDARS))
def test_every_meeting_is_followed_by_exactly_one_spider(calendar_key):
    response = _calendar(calendar_key)
    on_calendar = [
        response.urljoin(href)
        for href in response.css(
            ".view-item-calendar .views-field-title a::attr(href)"
        ).getall()
    ]
    followed = [url for name in FOLLOWED for url in _followed(name, calendar_key)]
    assert sorted(followed) == sorted(on_calendar)


def test_calendar_requests_carry_calendar_start():
    spider = _spider("ColgoWhiteSalmonCityCouncilSpider")
    requests = list(spider.parse(_calendar("all_2026_10")))
    assert [r.meta["calendar_start"] for r in requests] == [
        "2026-10-07T18:00:00-07:00",
        "2026-10-21T17:00:00-07:00",
        "2026-10-21T18:00:00-07:00",
    ]
    assert all(r.errback is not None for r in requests)


# ============================================================================
# Civil Service Commission: no meetings on the calendar
# ============================================================================
def test_civil_service_has_no_meetings():
    spider = _spider("ColgoWhiteSalmonCivilServiceSpider")
    response = _calendar("all_2026_10")
    assert len(response.css(".view-item-calendar")) == 10
    # The commission is only a filter option
    assert response.css('option[value="231"]::text').get() == (
        "-Civil Service Commission"
    )
    assert list(spider.parse(response)) == []
    assert spider.meeting_keyword == "civil-service-commission"
    assert spider.default_description == ""


# ============================================================================
# Meeting detail pages
# ============================================================================
DETAILS = {
    "ColgoWhiteSalmonCityCouncilSpider": {
        "file": "colgo_white_salmon_city_council_detail.html",
        "url": f"{LEGACY_HOST}/citycouncil/page/city-council-meeting-152",
        "title": "City Council Meeting",
        "start": datetime(2025, 12, 17, 18, 0),
        "id": "colgo_white_salmon_city_council/202512171800/x/city_council_meeting",
        "description": ["City Council of White Salmon", "part-time City Attorney"],
        "links": ["Agenda", "Agenda Packet"],
    },
    "ColgoWhiteSalmonPlanningSpider": {
        "file": "colgo_white_salmon_planning_detail.html",
        "url": f"{LEGACY_HOST}/planning/page/planning-commission-meeting-1",
        "title": "Planning Commission Meeting",
        "start": datetime(2025, 1, 20, 18, 0),
        "id": "colgo_white_salmon_planning/202501201800/x/planning_commission_meeting",
        "description": [],
        "links": ["Agenda", "Agenda Packet"],
    },
}


def _parse_detail(class_name, calendar_start=None):
    case = DETAILS[class_name]
    response = file_response(_path(case["file"]), url=case["url"])
    if calendar_start:
        response.meta["calendar_start"] = calendar_start
    with freeze_time("2025-12-22"):
        return list(_spider(class_name).parse_meeting(response))


@pytest.mark.parametrize("class_name", sorted(DETAILS))
def test_meeting_detail(class_name):
    case = DETAILS[class_name]
    items = _parse_detail(class_name)
    assert len(items) == 1
    item = items[0]

    assert item["title"] == case["title"]
    assert item["classification"] == CLASSIFICATIONS[class_name]
    assert item["start"] == case["start"]
    assert item["end"] is None
    assert item["all_day"] is False
    assert item["time_notes"] == ""
    assert item["id"] == case["id"]
    assert item["status"] == PASSED
    assert item["location"] == LOCATION
    assert item["source"] == case["url"]
    for title in case["links"]:
        assert any(link["title"] == title for link in item["links"])
    if case["description"]:
        for text in case["description"]:
            assert text in item["description"]
    else:
        assert item["description"] == ""


def test_start_prefers_calendar_start_over_detail_page():
    items = _parse_detail(
        "ColgoWhiteSalmonCityCouncilSpider",
        calendar_start="2026-03-04T18:00:00-08:00",
    )
    assert items[0]["start"] == datetime(2026, 3, 4, 18, 0)
