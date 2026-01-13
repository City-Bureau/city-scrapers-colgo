from city_scrapers.mixins.colgo_skamania import SkamaniaCountyMixin

_location = {
    "name": "Skamania County Courthouse",
    "address": "240 NW Vancouver Ave., Stevenson, WA 98648",
}

spider_configs = [
    {
        "class_name": "SkamaniaBoccSpider",
        "name": "colgo_ska_bocc",
        "agency": "Board of County Commissioners",
        "agenda_param": "agendas-minutes-meeting-audio/-folder-36#docfold_2924_1241_328_36",  # noqa
        "location": _location,
        "time_notes": (
            "The public is invited to attend the Skamania County Board of "
            "Commissioners (BOCC) meetings in person or remotely via Zoom. "
            "The Board holds its regular business meeting every Tuesday "
            "beginning at 9:30 a.m. in the boardroom on the lower level "
            "of the Skamania County Courthouse. Please refer to the meeting "
            "agenda for the meeting start time."
        ),
    },
    {
        "class_name": "SkamaniaBohSpider",
        "name": "colgo_ska_boh",
        "agency": "Board of Health",
        "agenda_param": "board-of-health/-folder-162#docfold_2001_2047_350_162",
        "location": _location,
        "time_notes": (
            "The Board of Health typically meets on the second Tuesday of "
            "each month in the Commissioners' Boardroom, located on the "
            "lower level of the Skamania County Courthouse. Please refer "
            "to the meeting agenda for the meeting start time."
        ),
    },
    {
        "class_name": "SkamaniaEmsbSpider",
        "name": "colgo_ska_emsb",
        "agency": "Board of EMS District #1",
        "agenda_param": "board-of-ems-district-1/-folder-619#docfold_2001_3132_1205_619",  # noqa
        "location": _location,
        "time_notes": (
            "The Skamania County Board of EMS District #1 meets annually on "
            "the second Tuesday of June in the Commissioners' boardroom on "
            "the bottom floor of the Skamania County Courthouse. Please refer "
            "to the meeting agenda for the meeting start time."
        ),
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
                (SkamaniaCountyMixin,),
                attrs,
            )

            # Register the class in the global namespace using its class_name
            globals()[class_name] = spider_class


# Create all spider classes at module load
create_spiders()
