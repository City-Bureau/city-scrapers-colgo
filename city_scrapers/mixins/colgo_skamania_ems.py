import re
from datetime import datetime
from urllib.parse import quote, urljoin

import scrapy
from city_scrapers_core.constants import BOARD
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil.parser import parse as date_parser

from city_scrapers.mixins.colgo_skamania import SkamaniaCountyMixinMeta

REAL_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)


class SkamaniaEMSCalendarError(Exception):
    """A meeting schedule cell has no readable date, so meetings would be missed."""


class SkamaniaEMSMixin(CityScrapersSpider, metaclass=SkamaniaCountyMixinMeta):
    name = None
    agency = None
    agenda_param = None
    time_notes = ""
    start_time = datetime.min.time()

    timezone = "America/Los_Angeles"

    _location = {
        "name": "Station 7-1",
        "address": "253 SW First St., Stevenson, WA 98648",
    }

    custom_settings = {
        "USER_AGENT": REAL_UA,
        "COOKIES_ENABLED": True,
        "ROBOTSTXT_OBEY": False,
        "FEED_EXPORT_ENCODING": "utf-8",
    }
    calendar_url = "https://skamaniaems.com/board-of-commissioners/"

    sharepoint_base_url = "https://skamaniacountyems.sharepoint.com"
    sharepoint_list_url = "https://skamaniacountyems.sharepoint.com/sites/SkamaniaEMSRescueResourceCenter/_api/web/GetListUsingPath(DecodedUrl=@a1)/RenderListDataAsStream?@a1=%27%2Fsites%2FSkamaniaEMSRescueResourceCenter%2FShared%20Documents%27"  # noqa
    folders_endpoint = f"{sharepoint_list_url}&RootFolder=%2Fsites%2FSkamaniaEMSRescueResourceCenter%2FShared%20Documents%2FSkamania%20EMS%20%26%20Rescue%20Public%20Documents%2FCommissioner%20Documents"  # noqa
    items_endpoint = sharepoint_list_url + "&RootFolder={root_folder}"
    sharepoint_url = "https://skamaniacountyems.sharepoint.com/:f:/s/SkamaniaEMSRescueResourceCenter/EhpbolB-av5FvuOOU3klW5IB-Ogwq-f33uqCMS-f8qA4ag?e=U6gn2K"  # noqa

    _description = f"To be able to access the meeting attachments, please visit the following SharePoint link once (visiting this link will provide your browser with the appropriate access cookies): {sharepoint_url}"  # noqa
    # RenderListDataAsStream only accepts POST; the FedAuth cookie from the
    # sharing link is carried by CookiesMiddleware, not set here

    sharepoint_headers = {
        "Origin": "https://skamaniacountyems.sharepoint.com",
        "Accept": "application/json;odata=nometadata",
        "Content-Type": "application/json;odata=nometadata",
    }
    sharepoint_body = '{"parameters": {"RenderOptions": 2}}'

    agenda_folder_re = re.compile(r"^\d{4} Agenda & Meeting Minutes$")

    # File names start with the meeting date, e.g. "01.26.26 Agenda.pdf"
    file_date_re = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2})(?=[\s_])")

    _title = "Board of Commissioners"

    def __init__(self, *args, **kwargs):
        self._sharepoint_links = {}
        self._pending_sharepoint_requests = 0
        self._calendar_started = False

        super().__init__(*args, **kwargs)

    def start_requests(self):
        yield scrapy.Request(
            url=self.sharepoint_url,
            callback=self._sharepoint_request,
            errback=self._sharepoint_error,
            dont_filter=True,
        )

    def _sharepoint_request(self, response):
        self._pending_sharepoint_requests += 1
        yield self._sharepoint_list_request(self.folders_endpoint, self._parse_folders)

    def _sharepoint_list_request(self, url, callback, meta=None):
        return scrapy.Request(
            url=url,
            method="POST",
            headers=self.sharepoint_headers,
            body=self.sharepoint_body,
            callback=callback,
            errback=self._sharepoint_error,
            meta=meta or {},
            dont_filter=True,
        )

    def _sharepoint_error(self, failure):
        """Scrape the calendar without attachments if SharePoint is unavailable"""
        self.logger.error(
            "SharePoint request failed for %s: %s", failure.request.url, failure.value
        )
        if failure.request.url != self.sharepoint_url:
            self._pending_sharepoint_requests -= 1
        yield from self._start_calendar()

    def _parse_folders(self, response):
        try:
            data = response.json()
        except Exception as e:
            self.logger.error(
                "SharePoint folders response was not JSON: %s %s",
                e,
                response.text[:500],
            )
            self._pending_sharepoint_requests -= 1
            yield from self._start_calendar()
            return

        for item in data.get("Row", []):
            folder_name = item.get("FileLeafRef", "")
            if item.get("FSObjType") != "1" or not self.agenda_folder_re.match(
                folder_name
            ):
                continue

            root_folder = quote(item["FileRef"], safe="")
            self._pending_sharepoint_requests += 1
            yield self._sharepoint_list_request(
                self.items_endpoint.format(root_folder=root_folder),
                self._parse_folder_items,
                meta={"folder_name": folder_name},
            )

        self._pending_sharepoint_requests -= 1
        yield from self._start_calendar()

    def _parse_folder_items(self, response):
        folder_name = response.meta.get("folder_name", "")

        try:
            data = response.json()
        except Exception as e:
            self.logger.error(
                "SharePoint folder items not JSON for %s: %s %s",
                folder_name,
                e,
                response.text[:500],
            )
            self._pending_sharepoint_requests -= 1
            yield from self._start_calendar()
            return

        for date_key, link in self._parse_links(data.get("Row", [])):
            self._sharepoint_links.setdefault(date_key, []).append(link)

        # Folders over 30 items are paged; NextHref already includes RootFolder
        next_href = data.get("NextHref")
        if next_href:
            self._pending_sharepoint_requests += 1
            yield self._sharepoint_list_request(
                f"{self.sharepoint_list_url}&{next_href.lstrip('?')}",
                self._parse_folder_items,
                meta={"folder_name": folder_name},
            )

        self._pending_sharepoint_requests -= 1
        yield from self._start_calendar()

    def _start_calendar(self):
        if self._pending_sharepoint_requests > 0 or self._calendar_started:
            return

        self._calendar_started = True
        yield scrapy.Request(url=self.calendar_url, callback=self.parse)

    def parse(self, response):
        time_notes = self._parse_time_notes(response)

        # Each year's schedule is a <p><strong>YYYY</strong></p> followed by a table
        for year_strong in response.xpath(
            "//strong[re:test(normalize-space(.), '^\\d{4}$')]"
        ):
            year = year_strong.xpath("normalize-space(.)").get()
            table = year_strong.xpath(
                "./ancestor::p[1]/following-sibling::*[1][self::table]"
            )
            for cell in table.css("td"):
                start, notes = self._parse_cell(cell, year)
                if start is None:
                    continue

                links = self._sharepoint_links.get(start.strftime("%Y-%m-%d"), [])
                description = "\n\n".join(
                    part for part in [notes, self._description if links else ""] if part
                )

                meeting = Meeting(
                    title=self._title,
                    description=description,
                    classification=BOARD,
                    start=start,
                    end=None,
                    all_day=False,
                    time_notes=time_notes,
                    location=self._location,
                    links=links,
                    source=self.calendar_url,
                )
                meeting["status"] = self._get_status(meeting)
                meeting["id"] = self._get_id(meeting)
                yield meeting

    def _parse_time_notes(self, response):
        for paragraph in response.css("p"):
            text = " ".join(" ".join(paragraph.css("::text").getall()).split())
            match = re.search(r"On meeting days that include.*?following", text)
            if match:
                return match.group(0)
        return ""

    def _parse_cell(self, cell, year):
        """Parse a cell like "September 14  (changed from Sept 21)<br/>*Budget
        Workshop, FY27" into the meeting start and its notes. Returns (None, "")
        for an empty cell and raises SkamaniaEMSCalendarError when a cell with
        text has no readable date."""
        lines = [
            " ".join(text.split())
            for text in cell.css("::text").getall()
            if text.strip()
        ]
        if not lines:
            raise SkamaniaEMSCalendarError(
                f"{self.name}: empty schedule cell for {year}: {cell.get()!r}"
            )

        match = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2})\b(.*)$", lines[0])
        if not match:
            raise SkamaniaEMSCalendarError(
                f"{self.name}: no meeting date in {year} schedule cell: "
                f"{' / '.join(lines)!r}"
            )
        try:
            date = date_parser(f"{match.group(1)} {match.group(2)} {year}").date()
        except (ValueError, OverflowError) as e:
            raise SkamaniaEMSCalendarError(
                f"{self.name}: could not parse meeting date {lines[0]!r} "
                f"in {year} schedule"
            ) from e

        notes = [match.group(3).strip()] + [
            line.lstrip("*").strip() for line in lines[1:]
        ]
        return datetime.combine(date, self.start_time), " ".join(n for n in notes if n)

    def _parse_links(self, rows):
        for item in rows:
            raw_title = item.get("FileLeafRef", "")
            if item.get("FSObjType") != "0" or not raw_title.lower().endswith(".pdf"):
                continue
            date_key = self._parse_file_date(raw_title)
            if not date_key:
                continue
            href = item.get("ServerRedirectedEmbedUrl") or item.get("FileRef", "")
            if not href:
                continue
            yield date_key, {
                "href": urljoin(self.sharepoint_base_url, href),
                "title": self._clean_link_title(raw_title),
            }

    def _clean_link_title(self, filename):
        name = re.sub(r"\.[^.]+$", "", filename)
        cleaned = self.file_date_re.sub("", name).lstrip(" _")
        return cleaned or name

    def _parse_file_date(self, filename):
        match = self.file_date_re.match(filename.strip())
        if not match:
            return None
        month, day, year = match.groups()
        try:
            return datetime.strptime(f"20{year}-{month}-{day}", "%Y-%m-%d").strftime(
                "%Y-%m-%d"
            )
        except ValueError:
            self.logger.warning("Could not parse date from file name: %s", filename)
            return None
