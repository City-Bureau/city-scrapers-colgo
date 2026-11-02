import logging
from datetime import time

from city_scrapers.mixins.colgo_skamania import SkamaniaCountyMixin
from city_scrapers.mixins.colgo_skamania_ems import SkamaniaEMSMixin

logger = logging.getLogger(__name__)

_META_KEYS = {"class_name", "mixin"}

spider_configs = [
    {
        "class_name": "SkamaniaBoccSpider",
        "mixin": SkamaniaCountyMixin,
        "name": "colgo_ska_bocc",
        "agency": "Board of County Commissioners",
        "agency_name": "Skamania County Board of Commissioners",
        "agenda_param": "agendas-minutes-meeting-audio/-folder-36#docfold_2924_1241_328_36",  # noqa
        "time_notes": (
            "Please refer to the meeting agenda for more accurate meeting "
            "start time. The public is invited to attend the Skamania County "
            "Board of Commissioners (BOCC) meetings in person or remotely "
            "via Zoom. The Board holds its regular business meeting every "
            "Tuesday beginning at 9:30 a.m. in the boardroom on the lower "
            "level of the Skamania County Courthouse."
        ),
        "start_time": time(9, 30),
    },
    {
        "class_name": "SkamaniaBohSpider",
        "mixin": SkamaniaCountyMixin,
        "name": "colgo_ska_boh",
        "agency": "Board of Health",
        "agency_name": "Skamania County Board of Commissioners",
        "agenda_param": "board-of-health/-folder-162#docfold_2001_2047_350_162",
        "time_notes": (
            "Please refer to the meeting agenda for more accurate meeting "
            "start time. The Board of Health typically meets on the second "
            "Tuesday of each month in the Commissioners' Boardroom, located "
            "on the lower level of the Skamania County Courthouse."
        ),
        "start_time": time(13, 30),
    },
    {
        "class_name": "SkamaniaEmsbSpider",
        "mixin": SkamaniaEMSMixin,
        "name": "colgo_ska_emsb",
        "agency": "Skamania County Public Hospital District #1 (EMS & Rescue) Board of Commissioners",  # noqa
        "agenda_param": "",
        "time_notes": "",
        "start_time": time(10, 0),
        "is_main": True,
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
            mixin = config["mixin"]
            # Build attributes dict, dropping factory-only keys.
            # This is done to avoid passing keys that are only
            # used for the factory function to the class constructor.
            attrs = {k: v for k, v in config.items() if k not in _META_KEYS}

            # Dynamically create the spider class
            spider_class = type(
                class_name,
                (mixin,),
                attrs,
            )

            # Register the class in the global namespace using its class_name
            globals()[class_name] = spider_class


# Create all spider classes at module load
create_spiders()
