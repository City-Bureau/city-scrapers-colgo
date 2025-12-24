"""
City of Stevenson, WA
This spider crawls the meetings page for the City of Stevenson, Washington.
"""

from city_scrapers.mixins.colgo_stevenson_city import ColgoStevensonCitySpiderMixin

# Common location used by multiple agencies
default_location = {
    "name": "Stevenson City Hall Council Chambers",
    "address": "7121 East Loop Road, Stevenson, WA 98648",
}

# Configuration for each spider
spider_configs = [
    {
        "class_name": "ColgoStevensonCitySpider",
        "name": "colgo_stevenson_city_council",
        "agency": "Stevenson City Council",
        "board_name": "City Council",
        "board_id": 27,
        "location": default_location,
        "description": "With the exception of executive session meetings, Council meetings are open to the public, with opportunity for the public to speak. For all comments and testimony, speakers are asked to limit statements to about three minutes in order to allow as many people as possible the chance to address Council.", # noqa
    },
    {
        "class_name": "ColgoStevensonPlanningSpider",
        "name": "colgo_stevenson_planning",
        "agency": "Stevenson Planning Commission",
        "board_name": "Planning Commission",
        "board_id": 28,
        "location": default_location,
        "description": "Commission meetings are open to the public, with opportunity for the public to speak. For all comments and testimony, speakers are asked to limit statements to about three minutes in order to allow as many people as possible the chance to address Commission.", # noqa
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
                (ColgoStevensonCitySpiderMixin,),
                attrs,
            )

            # Register the class in the global namespace using its class_name
            globals()[class_name] = spider_class


# Create all spider classes at module load
create_spiders()
