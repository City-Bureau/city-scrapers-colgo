"""
This file dynamically creates spider classes for the spider factory
mixin that agencies use.
"""

from city_scrapers.mixins.colgo_hood_river_city import ColgoHoodRiverCityMixin

"""
Hood River City Council

- City Budget Committee
- City Council
- City Tree Committee
- Planning Commission
- Urban Renewal Advisory Committee
- Urban Renewal Agency Meetings
- Landmark Review Board Meetings
- Mayor's Equity Advisory Group
"""

spider_configs = [
    {
        "class_name": "ColgoHoodRiverCityBudgetCommitteeSpider",
        "name": "colgo_hood_river_city_budget_committee",
        "agency": "Hood River City Budget Committee",
        "id": "",
        "title_filter": "Budget",
    },
    {
        "class_name": "ColgoHoodRiverCityCouncilSpider",
        "name": "colgo_hood_river_city_council",
        "agency": "Hood River City Council",
        "id": "",
        "title_filter": "City Council",
    },
    {
        "class_name": "ColgoHoodRiverTreeCommitteeSpider",
        "name": "colgo_hood_river_tree_committee",
        "agency": "Hood River City Tree Committee",
        "id": "",
        "title_filter": "Tree Committee",
    },
    {
        "class_name": "ColgoHoodRiverPlanningCommissionSpider",
        "name": "colgo_hood_river_planning_commission",
        "agency": "Hood River Planning Commission",
        "id": "",
        "title_filter": "Planning Commission",
    },
    {
        "class_name": "ColgoHoodRiverUrbanRenewalAdvisorySpider",
        "name": "colgo_hood_river_urban_renewal_advisory",
        "agency": "Hood River Urban Renewal Advisory Committee",
        "id": "",
        "title_filter": "Urban Renewal Advisory",
    },
    {
        "class_name": "ColgoHoodRiverUrbanRenewalAgencySpider",
        "name": "colgo_hood_river_urban_renewal_agency",
        "agency": "Hood River Urban Renewal Agency",
        "id": "",
        "title_filter": "Urban Renewal Agency",
    },
    {
        "class_name": "ColgoHoodRiverLandmarkReviewSpider",
        "name": "colgo_hood_river_landmark_review",
        "agency": "Hood River Landmark Review Board",
        "id": "",
        "title_filter": "Landmark",
    },
    {
        "class_name": "ColgoHoodRiverEquityAdvisorySpider",
        "name": "colgo_hood_river_equity_advisory",
        "agency": "Hood River Mayor's Equity Advisory Group",
        "id": "",
        "title_filter": "Equity Advisory",
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
                (ColgoHoodRiverCityMixin,),
                attrs,
            )

            globals()[class_name] = spider_class


# Create all spider classes at module load
create_spiders()
