"""
A Mixin & Mixin Meta for Hood River City scrapers that share the EventON calendar API.

Required class variables (enforced by metaclass):
    name (str): Spider name/slug (e.g., "colgo_hood_river_city_council")
    agency (str): Full agency name (e.g., "Hood River City Council")
    id (str): The event_type ID used to filter events from the EventON API.
        Each agency has a unique ID on the calendar.
"""

import re
from datetime import datetime, timezone

import scrapy
from city_scrapers_core.constants import (
    ADVISORY_COMMITTEE,
    BOARD,
    CITY_COUNCIL,
    COMMISSION,
    COMMITTEE,
    NOT_CLASSIFIED,
)
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from scrapy import FormRequest


class ColgoHoodRiverCityMixinMeta(type):
    """
    Metaclass that enforces the implementation of required static
    variables in child classes that inherit from the "Mixin" class.
    """

    def __init__(cls, name, bases, dct):
        required_static_vars = ["agency", "name", "id"]
        missing_vars = [var for var in required_static_vars if var not in dct]

        if missing_vars:
            missing_vars_str = ", ".join(missing_vars)
            raise NotImplementedError(
                f"{name} must define the following static variable(s): "
                f"{missing_vars_str}."
            )

        super().__init__(name, bases, dct)


class ColgoHoodRiverCityMixin(
    CityScrapersSpider, metaclass=ColgoHoodRiverCityMixinMeta
):
    """Mixin for Hood River City meeting spiders using EventON calendar API."""

    # Required to be overridden (enforced by metaclass)
    name = None
    agency = None
    id = None

    # Optional: filter events by title when id is empty/not available
    title_filter = None

    timezone = "America/Los_Angeles"
    start_urls = ["https://cityofhoodriver.gov/administration/meetings/"]
    location = {
        "name": "Hood River City Hall",
        "address": "211 2nd Street, Hood River, OR 97031",
    }

    # Enable cookies for this spider (needed for EventON API)
    custom_settings = {
        "COOKIES_ENABLED": True,
    }

    # Classification mapping based on meeting type
    classification_map = {
        "City Council": CITY_COUNCIL,
        "City Budget Committee": COMMITTEE,
        "City Tree Committee": COMMITTEE,
        "Planning Commission": COMMISSION,
        "Urban Renewal Advisory Committee": ADVISORY_COMMITTEE,
        "Urban Renewal Agency": COMMITTEE,
        "Landmark Review Board": BOARD,
        "Landmarks Review Board": BOARD,
        "Mayor's Equity Advisory Group": ADVISORY_COMMITTEE,
    }

    def start_requests(self):
        """Generate POST requests to EventON API for multiple year ranges."""
        # First, fetch video data from OMP Network API
        video_api_url = (
            "https://cityofhoodriver.ompnetwork.org/api-cache/"
            "site/1713/sessions?start=0&limit=100"
        )
        yield scrapy.Request(
            url=video_api_url,
            callback=self._parse_videos_first_page,
            dont_filter=True,
        )

    def _parse_videos_first_page(self, response):
        """Parse first page of videos and fetch remaining pages."""
        try:
            data = response.json()
            total_size = int(data.get("totalSize", 0))
            limit = 100

            # Initialize video cache with first page
            if not hasattr(self, "all_video_sessions"):
                self.all_video_sessions = []

            self.all_video_sessions.extend(data.get("results", []))

            # Fetch remaining pages
            start = limit
            while start < total_size:
                video_api_url = (
                    "https://cityofhoodriver.ompnetwork.org/api-cache/"
                    f"site/1713/sessions?start={start}&limit={limit}"
                )
                yield scrapy.Request(
                    url=video_api_url,
                    callback=self._parse_videos_additional_page,
                    dont_filter=True,
                    meta={"start": start, "total_size": total_size},
                )
                start += limit

            # If only one page, process immediately
            if total_size <= limit:
                yield from self._process_all_videos_and_fetch_events()

        except Exception as e:
            self.logger.error(f"Error parsing first video page: {e}")
            self.all_video_sessions = []
            yield from self._process_all_videos_and_fetch_events()

    def _parse_videos_additional_page(self, response):
        """Parse additional pages of videos."""
        try:
            data = response.json()
            total_size = response.meta.get("total_size", 0)

            self.all_video_sessions.extend(data.get("results", []))

            # Check if this is the last page
            if len(self.all_video_sessions) >= total_size:
                yield from self._process_all_videos_and_fetch_events()

        except Exception as e:
            self.logger.error(f"Error parsing additional video page: {e}")

    def _process_all_videos_and_fetch_events(self):
        """Process all collected videos and then fetch events."""
        videos = {}

        try:
            for session in self.all_video_sessions:
                video_url = session.get("video_url")
                if video_url:
                    # Store by date and title for matching
                    date_timestamp = session.get("date", 0)
                    if date_timestamp:
                        # Handle both string and int timestamps
                        if isinstance(date_timestamp, str):
                            date_timestamp = int(date_timestamp)
                        date_key = datetime.fromtimestamp(date_timestamp).strftime(
                            "%Y-%m-%d"
                        )
                        title = session.get("title", "").lower()

                        # Store video URL with metadata
                        video_info = {
                            "url": video_url,
                            "title": session.get("title", ""),
                            "date": date_key,
                            "video_id": session.get("video_id", ""),
                        }

                        # Create key from date and simplified title
                        key = f"{date_key}_{self._simplify_title(title)}"
                        videos[key] = video_info

            # Store in spider instance for later matching
            self.video_cache = videos

        except Exception as e:
            self.logger.error(f"Error processing videos: {e}")
            self.video_cache = {}

        # Now fetch events from EventON API
        api_url = "https://cityofhoodriver.gov/?evo-ajax=eventon_get_events"
        now = datetime.now()
        current_year = now.year

        # Fetch meetings from 2019 to 1 year in the future
        for year in range(2019, current_year + 2):
            form_data = self._build_form_data(start_month=1, start_year=year)
            yield FormRequest(
                url=api_url,
                formdata=form_data,
                callback=self.parse,
                headers={
                    "Referer": "https://cityofhoodriver.gov/administration/meetings/"
                },
                meta={"year": year},
            )

    def _simplify_title(self, title):
        """Simplify title for matching by removing common variations."""
        import re

        title = title.lower()
        # Remove common words and variations
        title = re.sub(r"\b(meeting|no\.?\s*\d+|session|#\d+)\b", "", title)
        # Remove extra whitespace and special chars
        title = re.sub(r"[^\w\s]", "", title)
        title = re.sub(r"\s+", "_", title.strip())
        return title[:50]  # Limit length

    def _build_form_data(self, start_month, start_year):
        """Build the form data for the EventON API request."""
        # Calculate Unix timestamps for the month range
        start_date = datetime(start_year, start_month, 1)
        # End date is 12 months from start
        end_year = start_year + 1 if start_month == 1 else start_year
        end_month = 12 if start_month == 1 else start_month - 1
        end_date = datetime(end_year, end_month, 28, 23, 59, 59)  # Safe end of month

        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        # Essential parameters only - removed 70+ UI/display parameters
        form_data = {
            # Core API parameters
            "ajaxtype": "jumper",
            "direction": "none",
            # Date range (CRITICAL)
            "shortcode[fixed_month]": str(start_month),
            "shortcode[fixed_year]": str(start_year),
            "shortcode[focus_start_date_range]": str(start_ts),
            "shortcode[focus_end_date_range]": str(end_ts),
            # Event filtering (CRITICAL)
            "shortcode[event_type]": self.id if self.id else "all",
            # Data retrieval settings (IMPORTANT)
            "shortcode[number_of_months]": "12",
            "shortcode[event_past_future]": "all",
            "shortcode[hide_past]": "no",
            "shortcode[hide_cancels]": "no",
            # Basic display (may affect HTML structure)
            "shortcode[event_order]": "ASC",
            "shortcode[sort_by]": "sort_date",
            "shortcode[calendar_type]": "default",
            # Version info
            "shortcode[_cver]": "4.5.3",
        }

        return form_data

    def parse(self, response):
        """
        Parse the EventON API JSON response.
        """
        try:
            data = response.json()
        except ValueError:
            self.logger.error(
                f"Failed to parse JSON response. "
                f"Status: {response.status}, Body: {response.text[:200]}"
            )
            return

        # Get HTML content from the response
        # The HTML is in data["html"][cal_id] where cal_id is the calendar ID
        html_content = None
        cal_id = "evcal_calendar_765"

        if "html" in data:
            if isinstance(data["html"], str):
                html_content = data["html"]
            elif isinstance(data["html"], dict):
                # Try to get HTML by calendar ID
                if cal_id in data["html"]:
                    html_content = data["html"][cal_id]
                else:
                    # Get first non-empty HTML value
                    for key, value in data["html"].items():
                        if isinstance(value, str) and value and key != "no_events":
                            html_content = value
                            break

        if not html_content:
            self.logger.warning(
                f"No HTML content found. html keys: {list(data.get('html', {}).keys())}"
            )
            return

        from scrapy import Selector

        selector = Selector(text=html_content)
        yield from self._parse_events(selector, response)

    def _parse_events(self, selector, response):
        """Parse events from the HTML content and validate links asynchronously."""
        for event in selector.css(
            ".eventon_list_event, .evo_eventcard, [data-event_id]"
        ):
            title = self._parse_title(event)
            if not title:
                continue

            if self.title_filter:
                title_lower = title.lower()
                if isinstance(self.title_filter, str):
                    if self.title_filter.lower() not in title_lower:
                        continue
                elif isinstance(self.title_filter, (list, tuple)):
                    if not any(
                        term.lower() in title_lower for term in self.title_filter
                    ):
                        continue

            start = self._parse_start(event)
            if not start:
                continue

            meeting = Meeting(
                title=title,
                description=self._parse_description(event),
                classification=self._parse_classification(title),
                start=start,
                end=self._parse_end(event),
                all_day=self._parse_all_day(event),
                time_notes=self._parse_time_notes(event),
                location=self._parse_location(event),
                links=self._parse_links(event),
                source=self._parse_source(response),
            )

            # Add video link
            meeting["links"] = self._add_video_link(meeting, meeting["links"])
            meeting["status"] = self._get_status(meeting, event)
            meeting["id"] = self._get_id(meeting)
            # Yield the meeting with all links included (no validation)
            yield meeting

    def _parse_title(self, item):
        """Parse or generate meeting title."""
        import re

        title_selectors = [
            ".evcal_event_title::text",
            ".evo_event_title::text",
            "[itemprop='name']::text",
            ".event_title::text",
            "span.evcal_desc2::text",
            "a::text",
        ]
        
        for selector in title_selectors:
            title = item.css(selector).get()
            if title:
                title = title.strip()
                # Remove date patterns - more aggressive approach
                # Match " - Month Day, Year" or " Month Day, Year"
                months = (
                    r"(January|February|March|April|May|June|July|"
                    r"August|September|October|November|December)"
                )
                title = re.sub(
                    rf"\s*[-–]\s*{months}\s+\d{{1,2}},?\s+\d{{4}}.*$",
                    "",
                    title,
                    flags=re.IGNORECASE,
                )
                # Also remove without dash if it's at the end
                title = re.sub(
                    rf"\s+{months}\s+\d{{1,2}},?\s+\d{{4}}.*$",
                    "",
                    title,
                    flags=re.IGNORECASE,
                )
                # Remove numeric date patterns
                title = re.sub(r"\s*[-–]\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*$", "", title)
                title = re.sub(r"\s+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*$", "", title)
                # Remove meeting numbers like "No. 1", "No. 2", "#1", etc.
                title = re.sub(r"\s+No\.?\s*\d+", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s+#\d+", "", title)
                title = re.sub(
                    r"\s+Meeting\s+\d+$", " Meeting", title, flags=re.IGNORECASE
                )
                return title.strip()

        # Try data attributes
        title = item.attrib.get("data-event_name", "")
        if title:
            title = title.strip()
            # Remove date patterns - more aggressive approach
            months = (
                r"(January|February|March|April|May|June|July|"
                r"August|September|October|November|December)"
            )
            title = re.sub(
                rf"\s*[-–]\s*{months}\s+\d{{1,2}},?\s+\d{{4}}.*$",
                "",
                title,
                flags=re.IGNORECASE,
            )
            title = re.sub(
                rf"\s+{months}\s+\d{{1,2}},?\s+\d{{4}}.*$",
                "",
                title,
                flags=re.IGNORECASE,
            )
            title = re.sub(r"\s*[-–]\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*$", "", title)
            title = re.sub(r"\s+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*$", "", title)
            # Remove meeting numbers like "No. 1", "No. 2", "#1", etc.
            title = re.sub(r"\s+No\.?\s*\d+", "", title, flags=re.IGNORECASE)
            title = re.sub(r"\s+#\d+", "", title)
            title = re.sub(r"\s+Meeting\s+\d+$", " Meeting", title, flags=re.IGNORECASE)
            return title.strip()

        return ""

    def _parse_description(self, item):
        """Parse or generate meeting description."""
        desc_selectors = [
            ".evcal_event_subtitle::text",
            ".evo_event_desc::text",
            "[itemprop='description']::text",
            ".event_description::text",
        ]
        for selector in desc_selectors:
            desc = item.css(selector).get()
            if desc:
                return desc.strip()
        return ""

    def _parse_classification(self, title):
        """Parse or generate classification from allowed options."""
        title_lower = title.lower()

        for key, classification in self.classification_map.items():
            if key.lower() in title_lower:
                return classification

        # Default classifications based on keywords
        if "council" in title_lower:
            return CITY_COUNCIL
        if "commission" in title_lower:
            return COMMISSION
        if "committee" in title_lower:
            return COMMITTEE
        if "board" in title_lower:
            return BOARD
        if "advisory" in title_lower:
            return ADVISORY_COMMITTEE

        return NOT_CLASSIFIED

    def _parse_start(self, item):
        """Parse start datetime as a naive datetime object."""
        data_time = item.attrib.get("data-time", "")
        if data_time and "-" in data_time:
            start_ts = data_time.split("-")[0]
            try:
                dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
                return dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        start_date = item.css(".evcal_desc span.evcal_desc2_time::text").get()
        if start_date:
            start_date = start_date.strip()
            return self._combine_date_time(start_date, "")

        return None

    def _parse_end(self, item):
        """Parse end datetime as a naive datetime object. Added by pipeline if None"""
        data_time = item.attrib.get("data-time", "")
        if data_time and "-" in data_time:
            end_ts = data_time.split("-")[1]
            try:
                dt = datetime.fromtimestamp(int(end_ts), tz=timezone.utc)
                return dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        return None

    def _parse_time_notes(self, item):
        """Parse any additional notes on the timing of the meeting"""
        return ""

    def _parse_all_day(self, item):
        """Parse or generate all-day status. Defaults to False."""
        all_day = item.attrib.get("data-all_day", "")
        return all_day.lower() == "yes" or all_day == "1"

    def _parse_location(self, item):
        """Parse or generate location."""
        location_selectors = [
            ".evo_location::text",
            ".evcal_location::text",
            "[itemprop='location']::text",
            ".event_location::text",
        ]
        for selector in location_selectors:
            loc = item.css(selector).get()
            if loc:
                loc = loc.strip()
                if "city hall" in loc.lower() or "211" in loc:
                    return self.location
                return {"name": loc, "address": ""}

        address_selector = "[itemprop='address']::text"
        address = item.css(address_selector).get()
        if address:
            return {"name": "", "address": address.strip()}

        return self.location

    def _normalize_url(self, url):
        """Normalize URL by ensuring it has a proper protocol."""
        if not url:
            return url

        url = url.strip()

        # Already has protocol
        if url.startswith(("http://", "https://")):
            return url

        # Protocol-relative URL
        if url.startswith("//"):
            return "https:" + url

        # Missing protocol - add https://
        if url.startswith(("www.", "youtube.com", "youtu.be", "cityofhoodriver.gov")):
            return "https://" + url

        # For other cases, assume https:// is needed
        if "." in url and not url.startswith("/"):
            return "https://" + url

        return url

    def _parse_links(self, item):
        """Collect agenda/minutes/packet links WITHOUT validating (async validation happens later)."""  # noqa
        links = []
        seen = set()

        link_selectors = [
            "a[href*='agenda']",
            "a[href*='minutes']",
            "a[href*='packet']",
            "a[href*='.pdf']",
            ".evcal_evdata_cell a",
            ".evo_event_links a",
        ]

        for selector in link_selectors:
            for link in item.css(selector):
                href = link.attrib.get("href", "")
                if not href:
                    continue

                if "google.com/calendar" in href or "eventon_ics_download" in href:
                    continue

                href = self._normalize_url(href)
                if not href or href in seen:
                    continue

                title = (link.css("::text").get() or "").strip()
                href_lower = href.lower()
                title_lower = title.lower()

                # Only keep agenda/minutes/packet
                if "agenda" in href_lower or "agenda" in title_lower:
                    if not title or title == href:
                        title = "Meeting Agenda"
                elif "minutes" in href_lower or "minutes" in title_lower:
                    if not title or title == href:
                        title = "Meeting Minutes"
                elif "packet" in href_lower or "packet" in title_lower:
                    if not title or title == href:
                        title = "Meeting Packet"
                else:
                    # skip anything else
                    continue

                seen.add(href)
                links.append({"href": href, "title": title})

        return links

    def _add_video_link(self, meeting, links):
        """Add video URL from OMP Network cache if available."""
        if not hasattr(self, "video_cache") or not self.video_cache:
            return links

        # Get meeting date and title for matching
        start_date = meeting.get("start")
        title = meeting.get("title", "")

        if not start_date or not title:
            return links

        # Create matching key
        date_key = start_date.strftime("%Y-%m-%d")
        simplified_title = self._simplify_title(title)
        match_key = f"{date_key}_{simplified_title}"

        # Try exact match first
        video_info = self.video_cache.get(match_key)

        # If no exact match, try fuzzy matching by date
        if not video_info:
            for key, info in self.video_cache.items():
                if key.startswith(date_key + "_"):
                    # Same date, check if titles are similar
                    cached_title_simplified = key.split("_", 1)[1] if "_" in key else ""

                    # Direct substring match
                    if (
                        cached_title_simplified in simplified_title
                        or simplified_title in cached_title_simplified  # noqa
                    ):
                        video_info = info
                        break

                    # Check for common important words in both
                    cached_words = set(re.findall(r"\b\w+\b", cached_title_simplified))
                    title_words = set(re.findall(r"\b\w+\b", simplified_title))
                    common_important_words = (
                        cached_words
                        & title_words
                        & {
                            "budget",
                            "city",
                            "council",
                            "committee",
                            "commission",
                            "planning",
                            "orientation",
                            "workshop",
                            "urban",
                            "renewal",
                        }
                    )

                    if (
                        len(common_important_words) >= 2
                    ):  # At least 2 important words match
                        video_info = info
                        break

        # Add video link if found
        if video_info:
            video_url = self._normalize_url(video_info["url"])
            if video_url not in {link["href"] for link in links}:
                links.append({"href": video_url, "title": "Video Recording"})

        return links

    def _parse_source(self, response):
        """Parse or generate source."""
        return "https://cityofhoodriver.gov/administration/meetings/"

    def _get_status(self, meeting, event=None):
        """
        Get meeting status, detecting cancelled meetings from EventON HTML.

        Args:
            meeting: Meeting dict with parsed data
            event: Scrapy selector for the event HTML element (optional)

        Returns:
            str: Status constant (CANCELLED, PASSED, or TENTATIVE)
        """
        from city_scrapers_core.constants import CANCELLED

        # Check for cancellation indicators in EventON HTML
        if event is not None:
            # Check for "cancelled" in CSS classes
            event_classes = event.attrib.get("class", "").lower()
            if "cancel" in event_classes:
                return CANCELLED

            # Check for cancellation in event status attribute
            event_status = event.attrib.get("data-event_status", "").lower()
            if "cancel" in event_status:
                return CANCELLED

            # Check for cancellation in event description or content
            event_html = event.get().lower()
            if "cancelled" in event_html or "canceled" in event_html:
                # Make sure it's not just in a link URL
                event_text = " ".join(event.css("::text").getall()).lower()
                if "cancelled" in event_text or "canceled" in event_text:
                    return CANCELLED

        # Fall back to default date-based status
        return super()._get_status(meeting)

    def _combine_date_time(self, date_str, time_str):
        """Combine date and time strings into datetime."""
        date_str = date_str.strip() if date_str else ""
        time_str = time_str.strip() if time_str else ""

        # Try ISO format first
        try:
            return datetime.fromisoformat(date_str.replace("Z", ""))
        except ValueError:
            pass

        # Try common date formats
        date_formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]
        parsed_date = None
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue

        if not parsed_date:
            return None

        # Try to parse time
        if time_str:
            time_str = re.sub(r"\s+", " ", time_str).strip()
            time_formats = [
                "%I:%M %p",
                "%I:%M%p",
                "%H:%M",
                "%I %p",
            ]
            for fmt in time_formats:
                try:
                    time_obj = datetime.strptime(time_str.upper(), fmt)
                    return datetime.combine(parsed_date.date(), time_obj.time())
                except ValueError:
                    continue

        return parsed_date
