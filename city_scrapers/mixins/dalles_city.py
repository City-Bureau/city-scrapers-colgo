"""
Mixin for scraping The Dalles, Oregon city government meetings.

The Dalles publishes meetings on the Open Media (OmpNetwork) platform at
https://thedalles-oregon.ompnetwork.org/. In autumn 2026 the platform moved to a
new app: the old ``/api-cache/site/312/sessions`` endpoint now returns the new
app's HTML shell, and sessions are served as JSON from ``/api/sessions/``, with
new category IDs and ISO 8601 dates.

The sessions API is the only source for these spiders: it carries the meeting
list, documents and video links in one response, so nothing else has to load
first. Any response that is not the expected JSON raises ``DallesApiError``,
and a run that ends with no meetings reports one, so a broken source shows up
in Sentry and the cron ``[stats]`` line instead of as a quiet empty feed.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import scrapy
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from scrapy import signals
from twisted.python.failure import Failure

# Sessions created before the platform move store the Pacific wall-clock time
# with a "Z" suffix (e.g. session 538, created 2026-09-21, "2026-09-28T17:30:00Z"
# for a 5:30 p.m. meeting). Sessions created on the new platform store real UTC
# (session 539, created 2026-09-28, onward; e.g. 542 "2026-10-27T00:30:00Z" for
# 5:30 p.m. PDT on Oct 26). Both were checked against the agenda PDFs.
WALL_CLOCK_CREATED_BEFORE = datetime(2026, 9, 28, tzinfo=timezone.utc)


class DallesApiError(Exception):
    """The Dalles sessions API could not be read, so meetings would be missed."""


class DallesCityMixinMeta(type):
    """
    Metaclass that enforces the implementation of required static
    variables in child classes that inherit from DallesCityMixin.
    """

    def __init__(cls, name, bases, dct):
        # Skip validation for the base mixin class itself
        if name != "DallesCityMixin":
            required_static_vars = [
                "agency",
                "name",
                "category_id",
                "location",
                "_classification",
            ]
            missing_vars = [var for var in required_static_vars if var not in dct]

            if missing_vars:
                missing_vars_str = ", ".join(missing_vars)
                raise NotImplementedError(
                    f"{name} must define the following static variable(s): "
                    f"{missing_vars_str}."
                )

        super().__init__(name, bases, dct)


class DallesCityMixin(CityScrapersSpider, metaclass=DallesCityMixinMeta):
    """
    This class is designed to be used as a mixin for The Dalles city website.
    Agencies are identified by a category ID on the Open Media platform
    (listed at https://thedalles-oregon.ompnetwork.org/api/categories/).

    To use this mixin, create a child spider class that inherits from DallesCityMixin
    and define the required static variables: agency, name, category_id, and location.
    """

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
    }

    name = None
    agency = None
    category_id = None
    location = None
    time_notes = None

    timezone = "America/Los_Angeles"
    base_url = "https://thedalles-oregon.ompnetwork.org"

    page_size = 100
    # Guard against a "next" link that never ends.
    max_pages = 50

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = 0
        self._meetings_parsed = 0
        self._failed_requests = 0
        self._other_category_items = 0

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_spider_closed, signal=signals.spider_closed)
        return spider

    def start_requests(self):
        """Request the first page of this agency's sessions."""
        yield self._request(self.api_url)

    def _build_api_url(self, offset=0, limit=100):
        """Build a sessions API URL for this agency's category."""
        params = {"category": self.category_id, "limit": limit, "offset": offset}
        return f"{self.base_url}/api/sessions/?{urlencode(params)}"

    @property
    def api_url(self):
        """Construct the initial API URL for the agency."""
        return self._build_api_url(offset=0, limit=self.page_size)

    def _request(self, url):
        self._pages += 1
        # The API is Django REST Framework: without this header it answers
        # Scrapy's default "Accept: text/html" with its HTML browsable page.
        return scrapy.Request(
            url=url,
            headers={"Accept": "application/json"},
            callback=self.parse,
            errback=self._request_failed,
        )

    def parse(self, response):
        """
        Parse one page of the sessions API and yield meeting items, then
        follow the API's ``next`` link until every page has been read.

        Raises:
            DallesApiError: the response is not the sessions JSON.
        """
        data = self._load_json(response)

        for item in data["results"]:
            category = (item.get("category") or {}).get("id")
            if str(category) != str(self.category_id):
                # The API returns every category when it ignores the filter;
                # never publish another agency's meetings under this one.
                self._other_category_items += 1
                continue

            meeting = Meeting(
                title=self._parse_title(item),
                description="",
                classification=self._classification,
                start=self._parse_start(item),
                end=None,
                all_day=False,
                time_notes=self.time_notes or "",
                location=self.location,
                links=self._parse_links(item),
                source=self._parse_source(item),
            )

            # _parse_title strips "CANCELLED", so check the raw title too.
            meeting["status"] = self._get_status(meeting, text=item.get("title") or "")
            meeting["id"] = self._get_id(meeting)

            self._meetings_parsed += 1
            yield meeting

        next_url = data.get("next")
        if next_url:
            if self._pages >= self.max_pages:
                raise DallesApiError(
                    f"{self.name}: stopped after {self._pages} pages; "
                    f"next={next_url}"
                )
            yield self._request(next_url)

    def _load_json(self, response):
        """Return the decoded sessions page, or raise DallesApiError."""
        content_type = response.headers.get("Content-Type", b"").decode(
            "latin-1", "replace"
        )
        try:
            data = response.json()
        except (ValueError, AttributeError) as exc:
            snippet = " ".join(response.text[:80].split())
            raise DallesApiError(
                f"{self.name}: sessions API did not return JSON "
                f"(status={response.status}, content-type={content_type!r}, "
                f"url={response.url}, starts={snippet!r})"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise DallesApiError(
                f"{self.name}: sessions API JSON has no 'results' list "
                f"(url={response.url})"
            )
        return data

    def _request_failed(self, failure):
        """A page that could not be downloaded; reported at close."""
        self._failed_requests += 1
        self.logger.warning(f"Sessions request failed: {failure.value!r}")

    def _on_spider_closed(self, spider, reason):
        """Report one error per run if pages failed or no meetings were found."""
        problems = []
        if self._failed_requests:
            problems.append(f"{self._failed_requests} failed sessions request(s)")
        if reason == "finished" and self._meetings_parsed == 0:
            problems.append("0 meetings parsed")
        if self._other_category_items:
            problems.append(
                f"{self._other_category_items} item(s) from other categories skipped"
            )
        if not problems:
            return
        self._report_error(
            DallesApiError(
                f"{self.name}: {'; '.join(problems)} "
                f"(finish_reason={reason}, pages={self._pages}, "
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

    def _parse_title(self, item):
        """Parse or generate meeting title."""
        title = (item.get("title") or "").strip()
        if not title:
            return self.agency
        # Clean up title by removing streaming-related suffixes
        # Use built-in _clean_title to remove common irrelevant information
        title = self._clean_title(title)
        # Remove OmpNetwork-specific suffixes using regex to catch all variants
        # Handles: " - Live Stream", "- Live Stream", "-Live Stream",
        #          " | Live Stream", "| Live Stream", "|Live Stream"
        # "- Livestream" is left as is: older meetings already carry it in their
        # IDs, and changing it would duplicate them.
        title = re.sub(
            r"\s*[-|]\s*Live Stream$", "", title, flags=re.IGNORECASE
        ).strip()
        return title

    def _parse_start(self, item):
        """
        Parse the start as a naive datetime in Pacific time (for compatibility
        with city-scrapers-core's _get_status, which uses naive datetime.now()).

        See WALL_CLOCK_CREATED_BEFORE: sessions created before the platform
        move hold the Pacific wall-clock time labelled as UTC; newer sessions
        hold real UTC.
        """
        start = self._parse_iso(item.get("start_date"))
        if start is None:
            return None
        created = self._parse_iso(item.get("created_at"))
        if created is not None and created < WALL_CLOCK_CREATED_BEFORE:
            return start.replace(tzinfo=None)
        return start.astimezone(ZoneInfo(self.timezone)).replace(tzinfo=None)

    @staticmethod
    def _parse_iso(value):
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _parse_links(self, item):
        """Parse meeting links (agendas, minutes, packets, videos)."""
        links = []

        # Add video link if available
        video_url = (item.get("video_url") or "").strip()
        if video_url:
            links.append({"href": video_url, "title": "Video"})

        # Add document links
        for doc in item.get("documents") or []:
            doc_url = (doc.get("file") or doc.get("link") or "").strip()
            if not doc_url:
                continue
            title = (doc.get("label") or "").strip()
            if not title:
                doc_type = (doc.get("type") or "").strip()
                title = doc_type.replace("_", " ").title() or "Document"
            links.append({"href": doc_url, "title": title})

        return links

    def _parse_source(self, item):
        """Link to the session's page on the Open Media site."""
        session_id = item.get("id")
        if session_id:
            return f"{self.base_url}/sessions/{session_id}"
        return self.api_url
