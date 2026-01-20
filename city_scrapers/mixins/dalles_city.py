"""
Mixin for scraping The Dalles, Oregon city government meetings.

The Dalles uses the OmpNetwork platform (https://ompnetwork.org/) for hosting
video streaming and meeting management. This mixin handles the common API
patterns used across all Dalles government agencies.
"""

import re
from datetime import datetime, timezone

import scrapy
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider


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
    Agencies are identified by a category ID on the OmpNetwork platform.

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

    # The Dalles OmpNetwork site ID (constant for all Dalles agencies)
    site_id = "312"

    timezone = "America/Los_Angeles"
    base_url = "https://thedalles-oregon.ompnetwork.org"

    # API has a maximum limit of ~150 items per request, use 100 to be safe
    page_size = 100

    def start_requests(self):
        """
        This spider mixin fetches meeting data from The Dalles OmpNetwork API
        using a category ID which is specified in each child spider class.
        """
        api_url = self._build_api_url(start=0, limit=self.page_size)
        yield scrapy.Request(url=api_url, callback=self.parse)

    def _build_api_url(self, start=0, limit=100):
        """
        Build API URL with pagination parameters.

        Args:
            start: Offset for pagination
            limit: Number of items to fetch

        Returns:
            Full API URL with parameters
        """
        base_url = "https://thedalles-oregon.ompnetwork.org/api-cache"
        return (
            f"{base_url}/site/{self.site_id}/sessions"
            f"?category[]={self.category_id}&start={start}&limit={limit}"
        )

    @property
    def api_url(self):
        """Construct the initial API URL for the agency."""
        return self._build_api_url(start=0, limit=self.page_size)

    def parse(self, response):
        """
        Parse the API response and yield meeting items.

        This method handles pagination automatically to ensure all historical,
        current, and future meetings are scraped regardless of how many years
        pass or how many meetings accumulate in the system.

        Args:
            response: Scrapy response object containing JSON data

        Yields:
            Meeting items and follow-up requests for pagination
        """
        data = response.json()

        # Parse all meetings in current page
        for item in data.get("results", []):
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

            meeting["status"] = self._get_status(meeting)
            meeting["id"] = self._get_id(meeting)

            yield meeting

        # Handle pagination if there are more results
        current_start = data.get("start", 0)
        current_size = data.get("size", 0)
        total_size = int(data.get("totalSize", 0))

        # Check if there are more pages to fetch
        if current_start + current_size < total_size:
            next_start = current_start + current_size
            next_url = self._build_api_url(start=next_start, limit=self.page_size)
            yield scrapy.Request(url=next_url, callback=self.parse)

    def _parse_title(self, item):
        """Parse or generate meeting title."""
        title = item.get("title", "").strip()
        if not title:
            return self.agency
        # Clean up title by removing streaming-related suffixes
        # Use built-in _clean_title to remove common irrelevant information
        title = self._clean_title(title)
        # Remove OmpNetwork-specific suffixes using regex to catch all variants
        # Handles: " - Live Stream", "- Live Stream", "-Live Stream",
        #          " | Live Stream", "| Live Stream", "|Live Stream"
        title = re.sub(r"\s*[-|]\s*Live Stream$", "", title, flags=re.IGNORECASE).strip()
        return title

    def _parse_start(self, item):
        """
        Parse start datetime from Unix timestamp.

        The OmpNetwork API returns Unix timestamps that represent Pacific time
        directly (stored as if they were UTC). We interpret the timestamp as
        Pacific time and return a naive datetime object (for compatibility with
        city-scrapers-core's _get_status method which uses naive datetime.now()).

        Example: A meeting at 5:30 PM PST (timestamp: 1768239000) is stored as
        1768239000 which when decoded as UTC gives 2026-01-12 17:30:00. This
        UTC interpretation is actually the correct Pacific time.
        """
        timestamp = item.get("date")
        if timestamp:
            # The API stores Pacific time as Unix timestamp
            # Use timezone-aware conversion then strip timezone for naive datetime
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).replace(
                tzinfo=None
            )
        return None

    def _parse_links(self, item):
        """Parse meeting links (agendas, minutes, packets, videos)."""
        links = []

        # Add video link if available
        video_url = (item.get("video_url") or "").strip()
        if video_url:
            links.append({"href": video_url, "title": "Video"})

        # Add document links
        for doc in item.get("documents", []):
            doc_url = doc.get("url") or ""
            doc_url = doc_url.strip() if doc_url else ""
            doc_type = doc.get("type") or "Document"
            doc_type = doc_type.strip() if doc_type else "Document"
            if doc_url:
                links.append({"href": doc_url, "title": doc_type})

        return links

    def _parse_source(self, item):
        """Parse or generate source URL."""
        session_url = item.get("url") or ""
        session_url = session_url.strip() if session_url else ""
        if session_url:
            return f"https://thedalles-oregon.ompnetwork.org{session_url}"
        return self.api_url
