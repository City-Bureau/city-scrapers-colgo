"""
White Salmon Mixin for scrapers that share a common data source.

This mixin scrapes meeting data from the City of White Salmon government website
using CSS selectors on their Drupal calendar.

Calendar URL:
    https://legacywhitesalmonwa.teammunicode.com/calendar/month/{YYYY-MM}
        ?field_microsite_tid=All&field_microsite_tid_1=All

Every spider reads the unfiltered ("All") calendar and keeps only the meetings
whose detail link contains its meeting_keyword. The per-agency filter is not
used: the City Council filter (27), for example, also lists Tree Board, CityLAB
Board and committee meetings.

In 2026 the city moved www.whitesalmonwa.gov to CivicPlus, where the old
calendar URLs return 404. The Drupal site still runs on the legacy host, and the
city's own Agendas & Minutes page (https://www.whitesalmonwa.gov/186/Agendas-Minutes)
embeds https://legacywhitesalmonwa.teammunicode.com/meetings, so that is where
meetings are published for now.

If any calendar or meeting page fails to download, the calendar pages list no
events at all, or meeting links are followed but none can be parsed, the spider
reports one WhiteSalmonSourceError through the spider_error signal (which
reaches Sentry) instead of finishing quietly. If the calendar lists events but
none match meeting_keyword (e.g. the Civil Service Commission has no meetings),
it only logs a warning and counts it in the white_salmon/no_matching_meetings
stat.

Required class variables (enforced by __init_subclass__):
    name (str): Spider name/slug (e.g., "colgo_white_salmon_city_council")
    agency (str): Full agency name (e.g., "City Council of White Salmon")
    meeting_keyword (str): Text a meeting link must contain to belong to this
        spider (e.g., "city-council"). An empty string keeps every meeting.
    classification: Meeting classification constant (e.g., CITY_COUNCIL)

Example:
    class ColgoWhiteSalmonCityCouncilSpider(WhiteSalmonMixin):
        name = "colgo_white_salmon_city_council"
        agency = "City Council of White Salmon"
        meeting_keyword = "city-council"
        classification = CITY_COUNCIL
"""

from datetime import datetime
from typing import ClassVar

import scrapy
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil.relativedelta import relativedelta
from scrapy import signals
from twisted.python.failure import Failure


class WhiteSalmonSourceError(Exception):
    """The White Salmon calendar could not be read, so meetings would be missed."""


