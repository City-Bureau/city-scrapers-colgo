"""
Mixin for City of Stevenson, WA meetings.

In 2026 Stevenson replaced its Drupal site (www.ci.stevenson.wa.us, which now
redirects to www.stevensonwa.gov, where /meetings returns 404). Its Agendas &
Minutes page (https://www.stevensonwa.gov/1391/Agendas-Minutes) now embeds the
CivicClerk portal https://stevensonwa.portal.civicclerk.com, which reads the
public CivicClerk API:

    https://stevensonwa.api.civicclerk.com/v1/Events?$filter=categoryId eq N

Category IDs are listed at https://stevensonwa.api.civicclerk.com/v1/EventCategories.

A response that is not the Events JSON raises StevensonApiError. Failed requests,
or a finished run with no meetings, are reported once at close through the
spider_error signal, which reaches Sentry.
"""

from datetime import datetime
from urllib.parse import urlencode

import scrapy
from city_scrapers_core.constants import (
    CANCELLED,
    CITY_COUNCIL,
    COMMISSION,
    NOT_CLASSIFIED,
    PASSED,
)
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from scrapy import signals
from twisted.python.failure import Failure


class StevensonApiError(Exception):
    """The CivicClerk Events API could not be read, so meetings would be missed."""


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
                "category_id",
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
    Mixin class for Stevenson, WA city meetings, read from CivicClerk.
    """

    board_name = None
    location = None
    category_id = None

    timezone = "America/Los_Angeles"
    api_base = "https://stevensonwa.api.civicclerk.com/v1"
    portal_base = "https://stevensonwa.portal.civicclerk.com"

    # CivicClerk category ID -> classification
    classifications = {26: CITY_COUNCIL, 27: COMMISSION}

    # Guard against an @odata.nextLink that never ends.
    max_pages = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = 0
        self._meetings_parsed = 0
        self._failed_requests = 0

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_spider_closed, signal=signals.spider_closed)
        return spider

    @property
    def api_url(self):
        params = {
            "$filter": f"categoryId eq {self.category_id}",
            "$orderby": "startDateTime desc",
        }
        return f"{self.api_base}/Events?{urlencode(params)}"

    def start_requests(self):
        yield self._request(self.api_url)

    def _request(self, url):
        self._pages += 1
        return scrapy.Request(
            url,
            headers={"Accept": "application/json"},
            callback=self.parse,
            errback=self._request_failed,
        )

    def parse(self, response):
        """
        Parse one page of CivicClerk events and follow ``@odata.nextLink``.

        Raises:
            StevensonApiError: the response is not the Events JSON.
        """
        data = self._load_json(response)

        for event in data["value"]:
            if str(event.get("categoryId")) != str(self.category_id):
                continue
            if event.get("isDeleted"):
                continue
            title = self._parse_title(event)
            meeting = Meeting(
                title=title,
                description=self.description,
                classification=self._parse_classification(),
                start=self._parse_start(event),
                end=None,
                all_day=False,
                time_notes="",
                location=self.location,
                links=self._parse_links(event),
                source=self._parse_source(event),
            )
            meeting["status"] = self._get_status(
                meeting, text=event.get("eventDescription") or ""
            )
            meeting["id"] = self._get_id(meeting)
            self._meetings_parsed += 1
            yield meeting

        next_url = data.get("@odata.nextLink")
        if next_url:
            if self._pages >= self.max_pages:
                raise StevensonApiError(
                    f"{self.name}: stopped after {self._pages} pages; "
                    f"next={next_url}"
                )
            yield self._request(next_url)

    def _load_json(self, response):
        content_type = response.headers.get("Content-Type", b"").decode(
            "latin-1", "replace"
        )
        try:
            data = response.json()
        except (ValueError, AttributeError) as exc:
            snippet = " ".join(response.text[:80].split())
            raise StevensonApiError(
                f"{self.name}: CivicClerk API did not return JSON "
                f"(status={response.status}, content-type={content_type!r}, "
                f"url={response.url}, starts={snippet!r})"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise StevensonApiError(
                f"{self.name}: CivicClerk API JSON has no 'value' list "
                f"(url={response.url})"
            )
        return data

    def _request_failed(self, failure):
        self._failed_requests += 1
        self.logger.warning(f"CivicClerk request failed: {failure.value!r}")

    def _on_spider_closed(self, spider, reason):
        """Report one error per run if requests failed or no meetings were found."""
        problems = []
        if self._failed_requests:
            problems.append(f"{self._failed_requests} failed CivicClerk request(s)")
        if reason == "finished" and self._meetings_parsed == 0:
            problems.append("0 meetings parsed")
        if not problems:
            return
        self._report_error(
            StevensonApiError(
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

    def _parse_title(self, event):
        return (event.get("eventName") or "").strip() or self.agency

    def _parse_classification(self):
        return self.classifications.get(int(self.category_id), NOT_CLASSIFIED)

    def _parse_start(self, event):
        """
        CivicClerk stores the local wall-clock time with a "Z" suffix: the
        portal shows "6:00 PM PDT" and the agenda "6:00 PM" for
        "2026-10-15T18:00:00Z". Return it as a naive local datetime.
        """
        value = event.get("startDateTime") or event.get("eventDate")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None

    def _parse_links(self, event):
        links = []
        for doc in sorted(
            event.get("publishedFiles") or [], key=lambda d: d.get("sort") or 0
        ):
            file_id = doc.get("fileId")
            if not file_id:
                continue
            links.append(
                {
                    "href": (
                        f"{self.api_base}/Meetings/GetMeetingFileStream"
                        f"(fileId={file_id},plainText=false)"
                    ),
                    "title": (doc.get("type") or doc.get("name") or "Document").strip(),
                }
            )
        media_url = (event.get("externalMediaUrl") or "").strip()
        if media_url:
            links.append({"href": media_url, "title": "Video"})
        youtube_id = (event.get("youtubeVideoId") or "").strip()
        if youtube_id and not media_url:
            links.append(
                {
                    "href": f"https://www.youtube.com/watch?v={youtube_id}",
                    "title": "Video",
                }
            )
        return links

    def _parse_source(self, event):
        return f"{self.portal_base}/event/{event['id']}/files"

    def _get_status(self, meeting, text=""):
        combined_text = f"{meeting.get('title', '')} {text}".lower()

        if "cancelled" in combined_text or "canceled" in combined_text:
            return CANCELLED

        # Rescheduled ≠ cancelled on this site
        if (
            "rescheduled" in combined_text
            and meeting["start"]
            and meeting["start"] < datetime.now()
        ):
            return PASSED

        return super()._get_status(meeting, text)
