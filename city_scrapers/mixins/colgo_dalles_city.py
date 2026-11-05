"""
Mixin for The Dalles, Oregon meetings: dates, times and locations from the
city's Revize calendar, links from the Open Media (OmpNetwork) sessions API.
"""

import html
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import scrapy
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil.rrule import rrulestr
from scrapy import signals
from twisted.python.failure import Failure

# Sessions created before this date store Pacific wall-clock time with a "Z"
# suffix; newer sessions store real UTC.
WALL_CLOCK_CREATED_BEFORE = datetime(2026, 9, 28, tzinfo=timezone.utc)


class DallesApiError(Exception):
    """A Dalles source could not be read."""


class DallesCityMixinMeta(type):
    """Requires child spiders to define the agency's static variables."""

    def __init__(cls, name, bases, dct):
        # Skip validation for the base mixin class itself
        if name != "DallesCityMixin":
            required_static_vars = [
                "agency",
                "name",
                "category_id",
                "calendar_title_pattern",
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
    ``calendar_title_pattern`` picks the agency's events from the calendar;
    ``category_id`` is its Open Media category, used for links.
    """

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
    }

    name = None
    agency = None
    category_id = None
    calendar_title_pattern = None
    location = None
    time_notes = None

    timezone = "America/Los_Angeles"
    base_url = "https://thedalles-oregon.ompnetwork.org"
    calendar_url = (
        "https://www.thedalles.gov/_assets_/plugins/revizeCalendar/"
        "calendar_data_handler.php?webspace=dallesor"
        "&relative_revize_url=//cms8.revize.com&protocol=https:"
    )
    calendar_page_url = "https://www.thedalles.gov/calendar.php"
    # Revize calendar id of "City Public Meetings"
    public_meetings_calendar = "1"
    city_hall = {
        "name": "City Hall",
        "address": "313 Court Street, The Dalles, OR 97058",
    }

    # How far ahead to expand recurring calendar events
    recurrence_horizon_days = 180

    page_size = 100
    # Guard against a "next" link that never ends.
    max_pages = 50

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = 0
        self._sessions = []
        self._meetings_parsed = 0
        self._failed_requests = 0
        self._other_category_items = 0
        self._bad_rrules = 0
        self._unmatched_sessions = 0

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_spider_closed, signal=signals.spider_closed)
        return spider

    # Requests: sessions pages first (to collect links), then the calendar.

    def start_requests(self):
        """Request the first page of this agency's sessions."""
        yield self._sessions_request(self.api_url)

    def _build_api_url(self, offset=0, limit=100):
        """Build a sessions API URL for this agency's category."""
        params = {"category": self.category_id, "limit": limit, "offset": offset}
        return f"{self.base_url}/api/sessions/?{urlencode(params)}"

    @property
    def api_url(self):
        """Construct the initial sessions API URL for the agency."""
        return self._build_api_url(offset=0, limit=self.page_size)

    def _sessions_request(self, url):
        self._pages += 1
        # The API is Django REST Framework: without this header it answers
        # Scrapy's default "Accept: text/html" with its HTML browsable page.
        return scrapy.Request(
            url=url,
            headers={"Accept": "application/json"},
            callback=self.parse_sessions,
            errback=self._sessions_failed,
        )

    def _calendar_request(self):
        return scrapy.Request(
            url=self.calendar_url,
            headers={"Accept": "application/json"},
            callback=self.parse,
            errback=self._calendar_failed,
            dont_filter=True,
        )

    def parse_sessions(self, response):
        """Collect sessions; request the calendar after the last page."""
        try:
            data = self._load_sessions_json(response)
        except DallesApiError as exc:
            self._failed_requests += 1
            self.logger.warning(str(exc))
            yield self._calendar_request()
            return

        for item in data["results"]:
            category = (item.get("category") or {}).get("id")
            if str(category) != str(self.category_id):
                # The API returns every category when it ignores the filter;
                # never attach another agency's documents to this one.
                self._other_category_items += 1
                continue
            self._sessions.append(item)

        next_url = data.get("next")
        if next_url and self._pages < self.max_pages:
            yield self._sessions_request(next_url)
            return
        if next_url:
            self._failed_requests += 1
            self.logger.warning(
                f"{self.name}: stopped after {self._pages} sessions pages; "
                f"next={next_url}"
            )
        yield self._calendar_request()

    def _sessions_failed(self, failure):
        """Carry on without links."""
        self._failed_requests += 1
        self.logger.warning(f"Sessions request failed: {failure.value!r}")
        yield self._calendar_request()

    def _calendar_failed(self, failure):
        self._report_error(
            DallesApiError(
                f"{self.name}: calendar request failed: {failure.value!r} "
                f"(url={self.calendar_url})"
            )
        )

    def _load_sessions_json(self, response):
        """Return the decoded sessions page, or raise DallesApiError."""
        try:
            data = response.json()
        except (ValueError, AttributeError) as exc:
            raise DallesApiError(
                f"{self.name}: sessions API did not return JSON "
                f"({self._describe(response)})"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise DallesApiError(
                f"{self.name}: sessions API JSON has no 'results' list "
                f"(url={response.url})"
            )
        return data

    def _load_calendar_json(self, response):
        """Return the list of calendar events, or raise DallesApiError."""
        try:
            data = response.json()
        except (ValueError, AttributeError) as exc:
            raise DallesApiError(
                f"{self.name}: calendar did not return JSON "
                f"({self._describe(response)})"
            ) from exc
        if not isinstance(data, list):
            raise DallesApiError(
                f"{self.name}: calendar JSON is not a list of events "
                f"(url={response.url})"
            )
        return data

    def _describe(self, response):
        content_type = response.headers.get("Content-Type", b"").decode(
            "latin-1", "replace"
        )
        snippet = " ".join(response.text[:80].split())
        return (
            f"status={response.status}, content-type={content_type!r}, "
            f"url={response.url}, starts={snippet!r}"
        )

    # Calendar parsing

    def parse(self, response):
        """Yield the agency's calendar meetings, with rrules expanded."""
        events = [
            event
            for event in self._load_calendar_json(response)
            if self._is_agency_event(event)
        ]
        horizon = self._now() + timedelta(days=self.recurrence_horizon_days)

        meetings = {}
        one_off_starts = set()
        recurring = []
        for event in events:
            starts = self._event_starts(event, horizon)
            if event.get("rrule"):
                recurring.extend((event, start) for start in starts)
                continue
            for start in starts:
                one_off_starts.add(start)
                self._add_meeting(meetings, event, start)
        for event, start in recurring:
            # A one-off event at the same time replaces that occurrence
            # (e.g. a moved or cancelled regular meeting).
            if start not in one_off_starts:
                self._add_meeting(meetings, event, start)

        ordered = sorted(meetings.values(), key=lambda pair: pair[0]["start"])
        self._attach_links([meeting for meeting, _ in ordered])

        for meeting, raw_title in ordered:
            # _parse_title strips "CANCELLED", so check the raw title too.
            meeting["status"] = self._get_status(meeting, text=raw_title)
            meeting["id"] = self._get_id(meeting)
            self._meetings_parsed += 1
            yield meeting

    def _now(self):
        return datetime.now(ZoneInfo(self.timezone)).replace(tzinfo=None)

    def _is_agency_event(self, event):
        if self.public_meetings_calendar not in (event.get("calendar_displays") or []):
            return False
        title = self._unescape(event.get("title"))
        return bool(re.search(self.calendar_title_pattern, title, flags=re.I))

    def _event_starts(self, event, horizon):
        """Start times of a calendar event: one, or each rrule occurrence."""
        start = self._parse_calendar_datetime(event.get("start"))
        if start is None:
            return []
        rule = event.get("rrule")
        if not rule:
            return [start]
        try:
            rules = rrulestr(rule, forceset=True, dtstart=start)
            return list(rules.between(start, horizon, inc=True))
        except (ValueError, TypeError) as exc:
            self._bad_rrules += 1
            self.logger.warning(
                f"Unreadable rrule on calendar event {event.get('id')}: {exc}"
            )
            return [start]

    def _add_meeting(self, meetings, event, start):
        raw_title = self._unescape(event.get("title"))
        meeting = Meeting(
            title=self._parse_title(raw_title),
            description="",
            classification=self._classification,
            start=start,
            end=self._parse_end(event, start),
            all_day=bool(event.get("allDay")),
            time_notes=self.time_notes or "",
            location=self._parse_location(event.get("location")),
            links=[],
            source=self._parse_source(start),
        )
        # Duplicate events (same title and time) become one meeting.
        meetings.setdefault((meeting["title"], start), (meeting, raw_title))

    def _unescape(self, value):
        text = html.unescape(value or "").replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()

    def _parse_title(self, raw_title):
        """Title without "CANCELLED -" style markers (status carries those)."""
        if not raw_title:
            return self.agency
        title = self._clean_title(raw_title)
        # e.g. "CANCELLED (Holiday) - X" leaves "(Holiday) - X"
        title = re.sub(r"^\([^)]*\)\s*[-|:]\s*", "", title)
        title = re.sub(r"^[\s|:-]+", "", title).strip()
        return title or self.agency

    def _parse_calendar_datetime(self, value):
        """Calendar times are Pacific wall-clock times without an offset."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            self.logger.warning(f"Unreadable calendar date: {value!r}")
            return None
        return parsed.replace(tzinfo=None)

    def _parse_end(self, event, start):
        """End from the event's "HH:MM" duration, if it is under a day."""
        match = re.fullmatch(r"(\d+):(\d{2})", (event.get("duration") or "").strip())
        if not match:
            return None
        duration = timedelta(hours=int(match.group(1)), minutes=int(match.group(2)))
        if not timedelta(0) < duration < timedelta(days=1):
            return None
        return start + duration

    def _parse_location(self, value):
        """Split free-text locations like "Harding House 200 East 4th St"."""
        text = self._unescape(value)
        if not text:
            return self.location
        lowered = text.lower()
        if any(s in lowered for s in ("313 court", "city hall", "council chamber")):
            return dict(self.city_hall)
        match = re.match(r"^(?P<name>\D*?)[\s,]*(?P<address>\d.*)$", text)
        if match:
            return {
                "name": match.group("name").strip(" ,-"),
                "address": match.group("address").strip(),
            }
        return {"name": text, "address": ""}

    def _parse_source(self, start):
        """The calendar page's month view for the meeting's month."""
        params = {
            "view": "month",
            "month": start.month,
            "day": "01",
            "year": start.year,
        }
        return f"{self.calendar_page_url}?{urlencode(params)}"

    # Links from Open Media sessions

    def _attach_links(self, meetings):
        """Attach each session's links to the closest meeting that day."""
        by_date = defaultdict(list)
        for meeting in meetings:
            by_date[meeting["start"].date()].append(meeting)

        for session in self._sessions:
            start = self._parse_session_start(session)
            candidates = by_date.get(start.date()) if start else None
            if not candidates:
                self._unmatched_sessions += 1
                self.logger.debug(
                    f"Session {session.get('id')} ({session.get('title')!r}, "
                    f"{start}) has no calendar meeting; links skipped"
                )
                continue
            meeting = min(candidates, key=lambda m: abs(m["start"] - start))
            hrefs = {link["href"] for link in meeting["links"]}
            for link in self._parse_links(session):
                if link["href"] not in hrefs:
                    hrefs.add(link["href"])
                    meeting["links"].append(link)

        self.crawler.stats.set_value(
            "dalles/sessions_without_meeting", self._unmatched_sessions
        )

    def _parse_session_start(self, item):
        """Session start as a naive Pacific datetime."""
        start = self._parse_iso(item.get("start_date"))
        if start is None:
            return None
        created = self._parse_iso(item.get("created_at"))
        if created is not None and created < WALL_CLOCK_CREATED_BEFORE:
            return start.replace(tzinfo=None)
        return start.astimezone(ZoneInfo(self.timezone)).replace(tzinfo=None)

    def _parse_iso(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            self.logger.warning(f"Unreadable session date: {value!r}")
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _parse_links(self, item):
        """Parse session links (agendas, minutes, packets, videos)."""
        links = []

        video_url = (item.get("video_url") or "").strip()
        if video_url:
            links.append({"href": video_url, "title": "Video"})

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

    # Error reporting

    def _on_spider_closed(self, spider, reason):
        """Report one error per run if a source failed or nothing was found."""
        problems = []
        if self._failed_requests:
            problems.append(
                f"{self._failed_requests} failed sessions request(s); "
                "meetings published without some links"
            )
        if reason == "finished" and self._meetings_parsed == 0:
            problems.append("0 meetings parsed")
        if self._other_category_items:
            problems.append(
                f"{self._other_category_items} session(s) from other categories "
                "skipped"
            )
        if self._bad_rrules:
            problems.append(f"{self._bad_rrules} unreadable calendar rrule(s)")
        if not problems:
            return
        self._report_error(
            DallesApiError(
                f"{self.name}: {'; '.join(problems)} "
                f"(finish_reason={reason}, sessions_pages={self._pages}, "
                f"meetings={self._meetings_parsed})"
            )
        )

    def _report_error(self, exc):
        """Log and send spider_error (forwarded to Sentry)."""
        self.logger.error(str(exc))
        self.crawler.stats.inc_value(f"spider_exceptions/{exc.__class__.__name__}")
        self.crawler.signals.send_catch_log(
            signal=signals.spider_error,
            failure=Failure(exc),
            response=None,
            spider=self,
        )
