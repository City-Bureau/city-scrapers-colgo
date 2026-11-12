"""
Mixin for The Dalles, Oregon meetings: dates, times and locations from the
city's Revize calendar, links from the Open Media (OmpNetwork) sessions API.
"""

import html
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlencode, urljoin
from zoneinfo import ZoneInfo

import scrapy
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil.relativedelta import relativedelta
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

    # Default location for all agencies.
    city_hall = {
        "name": "City Hall",
        "address": "313 Court Street, The Dalles, OR 97058",
    }

    # A session only gets matched to a meeting of the same kind
    meeting_kinds = ("work session", "executive session", "special")

    # Agenda notes in session titles, e.g. "Zoom Login on Agenda", "see agenda
    # for ZOOM login", "UPDATED ZOOM LINK ON AGENDA"
    login_note_pattern = (
        r"(?:updated\s+|see agenda for\s+)?(?:zoom\s+)?\b(?:log\s?in|link)\b[^,|]*"
    )

    # How far ahead to expand recurring calendar events
    recurrence_horizon_days = 180
    # How far back sessions missing from the calendar are published as meetings
    session_lookback_years = 2

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
            dont_filter=True,
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
        one_off_titles_by_start = defaultdict(set)
        recurring = []
        for event in events:
            starts = self._event_starts(event, horizon)
            if event.get("rrule"):
                recurring.extend((event, start) for start in starts)
                continue
            for start in starts:
                one_off_titles_by_start[start].add(
                    self._parse_title(self._unescape(event.get("title")))
                )
                self._add_meeting(meetings, event, start)
        for event, start in recurring:
            # A one-off event at the same time replaces that occurrence
            # (e.g. a moved or cancelled regular meeting).
            title = self._parse_title(self._unescape(event.get("title")))
            if title not in one_off_titles_by_start[start]:
                self._add_meeting(meetings, event, start)

        pairs = list(meetings.values())
        pairs += self._merge_sessions([meeting for meeting, _ in pairs])
        ordered = sorted(pairs, key=lambda pair: pair[0]["start"])

        for meeting, raw_title in ordered:
            # Status from the raw title only: _parse_title strips "CANCELLED", and
            # most descriptions say "If no agenda is posted that meeting has been
            # cancelled."
            meeting["status"] = self._get_status(
                dict(meeting, description=""), text=raw_title
            )
            meeting["id"] = self._get_id(meeting)
            self._meetings_parsed += 1
            yield meeting

    def _now(self):
        return datetime.now(ZoneInfo(self.timezone)).replace(tzinfo=None)

    def _is_agency_event(self, event):
        if self.public_meetings_calendar not in (event.get("calendar_displays") or []):
            return False
        return self._is_agency_title(self._unescape(event.get("title")))

    def _is_agency_title(self, title):
        return bool(re.search(self.calendar_title_pattern, title or "", flags=re.I))

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
            description=self._parse_description(event),
            classification=self._classification,
            start=start,
            end=self._parse_end(event, start),
            all_day=bool(event.get("allDay")),
            time_notes=self.time_notes or "",
            location=self._parse_location(event.get("location")),
            links=[],
            source=self._parse_source(start),
        )
        # Duplicate events (same title and time) become one meeting; a
        # cancellation wins over an active event whatever the feed order.
        key = (meeting["title"], start)
        existing = meetings.get(key)
        if existing is None or (
            "cancel" in raw_title.lower() and "cancel" not in existing[1].lower()
        ):
            meetings[key] = (meeting, raw_title)

    def _parse_description(self, event):
        """Plain text from the URL-encoded HTML "desc" field."""
        text = unquote(event.get("desc") or "")
        text = re.sub(
            r"<a\b[^>]*?href=[\"']([^\"']*)[\"'][^>]*>(.*?)</a>",
            self._link_text,
            text,
            flags=re.I | re.S,
        )
        text = re.sub(r"<br\s*/?>|</p>|</div>|<hr\s*/?>", "\n", text, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
        lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
        # "To view the agenda, click the "Read More" button below." refers to a
        # button on the city's calendar page
        return "\n".join(
            line
            for line in lines
            if line and not re.search(r"click.*read more", line, flags=re.I)
        )

    def _link_text(self, match):
        """'<a href="url">text</a>' as 'text (url)'."""
        text = self._unescape(re.sub(r"<[^>]+>", "", match.group(2)))
        href = urljoin(self.calendar_page_url, html.unescape(match.group(1)).strip())
        if not match.group(1).strip() or href.removeprefix("mailto:") == text:
            return text
        return f"{text} ({href})"

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
            return dict(self.city_hall)
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

    def _merge_sessions(self, meetings):
        """
        Attach each session's links to the closest same-kind meeting that day.
        Return a (meeting, raw_title) pair for each recent agency session on a
        day with no calendar meeting, which the calendar is missing.
        """
        by_date = defaultdict(list)
        for meeting in meetings:
            by_date[meeting["start"].date()].append(meeting)

        cutoff = self._now() - relativedelta(years=self.session_lookback_years)
        extra = []
        for session in self._sessions:
            start = self._parse_session_start(session)
            raw_title = self._unescape(session.get("title"))
            day = by_date.get(start.date(), []) if start else []
            kinds = self._meeting_kinds(raw_title)
            candidates = [m for m in day if self._meeting_kinds(m["title"]) == kinds]
            if candidates:
                meeting = min(candidates, key=lambda m: abs(m["start"] - start))
                self._add_links(meeting, self._parse_links(session))
            elif start and not day and self._is_agency_title(raw_title):
                if start >= cutoff:
                    extra.append((self._session_meeting(session, start), raw_title))
            else:
                self.logger.warning(
                    f"Session {session.get('id')} ({raw_title!r}, {start}) has no "
                    "matching calendar meeting; links skipped"
                )

        return extra

    def _session_meeting(self, session, start):
        """A meeting from a session, with its own links."""
        return Meeting(
            title=self._parse_session_title(self._unescape(session.get("title"))),
            description=self._parse_session_description(
                self._unescape(session.get("title"))
            ),
            classification=self._classification,
            start=start,
            end=None,
            all_day=False,
            time_notes=self.time_notes or "",
            location=self._parse_session_location(session.get("title")),
            links=self._parse_links(session),
            source=f"{self.base_url}/embed/full/sessions/{session.get('id')}",
        )

    def _parse_session_location(self, title):
        """
        Sessions have no location. Regular and special meetings are at City Hall;
        work sessions (goal setting, joint sessions) are often held elsewhere.
        """
        if "work session" in (title or "").lower():
            return {"name": "TBD", "address": ""}
        return dict(self.city_hall)

    def _strip_session_dates(self, raw_title):
        """Session title without "October 26, 2026" or "11/05/2026" dates."""
        title = self._parse_title(raw_title)
        title = re.sub(
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
            r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}",
            "",
            title,
            flags=re.I,
        )
        return re.sub(r"\b\d{1,2}([/.-])\d{1,2}\1\d{2,4}\b", "", title)

    def _parse_session_description(self, raw_title):
        """Agenda notes from the session title, e.g. "Zoom Login on Agenda"."""
        title = self._strip_session_dates(raw_title)
        notes = re.findall(self.login_note_pattern, title, flags=re.I)
        notes = [re.sub(r"\s+", " ", note).strip(" .-") for note in notes]
        return "\n".join(dict.fromkeys(note for note in notes if note))

    def _parse_session_title(self, raw_title):
        """
        "Planning Commission Meeting, 11/05/2026, Login details on Agenda" and
        "City Council Meeting October 26, 2026 - Livestream" without the date,
        agenda notes (kept in the description) and streaming notes.
        """
        title = self._strip_session_dates(raw_title)
        title = re.sub(
            self.login_note_pattern
            + r"|\bno video\b[^,|]*|\blive\s?stream\b|\brecording\b",
            "",
            title,
            flags=re.I,
        )
        # Drop the pieces left empty where the date and notes were, keeping
        # the separator ("," "|" ":" "." or a spaced dash) between the others.
        pieces = re.split(
            r"(\s*[,|:]\s*|\.(?=\s|$)\s*|(?:^|\s+)[-\u2013]+(?=\s|$)\s*)", title
        )
        result, separator = "", None
        for index, piece in enumerate(pieces[0::2]):
            piece = re.sub(r"\s+", " ", piece).strip(" -")
            # Words left dangling by a removed date or note, e.g. "Meeting of"
            piece = re.sub(r"(?:^|\s+)(?:of|no)$", "", piece, flags=re.I)
            if piece:
                result += (separator or "") + piece
                separator = None
            if result and separator is None and index < len(pieces) // 2:
                mark = pieces[2 * index + 1].strip()[:1]
                separator = {",": ", ", ".": ". ", ":": ": ", "|": " | "}.get(
                    mark, " - "
                )
        return result or self.agency

    def _add_links(self, meeting, links):
        hrefs = {link["href"] for link in meeting["links"]}
        for link in links:
            if link["href"] not in hrefs:
                hrefs.add(link["href"])
                meeting["links"].append(link)

    def _meeting_kinds(self, title):
        # "After Executive Session" is the regular meeting reconvening
        title = re.sub(
            r"\b(?:after|before|following|return from)\s+(?:ex|exec|executive)\b"
            r"\.?\s+session",
            "",
            (title or "").lower(),
        )
        return {kind for kind in self.meeting_kinds if kind in title}

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
