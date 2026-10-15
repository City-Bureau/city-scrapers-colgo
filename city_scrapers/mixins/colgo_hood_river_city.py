"""
A Mixin & Mixin Meta for Hood River City scrapers that share the EventON calendar API.

Required class variables (enforced by metaclass):
    name (str): Spider name/slug (e.g., "colgo_hood_river_city_council")
    agency (str): Full agency name (e.g., "Hood River City Council")
    id (str): The event_type ID used to filter events from the EventON API.
        Each agency has a unique ID on the calendar.
"""

import re
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import scrapy
from city_scrapers_core.constants import (
    ADVISORY_COMMITTEE,
    BOARD,
    CITY_COUNCIL,
    COMMISSION,
    COMMITTEE,
    NOT_CLASSIFIED,
)
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from scrapy import FormRequest, Selector, signals
from scrapy.exceptions import DontCloseSpider
from twisted.python.failure import Failure

OMP_SESSIONS_URL = (
    "https://cityofhoodriver.ompnetwork.org/api-cache/"
    "site/1713/sessions?start={start}&limit={limit}"
)
VIDEO_PAGE_SIZE = 100
# The OMP index held a few hundred sessions in 2026; never queue more than this.
MAX_EXTRA_VIDEO_PAGES = 50


class HoodRiverCalendarError(Exception):
    """The EventON calendar could not be read, so meetings would be missed."""


class ColgoHoodRiverCityMixinMeta(type):
    """
    Metaclass that enforces the implementation of required static
    variables in child classes that inherit from the "Mixin" class.
    """

    def __init__(cls, name, bases, dct):
        required_static_vars = ["agency", "name", "id"]
        missing_vars = [var for var in required_static_vars if var not in dct]

        if missing_vars:
            missing_vars_str = ", ".join(missing_vars)
            raise NotImplementedError(
                f"{name} must define the following static variable(s): "
                f"{missing_vars_str}."
            )

        super().__init__(name, bases, dct)


