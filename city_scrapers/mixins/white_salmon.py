"""
White Salmon Mixin for scrapers that share a common data source.

This mixin scrapes meeting data from the City of White Salmon government website
using CSS selectors on their Drupal calendar.

Calendar URL:
    https://www.whitesalmonwa.gov/calendar/month/{YYYY-MM}?field_microsite_tid=All&field_microsite_tid_1={agency_id}

Required class variables (enforced by __init_subclass__):
    name (str): Spider name/slug (e.g., "colgo_white_salmon_city_council")
    agency (str): Full agency name (e.g., "City Council of White Salmon")
    agency_id (str): Agency filter ID from website (e.g., "27")
    meeting_keyword (str): Keyword to filter meeting links (e.g., "city-council")
    classification: Meeting classification constant (e.g., CITY_COUNCIL)

Example:
    class ColgoWhiteSalmonCityCouncilSpider(WhiteSalmonMixin):
        name = "colgo_white_salmon_city_council"
        agency = "City Council of White Salmon"
        agency_id = "27"
        meeting_keyword = "city-council"
        classification = CITY_COUNCIL
"""

import re
from datetime import datetime
from typing import ClassVar

import scrapy
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil.relativedelta import relativedelta


class WhiteSalmonMixin(CityScrapersSpider):
    """
    Base mixin class for scraping White Salmon government meetings.

    Uses CSS selectors to extract meeting data from the Drupal calendar.
    """

    # Required to be overridden (enforced by __init_subclass__)
    name = None
    agency = None
    agency_id = None
    meeting_keyword = None
    classification = None

    _required_vars = [
        "agency",
        "name",
        "agency_id",
        "meeting_keyword",
        "classification",
    ]

    def __init_subclass__(cls, **kwargs):
        """Enforces the implementation of required class variables in subclasses."""
        super().__init_subclass__(**kwargs)

        missing_vars = [
            var
            for var in cls._required_vars
            if (var != "meeting_keyword" and not getattr(cls, var, None))
            or (var == "meeting_keyword" and getattr(cls, var, None) is None)
        ]

        if missing_vars:
            missing_vars_str = ", ".join(missing_vars)
            raise NotImplementedError(
                f"{cls.__name__} must define the following static variable(s): "
                f"{missing_vars_str}."
            )

    # Configuration
    timezone = "America/Los_Angeles"
    calendar_url = (
        "https://www.whitesalmonwa.gov/calendar/month/{month}"
        "?field_microsite_tid=All&field_microsite_tid_1={agency_id}"
    )

    # Default location fallback
    default_location: ClassVar[dict[str, str]] = {
        "name": "",
        "address": "",
    }

    def start_requests(self):
        """
        Generate requests for current and future months.

        Yields:
            Request: GET requests to calendar pages
        """
        today = datetime.now()
        # Scrape current month plus next 2 months
        for i in range(3):
            target_date = today + relativedelta(months=i)
            month_str = target_date.strftime("%Y-%m")
            url = self.calendar_url.format(month=month_str, agency_id=self.agency_id)
            yield scrapy.Request(url=url, callback=self.parse)

    def parse(self, response):
        """
        Parse calendar page and follow links to meeting detail pages.

        Args:
            response: Scrapy response containing calendar HTML

        Yields:
            Request: Requests to individual meeting detail pages
        """
        # Find all meeting links in the calendar
        meeting_links = response.css(
            ".view-item-calendar .views-field-title a::attr(href)"
        ).getall()

        for link in meeting_links:
            # Filter by meeting_keyword to only scrape relevant meetings
            if self.meeting_keyword and self.meeting_keyword not in link:
                continue
            full_url = response.urljoin(link)
            yield scrapy.Request(url=full_url, callback=self.parse_meeting)

    def parse_meeting(self, response):
        """
        Parse a single meeting detail page.

        Args:
            response: Scrapy response containing meeting detail HTML

        Yields:
            Meeting: Parsed meeting item
        """
        title = self._parse_title(response)
        start = self._parse_start(response)

        if not start:
            self.logger.warning("Skipping meeting with no start time: %s", response.url)
            return

        meeting = Meeting(
            title=title,
            description=self._parse_description(response),
            classification=self.classification,
            start=start,
            end=None,
            all_day=False,
            time_notes="",
            location=self._parse_location(response),
            links=self._parse_links(response),
            source=response.url,
        )

        meeting["status"] = self._get_status(meeting, text=title)
        meeting["id"] = self._get_id(meeting)

        yield meeting

    def _parse_title(self, response):
        """Extract meeting title from page."""
        title = response.css("#page-title::text").get()
        return (title and title.strip()) or self.agency

    def _parse_start(self, response):
        """
        Parse meeting start datetime from detail page.

        The datetime is stored in ISO format in the content attribute
        of span.date-display-single elements.

        Returns:
            datetime: Naive datetime object or None
        """
        # Look for ISO datetime in content attribute
        dt_str = response.css(
            ".calendar-date span.date-display-single::attr(content)"
        ).get()

        if dt_str:
            try:
                # Parse ISO format: 2025-12-17T18:00:00-08:00
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                # Return naive datetime (timezone handled by spider)
                return dt.replace(tzinfo=None)
            except ValueError:
                self.logger.debug("Failed to parse datetime: %s", dt_str)

        return None

    def _parse_location(self, response):
        """
        Parse location from meeting detail page.

        Looks for "Location:" pattern in the page content.

        Returns:
            dict: Location with name and address keys
        """
        # Look for location in the body content using Scrapy selectors
        for p in response.css(".field-name-body .field-item p"):
            p_text = p.xpath("string()").get()
            match = re.search(r"location:(.*)", p_text or "", re.IGNORECASE | re.DOTALL)
            if match:
                location_text = match.group(1).strip()
                parts = [part.strip() for part in location_text.split(",", 1)]
                if len(parts) >= 1 and parts[0]:
                    name = parts[0]
                    address = parts[1] if len(parts) > 1 else ""
                    return {"name": name, "address": address}

        return self.default_location.copy()

    def _parse_description(self, response):
        """Extract meeting description if available."""
        selector = response.css(".field-name-body .field-item")
        description = selector.xpath("string()").get()
        return description.strip() if description else ""

    def _parse_links(self, response):
        """
        Extract document links from meeting detail page.

        Args:
            response: Scrapy response

        Returns:
            list: List of link dicts with href and title
        """
        links = []

        # Standard document links with their CSS selectors and titles
        link_configs = [
            (".field-name-field-agenda-link a::attr(href)", "Agenda"),
            (".field-name-field-packets-link a::attr(href)", "Agenda Packet"),
            (".field-name-field-video-link a::attr(href)", "Video"),
        ]

        for selector, title in link_configs:
            href = response.css(selector).get()
            if href:
                links.append({"href": response.urljoin(href), "title": title})

        # Supporting documents
        for doc in response.css(".other_attachments .filefield-file a"):
            href = doc.css("::attr(href)").get()
            title = doc.css("::text").get()
            if href and title:
                links.append({"href": response.urljoin(href), "title": title.strip()})

        return links
