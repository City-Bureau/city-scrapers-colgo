import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

import scrapy
from city_scrapers_core.constants import (
    CANCELLED,
    CITY_COUNCIL,
    COMMISSION,
    NOT_CLASSIFIED,
    PASSED
)
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider


class ColgoStevensonCitySpiderMixinMeta(type):
    """
    Metaclass that enforces the implementation of required static
    variables in child classes that inherit from ColgoStevensonCitySpiderMixin.
    """

    def __init__(cls, name, bases, dct):
        # Only validate if this is not the mixin class itself
        if name != "ColgoStevensonCitySpiderMixin":
            required_static_vars = [
                "agency",
                "name",
                "board_name",
                "location",
                "description",
            ]
            missing_vars = [var for var in required_static_vars if var not in dct]

            if missing_vars:
                missing_vars_str = ", ".join(missing_vars)
                raise NotImplementedError(
                    f"{name} must define the following static variable(s): "
                    f"{missing_vars_str}."
                )

        super().__init__(name, bases, dct)


class ColgoStevensonCitySpiderMixin(
    CityScrapersSpider, metaclass=ColgoStevensonCitySpiderMixinMeta
):
    """
    Mixin class for Stevenson, WA city meetings.
    """

    board_name = None
    location = None
    board_id = 27  # Default board ID (City Council), can be overridden in spider config

    timezone = "America/Los_Angeles"
    base_url = "https://www.ci.stevenson.wa.us/meetings"

    def start_requests(self):
        """
        Generate initial request with date filter parameters.
        Fetches all past, current, and upcoming meetings by setting a wide date range.
        """

        # Set start date to capture all historical meetings
        start_date = datetime(2018, 1, 1)

        # Set end date to 2 years in the future to capture all upcoming meetings
        end_date = datetime.now() + timedelta(days=365 * 2)

        # Build URL with date filters using the board_id
        params = {
            "date_filter[value][month]": start_date.month,
            "date_filter[value][day]": start_date.day,
            "date_filter[value][year]": start_date.year,
            "date_filter_1[value][month]": end_date.month,
            "date_filter_1[value][day]": end_date.day,
            "date_filter_1[value][year]": end_date.year,
            "field_microsite_tid": "All",
            "field_microsite_tid_1": self.board_id,
        }

        url = f"{self.base_url}/?{urlencode(params)}"

        yield scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        meetings = response.css("tr.even, tr.odd")
        if not meetings:
            self.logger.warning(f"No meetings found at {response.url}")
        else:
            for item in meetings:
                meeting = Meeting(
                    title=self._parse_title(item),
                    description=self.description,
                    classification=self._parse_classification(),
                    start=self._parse_start(item),
                    end=None,
                    all_day=False,
                    time_notes="",
                    location=self.location,
                    links=self._parse_links(item, response),
                    source=self._parse_source(response),
                )
                meeting["status"] = self._get_status(meeting)
                meeting["id"] = self._get_id(meeting)
                yield meeting

        # pagination
        next_page = response.css(
            ".pager-next a::attr(href), .pager__item--next a::attr(href)"
        ).get()
        if next_page:
            yield scrapy.Request(response.urljoin(next_page), callback=self.parse)

    def _parse_title(self, item):
        """
        Remove leading date information from the title.
        Preserve the original title text exactly otherwise.
        """
        title = item.css("td.views-field-title::text, .views-field-title::text").get()

        title = title.strip() if title else ""

        # Remove date patterns from the beginning of the title
        date_patterns = [
            # Pattern 1: "DayOfWeek, Month DD-" - e.g., "Wednesday, February 5-"
            r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+[A-Za-z]+\s+\d{1,2}\s*-\s*",  # noqa
            # Pattern 2: "Month DD & DD " - e.g., "May 27 & 28 "
            r"^[A-Za-z]+\s+\d{1,2}\s*&\s*\d{1,2}\s+",
            # Pattern 3: "Month DD-DD, YYYY " - e.g., "October 19-20, 2018 "
            r"^[A-Za-z]+\s+\d{1,2}\s*-\s*\d{1,2},?\s+\d{4}\s+",
            # Pattern 4: "Month DDth/nd/rd/st "
            r"^[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)\s+",
            # Pattern 5: "Month DDth/nd/rd/st, YYYY "
            r"^[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th),?\s+\d{4}\s+",
            # Pattern 6: "Month DD, YYYY " or "Month DD, YYYY - "
            r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}\s*-?\s*",
            # Pattern 7: "Month DD-" - e.g., "February 25-"
            r"^[A-Za-z]+\s+\d{1,2}\s*-\s*",
            # Pattern 8: "Month YYYY " - e.g., "January 2026 ", "July 2022 "
            r"^[A-Za-z]+\s+\d{4}\s+",
        ]

        for pattern in date_patterns:
            title = re.sub(pattern, "", title)

        title = title.strip()

        return title

    def _parse_classification(self):
        """
        Parse or generate classification based on board_id:
        board_id 27 = City Council, board_id 28 = Commission
        """

        if self.board_id == 27:
            return CITY_COUNCIL
        elif self.board_id == 28:
            return COMMISSION
        else:
            return NOT_CLASSIFIED

    def _parse_start(self, item):
        """Parse start datetime as a naive datetime object."""
        # Try to find date and time from multiple possible locations
        date_str = ""
        time_str = ""

        # Look for date
        date_elem = item.css(
            ".meeting-date::text, .views-field-field-date::text, "
            ".date-display-single::text, time::attr(datetime)"
        ).get()

        if date_elem:
            date_str = date_elem.strip()

        # Look for time
        time_elem = item.css(".meeting-time::text, .views-field-field-time::text").get()

        if time_elem:
            time_str = time_elem.strip()

        # Combine and parse
        if date_str:
            return self._parse_datetime(date_str, time_str)

        return None

    def _parse_datetime(self, date_str, time_str=""):
        """
        Parse date and time strings into a datetime object.
        Handles various formats.
        """
        try:
            # Clean up the strings
            date_str = date_str.strip()
            time_str = time_str.strip() if time_str else ""

            # Try ISO format first (from datetime attribute)
            if "T" in date_str:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                return dt.replace(tzinfo=None)

            # Combine date and time
            datetime_str = f"{date_str} {time_str}".strip()

            # Try various date formats
            formats = [
                "%m/%d/%Y - %I:%M%p",  # for "12/01/2025 - 5:30pm"
                "%m/%d/%Y %I:%M%p",
                "%m/%d/%Y %I:%M %p",
                "%m/%d/%Y %H:%M",
                "%B %d, %Y %I:%M%p",
                "%B %d, %Y %I:%M %p",
                "%Y-%m-%d %H:%M:%S",
                "%m/%d/%Y",
                "%B %d, %Y",
            ]

            for fmt in formats:
                try:
                    return datetime.strptime(datetime_str, fmt)
                except ValueError:
                    continue

            return None

        except Exception:
            return None

    def _parse_links(self, item, response):
        """
        Parse links from table columns and assign correct titles.
        """
        headers = ["Agenda", "Agenda Packet", "Minutes", "Video"]
        links = []
        cells = item.css("td")

        # Map table column links to headers
        for header, cell in zip(headers, cells[2:]):  # links start at 3rd td
            href = cell.css("a::attr(href)").get()
            if href:
                links.append({"href": response.urljoin(href), "title": header})

        return links

    def _get_status(self, meeting, text=""):
        combined_text = f"{meeting.get('title', '')} {text}".lower()

        if "cancelled" in combined_text:
            return CANCELLED

        # Rescheduled ≠ cancelled on this site
        if (
            "rescheduled" in combined_text
            and meeting["start"]
            and meeting["start"] < datetime.now()
        ):
            return PASSED

        return super()._get_status(meeting, text)

    def _parse_source(self, response):
        """Parse or generate source."""
        return response.url