class ColgoHoodRiverCityMixin(
    CityScrapersSpider, metaclass=ColgoHoodRiverCityMixinMeta
):
    """Mixin for Hood River City meeting spiders using EventON calendar API."""

    # Required to be overridden (enforced by metaclass)
    name = None
    agency = None
    id = None

    # Optional: filter events by title when id is empty/not available
    title_filter = None

    timezone = "America/Los_Angeles"
    location = {
        "name": "Hood River City Hall",
        "address": "211 2nd Street, Hood River, OR 97031",
    }

    # Enable cookies for this spider (needed for EventON API)
    custom_settings = {
        "COOKIES_ENABLED": True,
    }

    # Classification mapping based on meeting type
    classification_map = {
        "City Council": CITY_COUNCIL,
        "City Budget Committee": COMMITTEE,
        "City Tree Committee": COMMITTEE,
        "Planning Commission": COMMISSION,
        "Urban Renewal Advisory Committee": ADVISORY_COMMITTEE,
        "Urban Renewal Agency": COMMITTEE,
        "Landmark Review Board": BOARD,
        "Landmarks Review Board": BOARD,
        "Mayor's Equity Advisory Group": ADVISORY_COMMITTEE,
    }

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_spider_idle, signal=signals.spider_idle)
        crawler.signals.connect(spider._on_spider_closed, signal=signals.spider_closed)
        return spider

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.all_video_sessions = []
        self.video_cache = {}
        self._video_pages_pending = 0
        self._calendar_requested = False
        self._calendar_from_idle = False
        self._calendar_responses = 0
        self._calendar_unreadable = 0
        self._calendar_failures = 0
        self._calendar_events = 0
        self._meetings_parsed = 0

    def start_requests(self):
        """
        Fetch the OMP Network video index, then the EventON calendar.

        Videos only add "Video Recording" links, so the calendar must be fetched
        however the video API behaves. Every video page request has an errback,
        and each page (parsed, unreadable or failed) decrements a counter; the
        calendar is requested when it reaches zero, always from a callback or
        errback. The spider_idle handler is only a last resort.
        """
        yield self._video_request(start=0)

    def _video_request(self, start):
        self._video_pages_pending += 1
        return scrapy.Request(
            url=OMP_SESSIONS_URL.format(start=start, limit=VIDEO_PAGE_SIZE),
            callback=self._parse_video_page,
            errback=self._video_page_failed,
            dont_filter=True,
            meta={"video_start": start},
        )

    def _parse_video_page(self, response):
        """
        Collect one page of video sessions; the first page queues the rest.

        Any problem with the page's content is caught here, so the page is
        always counted as done and the calendar is never left waiting on it.
        Follow-up page requests are only created after the try block, so a
        failure can't leave a counted request that was never yielded.
        """
        page_start = response.meta.get("video_start")
        extra_starts = []
        try:
            extra_starts = self._read_video_page(response, page_start)
        except Exception as e:
            self.logger.warning(
                f"OMP video page {page_start} unreadable "
                f"({e.__class__.__name__}); continuing without those videos"
            )

        for start in extra_starts:
            yield self._video_request(start)
        yield from self._video_page_done()

    def _read_video_page(self, response, page_start):
        """Store a page's sessions; return the starts of further pages to fetch."""
        data = response.json()

        results = data.get("results")
        if isinstance(results, list):
            self.all_video_sessions.extend(r for r in results if isinstance(r, dict))
        else:
            self.logger.warning(f"OMP video page {page_start} has no results list")

        if page_start != 0:
            return []
        try:
            total_size = int(data.get("totalSize") or 0)
        except (TypeError, ValueError):
            self.logger.warning("OMP video index has an unreadable totalSize")
            return []
        last_start = VIDEO_PAGE_SIZE * MAX_EXTRA_VIDEO_PAGES
        if total_size > last_start + VIDEO_PAGE_SIZE:
            self.logger.warning(
                f"OMP video index reports {total_size} sessions; "
                f"fetching only the first {last_start + VIDEO_PAGE_SIZE}"
            )
        return list(
            range(
                VIDEO_PAGE_SIZE,
                min(total_size, last_start + VIDEO_PAGE_SIZE),
                VIDEO_PAGE_SIZE,
            )
        )

    def _video_page_failed(self, failure):
        self.logger.warning(
            f"OMP video request failed ({failure.value!r}); "
            "continuing without those videos"
        )
        yield from self._video_page_done()

    def _video_page_done(self):
        self._video_pages_pending -= 1
        if self._video_pages_pending <= 0:
            yield from self._fetch_calendar()

    def _on_spider_idle(self, spider):
        requests = list(self._on_spider_idle_requests())
        for request in requests:
            self.crawler.engine.crawl(request)
        if requests:
            raise DontCloseSpider

    def _on_spider_idle_requests(self):
        """
        Last resort: the spider went idle before the calendar was requested,
        which the video page callbacks and errbacks should make impossible.

        This runs in the same spider_idle pass in which DiffPipeline injects
        the previous run's items. Those reach the pipeline before the calendar
        responses, so every upcoming meeting is published as cancelled and the
        real meetings are then dropped as already scraped. This run's output
        can't be trusted, so it is reported as an error at spider close.
        """
        if not self._calendar_requested:
            self._calendar_from_idle = True
            self.logger.warning(
                f"{self._video_pages_pending} OMP video page(s) never completed; "
                "fetching the calendar from spider_idle"
            )
            yield from self._fetch_calendar()

    def _build_video_cache(self):
        """Index collected video sessions by date and simplified title."""
        videos = {}

        try:
            for session in self.all_video_sessions:
                video_url = session.get("video_url")
                if video_url:
                    # Store by date and title for matching
                    date_timestamp = session.get("date", 0)
                    if date_timestamp:
                        # Handle both string and int timestamps
                        if isinstance(date_timestamp, str):
                            date_timestamp = int(date_timestamp)
                        tz = ZoneInfo(self.timezone)  # Use the spider's timezone
                        date_key = datetime.fromtimestamp(
                            date_timestamp, tz=tz
                        ).strftime("%Y-%m-%d")

                        title = session.get("title", "").lower()

                        # Store video URL with metadata
                        video_info = {
                            "url": video_url,
                            "title": session.get("title", ""),
                            "date": date_key,
                            "video_id": session.get("video_id", ""),
                        }

                        # Create key from date and simplified title
                        key = f"{date_key}_{self._simplify_title(title)}"
                        videos[key] = video_info

        except Exception as e:
            self.logger.warning(f"Error processing videos: {e}")
            videos = {}

        return videos

    def _fetch_calendar(self):
        """Request each year of the EventON calendar (once per run)."""
        if self._calendar_requested:
            return
        self._calendar_requested = True
        self.video_cache = self._build_video_cache()

        api_url = "https://cityofhoodriver.gov/?evo-ajax=eventon_get_events"
        current_year = datetime.now().year

        # Fetch meetings from 2019 to 1 year in the future
        for year in range(2019, current_year + 2):
            form_data = self._build_form_data(start_month=1, start_year=year)
            yield FormRequest(
                url=api_url,
                formdata=form_data,
                callback=self.parse,
                errback=self._calendar_failed,
                headers={
                    "Referer": "https://cityofhoodriver.gov/administration/meetings/"
                },
                meta={"year": year},
            )

    def _calendar_failed(self, failure):
        """A calendar year that could not be downloaded; reported at close."""
        self._calendar_failures += 1
        self._inc_stat("hood_river/calendar_failed")
        year = failure.request.meta.get("year")
        self.logger.warning(f"EventON request for {year} failed: {failure.value!r}")

    def _inc_stat(self, key):
        crawler = getattr(self, "crawler", None)
        if crawler is not None and crawler.stats is not None:
            crawler.stats.inc_value(key, spider=self)

    def _on_spider_closed(self, spider, reason):
        """
        Send at most one aggregated error per run when the calendar could not
        be read, or a finished run produced no meetings.
        """
        problems = []
        if self._calendar_unreadable:
            problems.append(
                f"{self._calendar_unreadable} unreadable EventON response(s)"
            )
        if self._calendar_failures:
            problems.append(f"{self._calendar_failures} failed EventON request(s)")
        if self._calendar_from_idle:
            problems.append(
                "calendar requested from spider_idle, so upcoming meetings may "
                "have been published as cancelled"
            )
        if reason == "finished" and self._meetings_parsed == 0:
            problems.append("0 meetings parsed")
        if not problems:
            return
        self._report_error(
            HoodRiverCalendarError(
                f"{self.name}: {'; '.join(problems)} "
                f"(finish_reason={reason}, "
                f"calendar requested={self._calendar_requested}, "
                f"responses={self._calendar_responses}, "
                f"events={self._calendar_events}, "
                f"meetings={self._meetings_parsed})"
            )
        )

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

    def _simplify_title(self, title):
        """Simplify title for matching by removing common variations."""

        title = title.lower()
        # Remove common words and variations
        title = re.sub(r"\b(meeting|no\.?\s*\d+|session|#\d+)\b", "", title)
        # Remove extra whitespace and special chars
        title = re.sub(r"[^\w\s]", "", title)
        title = re.sub(r"\s+", "_", title.strip())
        return title[:50]  # Limit length

    def _build_form_data(self, start_month, start_year):
        """Build the form data for the EventON API request."""
        # Calculate Unix timestamps for the month range
        start_date = datetime(start_year, start_month, 1)
        # End date is 12 months from start
        end_year = start_year + 1 if start_month == 1 else start_year
        end_month = 12 if start_month == 1 else start_month - 1
        end_date = datetime(end_year, end_month, 28, 23, 59, 59)  # Safe end of month

        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        # Essential parameters only - removed 70+ UI/display parameters
        form_data = {
            # Core API parameters
            "ajaxtype": "jumper",
            "direction": "none",
            # Date range (CRITICAL)
            "shortcode[fixed_month]": str(start_month),
            "shortcode[fixed_year]": str(start_year),
            "shortcode[focus_start_date_range]": str(start_ts),
            "shortcode[focus_end_date_range]": str(end_ts),
            # Event filtering (CRITICAL)
            "shortcode[event_type]": self.id if self.id else "all",
            # Data retrieval settings (IMPORTANT)
            "shortcode[number_of_months]": "12",
            "shortcode[event_past_future]": "all",
            "shortcode[hide_past]": "no",
            "shortcode[hide_cancels]": "no",
            # Basic display (may affect HTML structure)
            "shortcode[event_order]": "ASC",
            "shortcode[sort_by]": "sort_date",
            "shortcode[calendar_type]": "default",
            # Version info
            "shortcode[_cver]": "4.5.3",
        }

        return form_data

    def parse(self, response):
        """
        Parse one year of the EventON API JSON response.

        A response that is not JSON or carries no calendar HTML is counted
        (stat hood_river/calendar_unreadable) and reported once at spider
        close. A year with no events is normal: EventON returns a "no_events"
        placeholder.
        """
        self._calendar_responses += 1
        year = response.meta.get("year")
        try:
            data = response.json()
        except (AttributeError, ValueError):
            data = None
            detail = (
                f"is not JSON (status {response.status}, "
                f"Content-Type {response.headers.get('Content-Type')!r}, "
                f"{len(response.body)} bytes)"
            )
        else:
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            detail = f"has no calendar HTML (status {response.status}, keys {keys})"

        html_content = self._extract_calendar_html(data)
        if not html_content:
            self._calendar_unreadable += 1
            self._inc_stat("hood_river/calendar_unreadable")
            self.logger.warning(f"EventON response for {year} {detail}")
            return

        selector = Selector(text=html_content)
        self._calendar_events += len(selector.css("[data-event_id]"))
        yield from self._parse_events(selector, response)

    def _extract_calendar_html(self, data):
        """
        The HTML is in data["html"], either a string or a dict keyed by
        calendar ID.
        """
        if not isinstance(data, dict):
            return None
        html = data.get("html")
        if isinstance(html, str):
            return html
        if isinstance(html, dict):
            cal_id = "evcal_calendar_765"
            if html.get(cal_id):
                return html[cal_id]
            # Get first non-empty HTML value
            for key, value in html.items():
                if isinstance(value, str) and value and key != "no_events":
                    return value
        return None

    def _parse_events(self, selector, response):
        """Parse events from the HTML content and validate links asynchronously."""
        for event in selector.css(
            ".eventon_list_event, .evo_eventcard, [data-event_id]"
        ):
            title = self._parse_title(event)
            if not title:
                continue

            if self.title_filter:
                title_lower = title.lower()
                if isinstance(self.title_filter, str):
                    if self.title_filter.lower() not in title_lower:
                        continue
                elif isinstance(self.title_filter, (list, tuple)):
                    if not any(
                        term.lower() in title_lower for term in self.title_filter
                    ):
                        continue

            start = self._parse_start(event)
            if not start:
                continue

            meeting = Meeting(
                title=title,
                description="",
                classification=self._parse_classification(title),
                start=start,
                end=self._parse_end(event),
                all_day=self._parse_all_day(event),
                time_notes=self._parse_time_notes(event),
                location=self._parse_location(event),
                links=self._parse_links(event),
                source=self._parse_source(response),
            )

            # Add video link
            meeting["links"] = self._add_video_link(meeting, meeting["links"])
            meeting["status"] = self._get_status(meeting, event)
            meeting["id"] = self._get_id(meeting)
            self._meetings_parsed += 1

            if response.meta.get("skip_link_validation"):
                yield meeting
            else:
                # Validate links asynchronously - yields Request(s) then the Meeting
                yield from self._validate_links_async(meeting)

    def _clean_title(self, title: str) -> str:
        """Remove dates, meeting numbers, and other noise from title."""
        title = (title or "").strip()
        if not title:
            return ""

        months = (
            r"(January|February|March|April|May|June|July|"
            r"August|September|October|November|December)"
        )

        # Remove date suffixes like " - May 20, 2024 ..." or " May 20, 2024 ..."
        title = re.sub(
            rf"\s*[-–]\s*{months}\s+\d{{1,2}},?\s+\d{{4}}.*$",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(
            rf"\s+{months}\s+\d{{1,2}},?\s+\d{{4}}.*$",
            "",
            title,
            flags=re.IGNORECASE,
        )

        # Remove numeric date suffixes like " - 05/20/2024 ..." or " 05-20-2024 ..."
        title = re.sub(r"\s*[-–]\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*$", "", title)
        title = re.sub(r"\s+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*$", "", title)

        # Remove meeting numbers like "No. 1", "#2"
        title = re.sub(r"\s+No\.?\s*\d+", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\s+#\d+", "", title)

        # Normalize things like "Meeting 3" -> "Meeting"
        title = re.sub(r"\s+Meeting\s+\d+$", " Meeting", title, flags=re.IGNORECASE)

        return title.strip()

    def _parse_title(self, item):
        """Parse or generate meeting title."""
        title_selectors = [
            ".evcal_event_title::text",
            ".evo_event_title::text",
            "[itemprop='name']::text",
            ".event_title::text",
            "span.evcal_desc2::text",
            "a::text",
        ]

        for selector in title_selectors:
            raw_title = item.css(selector).get()
            cleaned = self._clean_title(raw_title)
            if cleaned:
                return cleaned

        # Fallback: data attribute
        raw_title = item.attrib.get("data-event_name", "")
        cleaned = self._clean_title(raw_title)
        if cleaned:
            return cleaned

        return ""

    def _parse_classification(self, title):
        """Parse or generate classification from allowed options."""
        title_lower = title.lower()

        for key, classification in self.classification_map.items():
            if key.lower() in title_lower:
                return classification

        # Default classifications based on keywords
        if "council" in title_lower:
            return CITY_COUNCIL
        if "commission" in title_lower:
            return COMMISSION
        if "committee" in title_lower:
            return COMMITTEE
        if "board" in title_lower:
            return BOARD
        if "advisory" in title_lower:
            return ADVISORY_COMMITTEE

        return NOT_CLASSIFIED

    def _parse_start(self, item):
        """Parse start datetime as a naive datetime object."""
        data_time = item.attrib.get("data-time", "")
        if data_time and "-" in data_time:
            start_ts = data_time.split("-")[0]
            try:
                dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
                return dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        start_date = item.css(".evcal_desc span.evcal_desc2_time::text").get()
        if start_date:
            start_date = start_date.strip()
            return self._combine_date_time(start_date, "")

        return None

    def _parse_end(self, item):
        """Parse end datetime, returning None if it's a placeholder (23:50)."""
        data_time = item.attrib.get("data-time", "")
        if data_time and "-" in data_time:
            end_ts = data_time.split("-")[1]
            try:
                dt = datetime.fromtimestamp(int(end_ts), tz=timezone.utc)
                dt = dt.replace(tzinfo=None)
                # If end time is 23:50, it's a placeholder - let pipeline handle it
                if dt.hour == 23 and dt.minute == 50:
                    return None
                return dt
            except (ValueError, TypeError):
                pass
        return None

    def _parse_time_notes(self, item):
        """Capture useful notes about timing or cancellation."""

        # Detect cancellation
        event_html = (item.get() or "").lower()
        is_cancelled = ("cancelled" in event_html) or ("canceled" in event_html)

        if is_cancelled:
            notes = []
            # Try subtitle first
            subtitle = item.css(
                ".evcal_event_subtitle::text, .evo_event_subtitle::text"
            ).get()
            subtitle = (subtitle or "").strip()

            if subtitle and (
                "cancelled" in subtitle.lower() or "canceled" in subtitle.lower()
            ):
                notes.append(subtitle)
            else:
                # Then try description-like fields
                desc = item.css(
                    ".evo_event_desc::text, .evcal_desc::text, [itemprop='description']::text, .event_description::text"  # noqa
                ).get()
                desc = (desc or "").strip()

                if desc and ("cancelled" in desc.lower() or "canceled" in desc.lower()):
                    notes.append(desc)
                else:
                    notes.append("Meeting has been cancelled")
            return ". ".join(notes)

        return "Please refer to the meeting attachments for more accurate start and end times."  # noqa

    def _parse_all_day(self, item):
        """Parse or generate all-day status. Defaults to False."""
        all_day = item.attrib.get("data-all_day", "")
        return all_day.lower() == "yes" or all_day == "1"

    def _parse_location(self, item):
        """Parse or generate location."""
        location_selectors = [
            ".evo_location::text",
            ".evcal_location::text",
            "[itemprop='location']::text",
            ".event_location::text",
        ]
        for selector in location_selectors:
            loc = item.css(selector).get()
            if loc:
                loc = loc.strip()
                if "city hall" in loc.lower() or "211" in loc:
                    return self.location
                return {"name": loc, "address": ""}

        address_selector = "[itemprop='address']::text"
        address = item.css(address_selector).get()
        if address:
            return {"name": "", "address": address.strip()}

        return self.location

    def _normalize_url(self, url):
        """Normalize URL by ensuring it has a proper protocol."""
        if not url:
            return url

        url = url.strip()

        # Already has protocol
        if url.startswith(("http://", "https://")):
            return url

        # Protocol-relative URL
        if url.startswith("//"):
            return "https:" + url

        # Missing protocol - add https://
        if url.startswith(("www.", "youtube.com", "youtu.be", "cityofhoodriver.gov")):
            return "https://" + url

        # For other cases, assume https:// is needed
        if "." in url and not url.startswith("/"):
            return "https://" + url

        return url

    def _wayback_available_api(self, url: str) -> str:
        """Wayback 'available' API endpoint for a given URL."""
        return f"https://archive.org/wayback/available?url={quote(url, safe='')}"

    def _get_wayback_snapshot_url(self, data: dict):
        """
        Extract a usable snapshot URL from Wayback 'available' API JSON.
        Returns None if nothing is available.
        """
        try:
            closest = (data or {}).get("archived_snapshots", {}).get("closest", {})
            if closest and closest.get("available") and closest.get("url"):
                return closest["url"]
        except Exception:
            pass
        return None

    def _force_https(self, url: str) -> str:
        return re.sub(r"^http://", "https://", (url or "").strip())

    def _wayback_raw(self, snapshot_url: str) -> str:
        """
        Convert replay URL to a more raw capture URL using id_.
        https://web.archive.org/web/<ts>/<orig>
        -> https://web.archive.org/web/<ts>id_/<orig>
        """
        snapshot_url = self._force_https(snapshot_url)
        m = re.match(
            r"^https?://web\.archive\.org/web/(\d+)([^/]*)/(.+)$", snapshot_url
        )
        if not m:
            return snapshot_url

        ts, flags, original = m.group(1), (m.group(2) or ""), m.group(3)
        if "id_" in flags:
            return snapshot_url
        return f"https://web.archive.org/web/{ts}id_/{original}"

    def _looks_like_pdf(self, response) -> bool:
        """
        True only if bytes look like a real PDF, not an HTML replay/wrapper.
        """
        body = (response.body or b"").lstrip()

        # Hard reject obvious HTML
        lowered = body[:400].lower()
        if b"<html" in lowered or b"<!doctype html" in lowered:
            return False

        # PDFs must begin with %PDF
        return body.startswith(b"%PDF")

    def _validate_links_async(self, meeting):
        """Validate links asynchronously using HEAD requests."""
        links_to_validate = [
            link
            for link in meeting.get("links", [])
            if "cityofhoodriver.gov" in link.get("href", "")
        ]

        if not links_to_validate:
            # No links to validate, yield meeting directly
            yield meeting
            return

        # Start validation chain - validate first link
        first_link = links_to_validate[0]
        yield scrapy.Request(
            url=first_link["href"],
            method="HEAD",
            callback=self._handle_link_validation,
            errback=self._handle_link_error,
            dont_filter=True,
            meta={
                "meeting": meeting,
                "current_link": first_link,
                "remaining_links": links_to_validate[1:],
                "validated_links": [],
                "external_links": [
                    link
                    for link in meeting.get("links", [])
                    if "cityofhoodriver.gov" not in link.get("href", "")
                ],
                "handle_httpstatus_list": [404],
            },
        )

    def _handle_link_validation(self, response):
        """Handle successful link validation response."""
        meeting = response.meta["meeting"]
        current_link = response.meta["current_link"]
        remaining_links = response.meta["remaining_links"]
        validated_links = response.meta["validated_links"]
        external_links = response.meta["external_links"]

        if response.status == 404:
            api_url = self._wayback_available_api(current_link["href"])
            yield scrapy.Request(
                url=api_url,
                method="GET",
                callback=self._handle_wayback_lookup,
                errback=self._handle_wayback_error,
                dont_filter=True,
                meta={
                    "meeting": meeting,
                    "original_link": current_link,
                    "remaining_links": remaining_links,
                    "validated_links": validated_links,
                    "external_links": external_links,
                },
            )
            return
        # Link is valid (status < 400)
        if response.status < 400:
            validated_links.append(current_link)
        else:
            self.logger.warning(
                f"Skipping broken link (status {response.status}): {current_link['href']}"  # noqa
            )

        # Continue with next link or yield meeting
        if remaining_links:
            next_link = remaining_links[0]
            yield scrapy.Request(
                url=next_link["href"],
                method="HEAD",
                callback=self._handle_link_validation,
                errback=self._handle_link_error,
                dont_filter=True,
                meta={
                    "meeting": meeting,
                    "current_link": next_link,
                    "remaining_links": remaining_links[1:],
                    "validated_links": validated_links,
                    "external_links": external_links,
                    "handle_httpstatus_list": [404],
                },
            )
        else:
            # All links validated, yield meeting with valid links only
            meeting["links"] = validated_links + external_links
            yield meeting

    def _handle_link_error(self, failure):
        """Handle link validation errors (timeouts, connection errors, etc.)."""
        request = failure.request
        meeting = request.meta["meeting"]
        current_link = request.meta["current_link"]
        remaining_links = request.meta["remaining_links"]
        validated_links = request.meta["validated_links"]
        external_links = request.meta["external_links"]

        self.logger.warning(f"Skipping broken link (error): {current_link['href']}")

        # Continue with next link or yield meeting
        if remaining_links:
            next_link = remaining_links[0]
            yield scrapy.Request(
                url=next_link["href"],
                method="HEAD",
                callback=self._handle_link_validation,
                errback=self._handle_link_error,
                dont_filter=True,
                meta={
                    "meeting": meeting,
                    "current_link": next_link,
                    "remaining_links": remaining_links[1:],
                    "validated_links": validated_links,
                    "external_links": external_links,
                    "handle_httpstatus_list": [404],
                },
            )
        else:
            # All links validated, yield meeting with valid links only
            meeting["links"] = validated_links + external_links
            yield meeting

    def _handle_wayback_lookup(self, response):
        """
        If Wayback has a snapshot URL, validate it (Range GET) and only keep it if it's a real PDF. # noqa
        """
        meeting = response.meta["meeting"]
        original_link = response.meta["original_link"]
        remaining_links = response.meta["remaining_links"]
        validated_links = response.meta["validated_links"]
        external_links = response.meta["external_links"]

        snapshot_url = None
        try:
            data = response.json()
            snapshot_url = self._get_wayback_snapshot_url(data)
        except Exception as e:
            self.logger.warning(
                f"Wayback lookup parse failed for {original_link['href']}: {e}"
            )

        if not snapshot_url:
            self.logger.warning(
                f"No Wayback snapshot found for 404 link: {original_link['href']}"
            )
            yield from self._continue_after_wayback(
                meeting, remaining_links, validated_links, external_links
            )
            return

        # Force https + raw bytes form
        snapshot_url = self._wayback_raw(snapshot_url)

        # Validate that it actually serves PDF bytes (don't accept HTML replay pages)
        yield scrapy.Request(
            url=snapshot_url,
            method="GET",
            headers={"Range": "bytes=0-1024"},
            callback=self._handle_wayback_snapshot_validation,
            errback=self._handle_wayback_snapshot_error,
            dont_filter=True,
            meta={
                "meeting": meeting,
                "original_link": original_link,
                "snapshot_url": snapshot_url,
                "remaining_links": remaining_links,
                "validated_links": validated_links,
                "external_links": external_links,
            },
        )

    def _continue_after_wayback(
        self, meeting, remaining_links, validated_links, external_links
    ):
        """Continue with next link or yield meeting."""
        if remaining_links:
            next_link = remaining_links[0]
            yield scrapy.Request(
                url=next_link["href"],
                method="HEAD",
                callback=self._handle_link_validation,
                errback=self._handle_link_error,
                dont_filter=True,
                meta={
                    "meeting": meeting,
                    "current_link": next_link,
                    "remaining_links": remaining_links[1:],
                    "validated_links": validated_links,
                    "external_links": external_links,
                    "handle_httpstatus_list": [404],
                },
            )
        else:
            meeting["links"] = validated_links + external_links
            yield meeting

    def _handle_wayback_snapshot_validation(self, response):
        meeting = response.meta["meeting"]
        original_link = response.meta["original_link"]
        snapshot_url = response.meta["snapshot_url"]
        remaining_links = response.meta["remaining_links"]
        validated_links = response.meta["validated_links"]
        external_links = response.meta["external_links"]

        # Must look like a real PDF header
        if response.status >= 400 or not self._looks_like_pdf(response):
            ct = (response.headers.get(b"Content-Type") or b"").decode(
                "latin-1", "ignore"
            )
            self.logger.warning(
                f"Wayback snapshot not a usable PDF header (status={response.status}, ct={ct}): {snapshot_url}"  # noqa
            )
            yield from self._continue_after_wayback(
                meeting, remaining_links, validated_links, external_links
            )
            return

        # Now verify the PDF ends properly (tail contains %%EOF)
        yield scrapy.Request(
            url=snapshot_url,
            method="GET",
            headers={"Range": "bytes=-4096"},
            callback=self._handle_wayback_snapshot_tail_validation,
            errback=self._handle_wayback_snapshot_error,
            dont_filter=True,
            meta={
                "meeting": meeting,
                "original_link": original_link,
                "snapshot_url": snapshot_url,
                "remaining_links": remaining_links,
                "validated_links": validated_links,
                "external_links": external_links,
            },
        )

    def _handle_wayback_snapshot_tail_validation(self, response):
        meeting = response.meta["meeting"]
        original_link = response.meta["original_link"]
        snapshot_url = response.meta["snapshot_url"]
        remaining_links = response.meta["remaining_links"]
        validated_links = response.meta["validated_links"]
        external_links = response.meta["external_links"]

        body = response.body or b""

        # Some servers ignore the negative range; still okay — we just check for EOF marker # noqa
        if b"%%EOF" not in body:
            self.logger.warning(
                f"Wayback PDF looks truncated (missing %%EOF): {snapshot_url}"
            )
            yield from self._continue_after_wayback(
                meeting, remaining_links, validated_links, external_links
            )
            return

        # Passed: keep it
        validated_links.append(
            {
                "href": self._normalize_url(self._force_https(snapshot_url)),
                "title": original_link.get("title", "Attachment"),
            }
        )

        yield from self._continue_after_wayback(
            meeting, remaining_links, validated_links, external_links
        )

    def _handle_wayback_snapshot_error(self, failure):
        request = failure.request
        meeting = request.meta["meeting"]
        snapshot_url = request.meta["snapshot_url"]
        remaining_links = request.meta["remaining_links"]
        validated_links = request.meta["validated_links"]
        external_links = request.meta["external_links"]

        self.logger.warning(f"Wayback snapshot validation failed: {snapshot_url}")
        yield from self._continue_after_wayback(
            meeting, remaining_links, validated_links, external_links
        )

    def _handle_wayback_error(self, failure):
        """If Wayback API fails, ignore the attachment and continue."""
        request = failure.request
        meeting = request.meta["meeting"]
        original_link = request.meta["original_link"]
        remaining_links = request.meta["remaining_links"]
        validated_links = request.meta["validated_links"]
        external_links = request.meta["external_links"]

        self.logger.warning(
            f"Wayback lookup failed; skipping 404 link: {original_link['href']}"
        )

        yield from self._continue_after_wayback(
            meeting, remaining_links, validated_links, external_links
        )

    def _parse_links(self, item):
        """Collect agenda/minutes/packet links for later async validation."""
        links = []
        seen = set()

        link_selectors = [
            "a[href*='agenda']",
            "a[href*='minutes']",
            "a[href*='packet']",
            "a[href*='.pdf']",
            ".evcal_evdata_cell a",
            ".evo_event_links a",
        ]

        for selector in link_selectors:
            for link in item.css(selector):
                href = link.attrib.get("href", "")
                if not href:
                    continue

                if "google.com/calendar" in href or "eventon_ics_download" in href:
                    continue

                href = self._normalize_url(href)
                if not href or href in seen:
                    continue

                title = (link.css("::text").get() or "").strip()
                href_lower = href.lower()
                title_lower = title.lower()

                # Only keep agenda/minutes/packet
                if "agenda" in href_lower or "agenda" in title_lower:
                    if not title or title == href:
                        title = "Meeting Agenda"
                elif "minutes" in href_lower or "minutes" in title_lower:
                    if not title or title == href:
                        title = "Meeting Minutes"
                elif "packet" in href_lower or "packet" in title_lower:
                    if not title or title == href:
                        title = "Meeting Packet"
                else:
                    # skip anything else
                    continue

                seen.add(href)
                links.append({"href": href, "title": title})

        return links

    def _add_video_link(self, meeting, links):
        """Add video URL from OMP Network cache if available."""
        if not self.video_cache:
            return links

        # Get meeting date and title for matching
        start_date = meeting.get("start")
        title = meeting.get("title", "")

        if not start_date or not title:
            return links

        # Create matching key
        date_key = start_date.strftime("%Y-%m-%d")
        simplified_title = self._simplify_title(title)
        match_key = f"{date_key}_{simplified_title}"

        # Try exact match first
        video_info = self.video_cache.get(match_key)

        # If no exact match, try fuzzy matching by date
        if not video_info:
            for key, info in self.video_cache.items():
                if key.startswith(date_key + "_"):
                    # Same date, check if titles are similar
                    cached_title_simplified = key.split("_", 1)[1] if "_" in key else ""

                    # Direct substring match
                    if (
                        cached_title_simplified in simplified_title
                        or simplified_title in cached_title_simplified  # noqa
                    ):
                        video_info = info
                        break

                    # Check for common important words in both
                    cached_words = set(re.findall(r"\b\w+\b", cached_title_simplified))
                    title_words = set(re.findall(r"\b\w+\b", simplified_title))
                    common_important_words = (
                        cached_words
                        & title_words
                        & {
                            "budget",
                            "city",
                            "council",
                            "committee",
                            "commission",
                            "planning",
                            "orientation",
                            "workshop",
                            "urban",
                            "renewal",
                        }
                    )

                    if (
                        len(common_important_words) >= 2
                    ):  # At least 2 important words match
                        video_info = info
                        break

        # Add video link if found
        if video_info:
            video_url = self._normalize_url(video_info["url"])
            if video_url not in {link["href"] for link in links}:
                links.append({"href": video_url, "title": "Video Recording"})

        return links

    def _parse_source(self, response):
        """Parse or generate source."""
        return "https://cityofhoodriver.gov/administration/meetings/"

    def _get_status(self, meeting, event=None):
        """
        Get meeting status, detecting cancelled meetings from EventON HTML.

        Args:
            meeting: Meeting dict with parsed data
            event: Scrapy selector for the event HTML element (optional)

        Returns:
            str: Status constant (CANCELLED, PASSED, or TENTATIVE)
        """
        from city_scrapers_core.constants import CANCELLED

        # Check for cancellation indicators in EventON HTML
        if event is not None:
            # Check for "cancelled" in CSS classes
            event_classes = event.attrib.get("class", "").lower()
            if "cancel" in event_classes:
                return CANCELLED

            # Check for cancellation in event status attribute
            event_status = event.attrib.get("data-event_status", "").lower()
            if "cancel" in event_status:
                return CANCELLED

            # Check for cancellation in event description or content
            event_html = event.get().lower()
            if "cancelled" in event_html or "canceled" in event_html:
                # Make sure it's not just in a link URL
                event_text = " ".join(event.css("::text").getall()).lower()
                if "cancelled" in event_text or "canceled" in event_text:
                    return CANCELLED

        # The core helper treats "rescheduled" anywhere in the title as
        # cancelled. On this calendar a "Rescheduled from <date>" or
        # "Originally Scheduled for <date>" title marks the new, active
        # meeting (the old date is a separate cancelled event), so strip
        # those phrases before falling back to the date-based status.
        title = re.sub(
            r"\b(rescheduled|moved)\s+from\b|\boriginally\s+scheduled\b",
            "",
            meeting.get("title", ""),
            flags=re.IGNORECASE,
        )
        return super()._get_status({**dict(meeting), "title": title})

    def _combine_date_time(self, date_str, time_str):
        """Combine date and time strings into datetime."""
        date_str = date_str.strip() if date_str else ""
        time_str = time_str.strip() if time_str else ""

        # Try ISO format first
        try:
            return datetime.fromisoformat(date_str.replace("Z", ""))
        except ValueError:
            pass

        # Try common date formats
        date_formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]
        parsed_date = None
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue

        if not parsed_date:
            return None

        # Try to parse time
        if time_str:
            time_str = re.sub(r"\s+", " ", time_str).strip()
            time_formats = [
                "%I:%M %p",
                "%I:%M%p",
                "%H:%M",
                "%I %p",
            ]
            for fmt in time_formats:
                try:
                    time_obj = datetime.strptime(time_str.upper(), fmt)
                    return datetime.combine(parsed_date.date(), time_obj.time())
                except ValueError:
                    continue

        return parsed_date