class WhiteSalmonMixin(CityScrapersSpider):
    """
    Base mixin class for scraping White Salmon government meetings.

    Uses CSS selectors to extract meeting data from the Drupal calendar.
    """

    # Required to be overridden (enforced by __init_subclass__)
    name = None
    agency = None
    meeting_keyword = None
    classification = None

    _required_vars = [
        "agency",
        "name",
        "meeting_keyword",
        "classification",
    ]

    def __init_subclass__(cls, **kwargs):
        """Enforces the implementation of required class variables in subclasses."""
        super().__init_subclass__(**kwargs)

        missing_vars = []
        for var in cls._required_vars:
            value = getattr(cls, var, None)
            # `meeting_keyword` can be an empty string, but not None.
            if var == "meeting_keyword":
                if value is None:
                    missing_vars.append(var)
            # Other required variables must be truthy.
            elif not value:
                missing_vars.append(var)

        if missing_vars:
            missing_vars_str = ", ".join(missing_vars)
            raise NotImplementedError(
                f"{cls.__name__} must define the following static variable(s): "
                f"{missing_vars_str}."
            )

    # Configuration
    timezone = "America/Los_Angeles"
    calendar_url = (
        "https://legacywhitesalmonwa.teammunicode.com/calendar/month/{month}"
        "?field_microsite_tid=All&field_microsite_tid_1=All"
    )

    # Rate limiting settings to avoid 429 errors
    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 2,  # 2 seconds between requests
        "RANDOMIZE_DOWNLOAD_DELAY": True,  # Randomize delay between 0.5x and 1.5x
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,  # Only 1 concurrent request per domain
    }

    # Default location - consistent across all White Salmon meetings
    default_location: ClassVar[dict[str, str]] = {
        "name": "City's Council Chambers",
        "address": "119 NE Church Ave, White Salmon, WA 98672",
    }

    # Default description - can be overridden by subclasses
    default_description = ""

    # Number of years to scrape into the past
    years_back = 3
    # Number of months to scrape into the future
    months_ahead = 12

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._calendar_requested = 0
        self._calendar_ok = 0
        self._failed_requests = 0
        # Events listed on calendar pages, and how many matched meeting_keyword
        self._calendar_events = 0
        self._meetings_matched = 0
        self._meetings_parsed = 0

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_spider_closed, signal=signals.spider_closed)
        return spider

    def start_requests(self):
        """
        Generate requests for the unfiltered calendar for past years and
        upcoming months.

        Scrapes calendar data from years_back years in the past
        through months_ahead months into the future.

        Yields:
            Request: GET requests to calendar pages
        """
        today = datetime.now()
        # Calculate total months to scrape:
        # years_back years of past data + current month + months_ahead future months
        total_months = (self.years_back * 12) + 1 + self.months_ahead
        start_date = today - relativedelta(years=self.years_back)

        for i in range(total_months):
            target_date = start_date + relativedelta(months=i)
            month_str = target_date.strftime("%Y-%m")
            url = self.calendar_url.format(month=month_str)
            self._calendar_requested += 1
            yield scrapy.Request(
                url=url, callback=self.parse, errback=self._request_failed
            )

    def parse(self, response):
        """
        Parse calendar page and follow links to meeting detail pages.

        Args:
            response: Scrapy response containing calendar HTML

        Yields:
            Request: Requests to individual meeting detail pages
        """
        self._calendar_ok += 1
        # Each calendar item contains the meeting link and its datetime
        for item in response.css(".view-item-calendar"):
            link = item.css(".views-field-title a::attr(href)").get()
            if not link:
                continue
            self._calendar_events += 1

            # Filter by meeting_keyword to only scrape relevant meetings
            if self.meeting_keyword and self.meeting_keyword not in link:
                continue
            self._meetings_matched += 1

            calendar_dt = item.css(
                ".views-field-field-calendar-date .date-display-single::attr(content)"
            ).get()

            meta = {}
            if calendar_dt:
                meta["calendar_start"] = calendar_dt

            full_url = response.urljoin(link)
            yield scrapy.Request(
                url=full_url,
                callback=self.parse_meeting,
                errback=self._request_failed,
                meta=meta,
                dont_filter=True,
            )

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
            description=self.default_description,
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

        self._meetings_parsed += 1
        yield meeting

    def _request_failed(self, failure):
        """A calendar or meeting page that could not be downloaded."""
        self._failed_requests += 1
        self.logger.warning(f"Request failed: {failure.value!r}")

    def _on_spider_closed(self, spider, reason):
        """
        Report once per run when the scraper may be broken or found nothing.

        A finished run with 0 meetings is an error when something points at
        the scraper or the site: a request failed, the calendar pages listed no
        events at all (the page layout or URL changed), or meeting links were
        followed but none could be parsed. When the calendar pages listed
        events and none matched meeting_keyword, the calendar simply has no
        meetings of this type, which is only a warning.
        """
        problems = []
        if self._failed_requests:
            problems.append(f"{self._failed_requests} failed request(s)")
        no_meetings = reason == "finished" and self._meetings_parsed == 0
        if no_meetings and not self._calendar_events:
            problems.append("0 meetings parsed: calendar pages listed no events")
        elif no_meetings and self._meetings_matched:
            problems.append(
                f"0 meetings parsed from {self._meetings_matched} meeting page(s)"
            )
        elif no_meetings and self._failed_requests:
            problems.append("0 meetings parsed")

        summary = (
            f"(finish_reason={reason}, "
            f"calendar pages ok={self._calendar_ok}/{self._calendar_requested}, "
            f"calendar events={self._calendar_events}, "
            f"matched={self._meetings_matched}, "
            f"meetings={self._meetings_parsed})"
        )
        if problems:
            self._report_error(
                WhiteSalmonSourceError(f"{self.name}: {'; '.join(problems)} {summary}")
            )
        elif no_meetings:
            self._report_warning(
                f"{self.name}: no meetings matching {self.meeting_keyword!r} "
                f"on the calendar {summary}",
                stat="no_matching_meetings",
            )

    def _report_warning(self, message, stat):
        """
        Log a warning and count it in the white_salmon/{stat} stat. Unlike
        _report_error this does not send the spider_error signal, so it does
        not reach Sentry.
        """
        self.logger.warning(message)
        self.crawler.stats.inc_value(f"white_salmon/{stat}", spider=self)

    def _report_error(self, exc):
        """
        Report an error outside a callback the way Scrapy reports callback
        errors: ERROR log, spider_exceptions stat, and the spider_error signal
        that scrapy_sentry_errors.extensions.Errors forwards to Sentry.
        """
        self.logger.error(str(exc))
        self.crawler.stats.inc_value(
            f"spider_exceptions/{exc.__class__.__name__}", spider=self
        )
        self.crawler.signals.send_catch_log(
            signal=signals.spider_error,
            failure=Failure(exc),
            response=None,
            spider=self,
        )

    def _parse_title(self, response):
        """Extract meeting title from page."""
        title_str = response.css("#page-title::text").get() or ""
        return title_str.strip() or self.agency

    def _parse_start(self, response):
        """
        Parse meeting start datetime from detail page.

        The datetime is stored in ISO format in the content attribute
        of span.date-display-single elements. Tries multiple sources
        in priority order: calendar grid datetime, then detail page datetime.

        Returns:
            datetime: Naive datetime object or None
        """
        candidates = [
            response.meta.get("calendar_start"),
            response.css(
                ".calendar-date span.date-display-single::attr(content)"
            ).get(),
        ]
        for dt_str in candidates:
            if dt_str:
                parsed = self._parse_iso_datetime(dt_str)
                if parsed:
                    return parsed
        return None

    def _parse_iso_datetime(self, dt_str: str):
        """Parse an ISO datetime string with offset into a naive datetime."""
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None)
        except ValueError:
            self.logger.debug("Failed to parse datetime: %s", dt_str)
            return None

    def _parse_location(self, response):
        """
        Return the default location for White Salmon meetings.

        All White Salmon government meetings are held at the same location
        (City's Council Chambers), so we use a consistent default rather
        than parsing potentially inconsistent text from the page.

        Returns:
            dict: Location with name and address keys
        """
        return self.default_location.copy()

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
