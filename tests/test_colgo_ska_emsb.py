from datetime import datetime
from os.path import dirname, join

import pytest
import scrapy
from city_scrapers_core.constants import BOARD, PASSED, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from scrapy.http import HtmlResponse

from city_scrapers.mixins.colgo_skamania_ems import SkamaniaEMSCalendarError
from city_scrapers.spiders.colgo_skamania import SkamaniaEmsbSpider


@pytest.fixture
def spider():
    return SkamaniaEmsbSpider()


@pytest.fixture
def listing_response():
    return file_response(
        join(dirname(__file__), "files", "skamania_ems_board_of_commissioners.html"),
        url="https://skamaniaems.com/board-of-commissioners/",
    )


@pytest.fixture
def folders_response():
    return file_response(
        join(
            dirname(__file__),
            "files",
            "skamania_ems_board_of_commissioners_folders.json",
        ),
        url=SkamaniaEmsbSpider.folders_endpoint,
    )


@pytest.fixture
def items_response():
    response = file_response(
        join(
            dirname(__file__),
            "files",
            "skamania_ems_board_of_commissioners_items.json",
        ),
        url=SkamaniaEmsbSpider.items_endpoint,
    )
    request = scrapy.Request(
        url=response.url, meta={"folder_name": "2026 Agenda & Meeting Minutes"}
    )
    return response.replace(request=request)


@pytest.fixture
def parsed_items(spider, items_response, listing_response):
    spider._pending_sharepoint_requests = 1
    list(spider._parse_folder_items(items_response))

    freezer = freeze_time("2026-10-29")
    freezer.start()
    items = list(spider.parse(listing_response))
    freezer.stop()
    return items


def test_folder_requests(spider, folders_response):
    spider._pending_sharepoint_requests = 1
    requests = list(spider._parse_folders(folders_response))
    folder_requests = [r for r in requests if r.callback == spider._parse_folder_items]
    assert [r.meta["folder_name"] for r in folder_requests] == [
        "2023 Agenda & Meeting Minutes",
        "2024 Agenda & Meeting Minutes",
        "2025 Agenda & Meeting Minutes",
        "2026 Agenda & Meeting Minutes",
    ]
    assert all(r.method == "POST" for r in folder_requests)
    # Calendar waits until the folder item requests have been answered
    assert not any(r.url == spider.calendar_url for r in requests)


def test_calendar_starts_after_sharepoint(spider, items_response):
    spider._pending_sharepoint_requests = 1
    requests = list(spider._parse_folder_items(items_response))
    assert [r.url for r in requests] == [spider.calendar_url]


def test_sharepoint_error_starts_calendar(spider):
    failure = type(
        "Failure",
        (),
        {"request": scrapy.Request(spider.sharepoint_url), "value": "timeout"},
    )()
    requests = list(spider._sharepoint_error(failure))
    assert [r.url for r in requests] == [spider.calendar_url]


def test_count(parsed_items):
    assert len(parsed_items) == 12


def test_title(parsed_items):
    assert parsed_items[0]["title"] == "Board of Commissioners"


def test_description(parsed_items):
    assert parsed_items[0]["description"] == (
        "Adjusted in recognition of Martin Luther King, Jr. Day\n\n"
        + SkamaniaEmsbSpider._description
    )
    assert parsed_items[11]["description"] == ""


def test_start(parsed_items):
    assert parsed_items[0]["start"] == datetime(2026, 1, 26, 10, 0)
    assert parsed_items[2]["start"] == datetime(2026, 9, 14, 10, 0)


def test_end(parsed_items):
    assert parsed_items[0]["end"] is None


def test_time_notes(parsed_items):
    assert parsed_items[0]["time_notes"] == (
        "On meeting days that include a public hearing or workshop, the hearing or "
        "workshop will begin promptly at 10:00am and the business meeting will "
        "commence immediately following"
    )


def test_classification(parsed_items):
    assert parsed_items[0]["classification"] == BOARD


def test_status(parsed_items):
    assert parsed_items[0]["status"] == PASSED
    assert parsed_items[11]["status"] == TENTATIVE


def test_location(parsed_items):
    assert parsed_items[0]["location"] == SkamaniaEmsbSpider._location


def test_source(parsed_items):
    assert (
        parsed_items[0]["source"] == "https://skamaniaems.com/board-of-commissioners/"
    )


def test_links(parsed_items):
    assert parsed_items[0]["links"] == [
        {
            "href": "https://skamaniacountyems.sharepoint.com/sites/SkamaniaEMSRescueResourceCenter/_layouts/15/Embed.aspx?UniqueId=118aeb2c-588e-4cbf-948f-9365dfb26acb",  # noqa
            "title": "Agenda",
        },
        {
            "href": "https://skamaniacountyems.sharepoint.com/sites/SkamaniaEMSRescueResourceCenter/_layouts/15/Embed.aspx?UniqueId=ce06253f-3b8d-4b74-85e6-1ab0bd35a875",  # noqa
            "title": "Agenda-Updated",
        },
        {
            "href": "https://skamaniacountyems.sharepoint.com/sites/SkamaniaEMSRescueResourceCenter/_layouts/15/Embed.aspx?UniqueId=e423a565-9b3e-4329-bf2a-124519e10d04",  # noqa
            "title": "Minutes - Approved",
        },
    ]
    # No documents posted yet for December
    assert parsed_items[11]["links"] == []


def test_all_day(parsed_items):
    assert parsed_items[0]["all_day"] is False


def test_id(parsed_items):
    assert (
        parsed_items[0]["id"] == "colgo_ska_emsb/202601261000/x/board_of_commissioners"
    )


def test_future_year_table(spider):
    """A new year heading and table is picked up without code changes"""
    body = """
    <p><strong>2026<br /></strong></p>
    <table><tr><td>December 21</td></tr></table>
    <p><strong>2027</strong></p>
    <table><tr><td>January 25<br />*Adjusted for MLK Day</td></tr></table>
    """
    response = HtmlResponse(
        url=spider.calendar_url, body=body.encode(), encoding="utf-8"
    )
    with freeze_time("2026-10-29"):
        items = list(spider.parse(response))
    assert [item["start"] for item in items] == [
        datetime(2026, 12, 21, 10, 0),
        datetime(2027, 1, 25, 10, 0),
    ]
    assert items[1]["description"] == "Adjusted for MLK Day"


def _schedule_response(spider, cells):
    body = f"<p><strong>2026</strong></p><table><tr>{cells}</tr></table>"
    return HtmlResponse(url=spider.calendar_url, body=body.encode(), encoding="utf-8")


@pytest.mark.parametrize(
    "cell, message",
    [
        ("<td>TBD<br />*Budget Workshop</td>", "no meeting date"),
        ("<td>Smarch 40</td>", "could not parse meeting date"),
    ],
)
def test_cell_without_date_fails_loudly(spider, cell, message):
    response = _schedule_response(spider, f"<td>June 15</td>{cell}")
    with freeze_time("2026-10-29"):
        with pytest.raises(SkamaniaEMSCalendarError, match=message):
            list(spider.parse(response))


def test_parse_file_date(spider):
    assert spider._parse_file_date("01.26.26 Agenda-Updated.pdf") == "2026-01-26"
    assert spider._parse_file_date("03.16.26_Public Hearing Notice.pdf") == (
        "2026-03-16"
    )
    assert spider._parse_file_date("10.16-10.19.23_Minutes - Approved.pdf") is None
