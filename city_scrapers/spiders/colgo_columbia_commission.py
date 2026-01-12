import datetime
import re

from city_scrapers_core.constants import CANCELLED, COMMITTEE
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider


class ColgoColumbiaCommissionSpider(CityScrapersSpider):
    name = "colgo_columbia_commission"
    agency = "Columbia River Gorge Commission"
    timezone = "America/Los_Angeles"
    base_url = "https://www.gorgecommission.org"
    start_urls = [
        "https://www.gorgecommission.org/about-crgc/commission-meetings",
        "https://www.gorgecommission.org/meeting/archived",
    ]

    time_notes = (
        "For meeting time and registration details, please check the Agenda."  # noqa
    )

    def parse(self, response):
        meetings = response.css("div.entry.clearfix > div.entry-c")
        for item in meetings:
            meeting = Meeting(
                title=self._parse_title(item),
                description="",
                classification=COMMITTEE,
                start=self._parse_time(item, "start"),
                end=self._parse_time(item, "end"),
                all_day=False,
                time_notes=self._parse_time_notes(item),
                location=self._parse_location(item),
                links=self._parse_links(item),
                source=response.url,
            )

            meeting["status"] = self._get_status(item, meeting)
            meeting["id"] = self._get_id(meeting)

            yield meeting

    def _parse_time_notes(self, item):
        meeting_start = self._parse_time(item, "start")
        return (
            self.time_notes
            if meeting_start and meeting_start > datetime.datetime.now()
            else ""
        )

    def _parse_location(self, item):
        location_text = (
            item.css("li i.icon-map-marker2").xpath("following-sibling::text()").get()
        )

        parts = location_text.split(" at ", 1)

        location = {
            "name": parts[1].strip(),
            "address": parts[0].strip(),
        }
        return location

    def _get_status(self, item, meeting, text=""):
        title_div = item.css(".entry-title a::text").get()
        if re.search(r"cancel\w+|rescheduled", title_div, re.IGNORECASE):
            return CANCELLED
        return super()._get_status(meeting, text)

    def _parse_title(self, item):
        title_div = item.css(".entry-title a::text").get()
        title = title_div.split(" - ")[0].strip()
        title = [
            word.capitalize() if word != "CRGC" else word for word in title.split()
        ]
        return " ".join(title)

    def _parse_time(self, item, flag):
        try:
            date_text = (
                item.css("li i.icon-calendar").xpath("following-sibling::text()").get()
            )
            time_text = (
                item.css("li i.icon-time").xpath("following-sibling::text()").get()
            )

            start_time = time_text.split("-")[0].strip()
            end_time = time_text.split("-")[1].strip()

            date_str = re.search(r"^(.*\d{4})", date_text.strip()).group(1)

            start_dt = datetime.datetime.strptime(
                f"{date_str} {start_time}", "%b %d, %Y %I:%M %p"
            )
            end_dt = datetime.datetime.strptime(
                f"{date_str} {end_time}", "%b %d, %Y %I:%M %p"
            )
            return start_dt if flag == "start" else end_dt
        except Exception:
            return None

    def _parse_links(self, item):
        links = item.css("a")[1:]
        if links:
            link_list = []
            for link in links:
                href = link.attrib.get("href")
                title = link.css("::text").get().strip()
                if title and href:
                    link_list.append({"href": f"{self.base_url}{href}", "title": title})
            return link_list
        return []
