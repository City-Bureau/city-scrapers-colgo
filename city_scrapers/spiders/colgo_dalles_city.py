"""
Spiders for The Dalles, Oregon meetings. Each config's calendar_title_pattern
selects its Revize calendar events; category_id selects its Open Media links.
"""

from city_scrapers_core.constants import BOARD, CITY_COUNCIL, COMMISSION, NOT_CLASSIFIED

from city_scrapers.mixins.colgo_dalles_city import DallesCityMixin

# Used when a calendar event has no location
default_location = {
    "name": "Location unavailable",
    "address": "Check source or agenda for location details",
}

# Configuration for each spider
spider_configs = [
    {
        "class_name": "ColgoDallesCityCouncilSpider",
        "name": "colgo_dalles_city_council",
        "agency": "The Dalles City Council",
        "category_id": "1",
        "calendar_title_pattern": r"city council",
        # The 2nd Monday City Council series has no location in the calendar
        "location": DallesCityMixin.city_hall,
        "time_notes": "",
        "_classification": CITY_COUNCIL,
        "is_main": True,
    },
    {
        "class_name": "ColgoDallesInformationalSpider",
        "name": "colgo_dalles_informational",
        "agency": "The Dalles Informational or Town Hall Meetings",
        "category_id": "2",
        "calendar_title_pattern": r"informational|town hall",
        "location": default_location,
        "time_notes": "",
        "_classification": NOT_CLASSIFIED,
    },
    {
        "class_name": "ColgoDallesPlanningCommissionSpider",
        "name": "colgo_dalles_planning_commission",
        "agency": "The Dalles Planning Commission",
        "category_id": "3",
        "calendar_title_pattern": r"planning commission",
        "location": default_location,
        "time_notes": "",
        "_classification": COMMISSION,
    },
    {
        "class_name": "ColgoDallesHistoricLandmarksSpider",
        "name": "colgo_dalles_historic_landmarks",
        "agency": "The Dalles Historic Landmarks Commission",
        "category_id": "4",
        "calendar_title_pattern": r"historic landmarks",
        "location": default_location,
        "time_notes": "",
        "_classification": COMMISSION,
    },
    {
        "class_name": "ColgoDallesUrbanRenewalSpider",
        "name": "colgo_dalles_urban_renewal",
        "agency": "The Dalles Urban Renewal Agency",
        "category_id": "5",
        "calendar_title_pattern": r"urban renewal",
        "location": default_location,
        "time_notes": "",
        "_classification": BOARD,
    },
]


def create_spiders():
    """
    Dynamically create spider classes using the spider_configs list
    and register them in the global namespace.
    """
    for config in spider_configs:
        class_name = config["class_name"]

        if class_name not in globals():
            # Build attributes dict without class_name to avoid duplication.
            # We make sure that the class_name is not already in the global namespace
            # Because some scrapy CLI commands like `scrapy list` will inadvertently
            # declare the spider class more than once otherwise
            attrs = {k: v for k, v in config.items() if k != "class_name"}

            # Dynamically create the spider class
            spider_class = type(
                class_name,
                (DallesCityMixin,),
                attrs,
            )

            # Register the class in the global namespace using its class_name
            globals()[class_name] = spider_class


# Create all spider classes at module load
create_spiders()
