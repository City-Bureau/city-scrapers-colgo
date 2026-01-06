import datetime
import re

from city_scrapers_core.constants import CANCELLED, COMMITTEE
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider


class ColgoColumbiaCommissionSpider(CityScrapersSpider):
    name = "colgo_columbia_commission"
    agency = "Columbia River Gorge Commission"
    timezone = "America/Chicago"
    base_url = "https://www.gorgecommission.org"
    start_urls = [
        "https://www.gorgecommission.org/about-crgc/commission-meetings",
        "https://www.gorgecommission.org/meeting/archived",
    ]

    time_notes = "Registration for the meeting is required. The link to register is located in the Agenda." # noqa

    location = {
        "name": "Zoom Webinar",
        "address": "White Salmon, WA",
    }

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
                time_notes=self.time_notes,
                location=self.location,
                links=self._parse_links(item),
                source=response.url,
            )

            meeting["status"] = self._get_status(item, meeting)
            meeting["id"] = self._get_id(meeting)

            yield meeting

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
        date_text = (
            item.css("li i.icon-calendar").xpath("following-sibling::text()").get()
        )
        time_text = item.css("li i.icon-time").xpath("following-sibling::text()").get()

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

    def _parse_links(self, item):
        links = item.css("a")[1:]
        if links:
            link_list = []
            for link in links:
                href = link.attrib.get("href")
                title = link.css("::text").get().strip()
                link_list.append({"href": f"{self.base_url}{href}", "title": title})
            return link_list
        return []
