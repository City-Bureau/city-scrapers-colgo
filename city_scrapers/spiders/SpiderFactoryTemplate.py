"""
This file dynamically creates spider classes for the spider factory mixin that agencies use.
"""

from city_scrapers.mixins.SpiderFactoryTemplate import SpiderfactorytemplateMixin

spider_configs = [
    {
        "class_name": "ExampleSpider1",
        "name": "example_spider_1",
        "agency": "Example Agency 1",
        "id": "example_agency_1",
    },
    {
        "class_name": "ExampleSpider2",
        "name": "example_spider_2",
        "agency": "Example Agency 2",
        "id": "example_agency_2",
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
                (SpiderfactorytemplateMixin,),
                attrs,
            )

            globals()[class_name] = spider_class


# Create all spider classes at module load
create_spiders()