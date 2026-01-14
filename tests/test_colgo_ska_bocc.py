from datetime import datetime
from os.path import dirname, join

import pytest
from city_scrapers_core.constants import BOARD
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders import colgo_skamania

SkamaniaBoccSpider = colgo_skamania.SkamaniaBoccSpider

test_response = file_response(
    join(dirname(__file__), "files", "skamania_county_bocc.html"),
    url="https://www.skamaniacounty.org/departments-offices/commissioners/agendas-minutes-meeting-audio/-folder-746#docfold_2924_1241_328_746",  # noqa
)
spider = SkamaniaBoccSpider()

freezer = freeze_time("2026-01-10")
freezer.start()

parsed_items = [item for item in spider.parse(test_response)]

freezer.stop()


def test_title():
    assert parsed_items[0]["title"] == "Board of County Commissioners"


def test_description():
    assert parsed_items[0]["description"] == ""


def test_start():
    assert parsed_items[0]["start"] == datetime(2026, 1, 13, 0, 0)


def test_end():
    assert parsed_items[0]["end"] is None


def test_time_notes():
    assert parsed_items[0]["time_notes"] == (
        "The public is invited to attend the Skamania County Board of "
        "Commissioners (BOCC) meetings in person or remotely via Zoom. "
        "The Board holds its regular business meeting every Tuesday "
        "beginning at 9:30 a.m. in the boardroom on the lower level "
        "of the Skamania County Courthouse. Please refer to the meeting "
        "agenda for the meeting start time."
    )


def test_id():
    assert (
        parsed_items[0]["id"]
        == "colgo_ska_bocc/202601130000/x/board_of_county_commissioners"
    )


def test_status():
    assert parsed_items[0]["status"] == "tentative"


def test_location():
    assert parsed_items[0]["location"] == {
        "name": "Skamania County Courthouse",
        "address": "240 NW Vancouver Ave., Stevenson, WA 98648",
    }


def test_source():
    assert (
        parsed_items[0]["source"]
        == "https://www.skamaniacounty.org/departments-offices/commissioners"
    )


def test_links():
    assert parsed_items[0]["links"] == [
        {
            "href": "https://www.skamaniacounty.org/home/showpublisheddocument/17384",
            "title": "Agenda",
        }
    ]


def test_classification():
    assert parsed_items[0]["classification"] == BOARD


@pytest.mark.parametrize("item", parsed_items)
def test_all_day(item):
    assert item["all_day"] is False
