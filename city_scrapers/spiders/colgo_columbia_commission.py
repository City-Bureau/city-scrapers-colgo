import re

from city_scrapers_core.constants import COMMITTEE
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil import parser as dateparser


class ColgoColumbiaCommissionSpider(CityScrapersSpider):
    name = "colgo_columbia_commission"
    agency = "Columbia River Gorge Commission"
    timezone = "America/Los_Angeles"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    start_urls = [
        "https://gorgecommission.org/home/past/",
        "https://gorgecommission.org/home/meetings/",
    ]

    def parse(self, response):
        detail_links = response.css("a.connect__meetings-link ::attr(href)").getall()
        for link in detail_links:
            yield response.follow(link, callback=self._parse_meeting)

    def _parse_meeting(self, item):
        title_text = item.css(".page-title ::text").get("")
        start, end = self._parse_time(item)
        meeting = Meeting(
            title=self._parse_title(title_text),
            description=self._parse_description(item),
            classification=COMMITTEE,
            start=start,
            end=end,
            all_day=False,
            time_notes="",
            location=self._parse_location(item),
            links=self._parse_links(item),
            source=item.url,
        )

        meeting["status"] = self._get_status(meeting, title_text)
        meeting["id"] = self._get_id(meeting)

        yield meeting

    def _parse_title(self, title_text):
        if not title_text:
            return ""
        title = title_text.replace("\u2013", "-").split(" - ")[0].strip()
        title = [
            word.capitalize() if word != "CRGC" else word for word in title.split()
        ]
        return " ".join(title)

    def _parse_description(self, item):
        desc_text = item.css("div.copy.copy-2.flow.default-content p::text").get()
        if desc_text:
            return desc_text.strip()
        return ""

    def _clean_text(self, selector, css):
        return (
            " ".join(t.strip() for t in selector.css(css).getall())
            .replace("\n", "")
            .strip()
        )

    def _parse_time(self, item):
        try:
            date_text = self._clean_text(item, "p.meeting__date ::text")
            time_text = self._clean_text(item, "p.meeting__time ::text")

            if not time_text:
                return dateparser.parse(f"{date_text}"), None

            end_time = None
            start_time = time_text.strip()

            if "-" in time_text or "to" in time_text:
                time_text = re.split(r"\s*[-to]+\s*", time_text)
                start_time = time_text[0].strip()
                end_time = time_text[1].strip()

            start_dt_obj = dateparser.parse(f"{date_text} {start_time}")
            end_dt_obj = None
            if end_time:
                end_dt_obj = dateparser.parse(f"{date_text} {end_time}")

            return start_dt_obj, end_dt_obj
        except Exception as e:
            self.logger.warning(f"Error parsing time: {e}")
            return None, None

    def _parse_location(self, item):
        location_text = self._clean_text(item, "p.meeting__location ::text")
        if not location_text:
            return {"name": "", "address": ""}

        # Pattern 1: "In person location: Name, Address"
        # Example: "In person location: Elk Ridge Golf Course,
        # 1 St. Martin's Springs Rd, Carson, Washington"
        pattern1 = r"In person location:\s*([^,]+),\s*(.+)"
        match = re.search(pattern1, location_text, re.IGNORECASE)

        if match:
            return {
                "name": match.group(1).strip(),
                "address": match.group(2).strip(),
            }

        # Pattern 2: "City, State at Venue and via zoom"
        # Example: "Cascade Locks, OR at the Gorge Pavilion and via zoom"
        pattern2 = r"(.+?)\s+at\s+(.+)"
        match = re.search(pattern2, location_text, re.IGNORECASE)

        if match:
            return {
                "name": match.group(2).strip(),
                "address": match.group(1).strip(),
            }

        return {"name": "", "address": ""}

    def _parse_links(self, item):
        links = []
        links_div = item.css(".meeting__agenda a, .meeting__files a")
        for link in links_div:
            attachment = {
                "href": link.attrib.get("href"),
                "title": link.css("span::text").get("").strip(),
            }
            if attachment["href"]:
                links.append(attachment)
        return links
