import re
from datetime import datetime, timedelta

import scrapy
from city_scrapers_core.constants import BOARD
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil import parser as dateparser


class SkamaniaCountyMixinMeta(type):
    """
    Metaclass that enforces the implementation of required static
    variables in child classes that inherit from the "Mixin" class.
    """

    def __init__(cls, name, bases, dct):
        required_static_vars = [
            "agency",
            "name",
            "agenda_param",
            "time_notes",
            "_start_time",
        ]
        missing_vars = [var for var in required_static_vars if var not in dct]

        if missing_vars:
            missing_vars_str = ", ".join(missing_vars)
            raise NotImplementedError(
                f"{name} must define the following static variable(s): "
                f"{missing_vars_str}."
            )

        super().__init__(name, bases, dct)


class SkamaniaCountyMixin(CityScrapersSpider, metaclass=SkamaniaCountyMixinMeta):
    name = None
    agency = None
    agenda_param = None
    time_notes = ""
    _start_time = datetime.min.time()

    timezone = "America/Los_Angeles"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
    }
    main_url = "https://www.skamaniacounty.org/departments-offices/commissioners"

    _seen_dates = set()
    _folder_year = None

    def _get_headers(self):
        """Return the request headers."""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",  # noqa
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Priority": "u=0, i",
            "Sec-Ch-Ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',  # noqa
            "Sec-Ch-Ua-Mobile": "?1",
            "Sec-Ch-Ua-Platform": '"Android"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36",  # noqa
        }

    def start_requests(self):
        self._seen_dates = set()
        self._folder_year = None
        yield scrapy.Request(
            f"{self.main_url}/{self.agenda_param}",
            callback=self.parse,
            headers=self._get_headers(),
        )

    def parse(self, response):
        for link in response.css("ul a.content_link"):
            text = link.css("::text").get("").strip()
            href = response.urljoin(link.attrib.get("href", ""))

            for bc_text in response.css(".document_breadcrumb a::text").getall():
                match = re.search(r"\d{4}", bc_text)
                if match:
                    self._folder_year = int(match.group())
            if "folder" in href:
                yield response.follow(
                    href,
                    callback=self.parse,
                    headers=self._get_headers(),
                )
            else:
                dates = self._extract_dates(text)
                for date in dates:
                    is_special = "special" in text.lower()
                    meeting_key = (date, is_special)
                    if meeting_key not in self._seen_dates:
                        self._seen_dates.add(meeting_key)

                        title = (
                            f"{self.agency} - Special Meeting"
                            if is_special
                            else self.agency
                        )

                        meeting = Meeting(
                            title=title,
                            description="",
                            classification=BOARD,
                            start=self._parse_start(date),
                            end=None,
                            all_day=False,
                            time_notes=self.time_notes,
                            location=self.location,
                            links=[
                                {
                                    "href": href,
                                    "title": "Agenda",
                                }
                            ],
                            source=self.main_url,
                        )

                        meeting["status"] = self._get_status(meeting, text)
                        meeting["id"] = self._get_id(meeting)

                        yield meeting

    def _extract_dates(self, text):
        meetings = []
        matches = re.findall(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text)
        for match in matches:
            try:
                parsed_date = dateparser.parse(match).date()

                if self._folder_year is not None:
                    parsed_date = parsed_date.replace(year=self._folder_year)
                meetings.append(parsed_date)
            except Exception:
                continue

        if "to" in text.lower() and len(meetings) == 2:
            start_date, end_date = meetings[0], meetings[1]
            meetings = []
            current_date = start_date
            while current_date <= end_date:
                meetings.append(current_date)
                current_date += timedelta(days=1)

        return meetings if meetings else []

    def _parse_start(self, date):
        return datetime.combine(date, self._start_time)
