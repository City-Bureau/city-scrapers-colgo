from datetime import datetime
from os.path import dirname, join

import pytest
from city_scrapers_core.constants import COMMITTEE, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.colgo_columbia_commission import (
    ColgoColumbiaCommissionSpider,
)

upcoming_meetings = file_response(
    join(dirname(__file__), "files", "colgo_columbia_commission_upcoming.html"),
    url="https://gorgecommission.org/home/meetings/",
)

sample_meeting_details = file_response(
    join(dirname(__file__), "files", "colgo_columbia_commission_meeting_detail.html"),
    url="https://gorgecommission.org/meeting/columbia-river-gorge-commission-economic-vitality-committee-meeting-4-1-2026/",  # noqa
)


@pytest.fixture
def spider():
    return ColgoColumbiaCommissionSpider()


@pytest.fixture
def request_items(spider):
    with freeze_time("2026-04-08"):
        return [request for request in spider.parse(upcoming_meetings)]


@pytest.fixture
def parsed_item(spider):
    with freeze_time("2026-04-08"):
        return next(spider._parse_meeting(sample_meeting_details))


def test_count(request_items):
    assert len(request_items) == 27


def test_title(parsed_item):
    assert (
        parsed_item["title"]
        == "Columbia River Gorge Commission Economic Vitality Committee Meeting"
    )


def test_description(parsed_item):
    assert (
        parsed_item["description"]
        == "The Zoom link to join the meeting is located in the agenda."
    )


def test_start(parsed_item):
    assert parsed_item["start"] == datetime(2026, 4, 8, 12, 0)


def test_end(parsed_item):
    assert parsed_item["end"] == datetime(2026, 4, 8, 13, 30)


def test_time_notes(parsed_item):
    assert parsed_item["time_notes"] == ""


def test_id(parsed_item):
    assert (
        parsed_item["id"]
        == "colgo_columbia_commission/202604081200/x/columbia_river_gorge_commission_economic_vitality_committee_meeting"  # noqa
    )


def test_status(parsed_item):
    assert parsed_item["status"] == TENTATIVE


def test_location(parsed_item):
    assert parsed_item["location"] == {
        "name": "",
        "address": "",
    }


def test_source(parsed_item):
    assert (
        parsed_item["source"]
        == "https://gorgecommission.org/meeting/columbia-river-gorge-commission-economic-vitality-committee-meeting-4-1-2026/"  # noqa
    )


def test_links(parsed_item):
    assert parsed_item["links"] == [
        {
            "href": "https://gorgecommission.org/wp-content/uploads/2026/03/Economic-Vitality-Committee-Meeting-Agenda-2026.04.08.pdf",  # noqa
            "title": "Economic Vitality Committee Meeting Agenda - 4/8/2026",
        },
    ]


def test_classification(parsed_item):
    assert parsed_item["classification"] == COMMITTEE
