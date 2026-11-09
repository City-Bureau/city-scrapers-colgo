from city_scrapers_core.constants import BOARD, CITY_COUNCIL, COMMISSION, COMMITTEE

from city_scrapers.mixins.colgo_white_salmon import WhiteSalmonMixin

spider_configs = [
    {
        "class_name": "ColgoWhiteSalmonCityCouncilSpider",
        "name": "colgo_white_salmon_city_council",
        "agency": "City Council of White Salmon",
        "meeting_keyword": "city-council",
        "classification": CITY_COUNCIL,
        "default_description": (
            "The City Council of White Salmon is comprised of five elected council "
            "members. All positions are part-time, with each council member serving "
            "a four-year term and receiving a modest stipend for their service. "
            "The City Council meets twice monthly, on the first and third Wednesday "
            "evenings. Public meetings begin at 6:00 p.m. and are held in the City "
            "Council Chambers at the White Salmon Fire Hall, located at 119 NE "
            "Church Street, White Salmon, Washington. "
            "The City Council is responsible for adopting city ordinances and "
            "resolutions and for providing policy direction and approval for the "
            "actions of the Mayor and City staff. Each council member also serves "
            "as the City's representative on one or more standing committees and "
            "is responsible for reporting relevant information and recommendations "
            "back to the full Council. "
            "The City Council contracts with a part-time City Attorney, who "
            "provides legal guidance to ensure compliance with applicable laws "
            "and regulations in all City discussions, decisions, and actions."
        ),
    },
    {
        "class_name": "ColgoWhiteSalmonCivilServiceSpider",
        "name": "colgo_white_salmon_civil_service",
        "agency": "White Salmon Civil Service Commission",
        "meeting_keyword": "civil-service-commission",
        "classification": COMMISSION,
    },
    {
        "class_name": "ColgoWhiteSalmonPlanningSpider",
        "name": "colgo_white_salmon_planning",
        "agency": "White Salmon Planning Commission",
        "meeting_keyword": "planning-commission",
        "classification": COMMISSION,
    },
    {
        "class_name": "ColgoWhiteSalmonPersonnelFinanceSpider",
        "name": "colgo_white_salmon_personnel_finance",
        "agency": "White Salmon Personnel and Finance Committee",
        "meeting_keyword": "personnel-and-finance-committee",
        "classification": COMMITTEE,
    },
    {
        "class_name": "ColgoWhiteSalmonCityOperationsSpider",
        "name": "colgo_white_salmon_city_operations",
        "agency": "White Salmon City Operations Committee",
        "meeting_keyword": "city-operations-committee",
        "classification": COMMITTEE,
    },
    {
        "class_name": "ColgoWhiteSalmonCommunityDevelopmentSpider",
        "name": "colgo_white_salmon_community_development",
        "agency": "White Salmon Community Development Committee",
        "meeting_keyword": "community-development-committee",
        "classification": COMMITTEE,
    },
    {
        "class_name": "ColgoWhiteSalmonLodgingTaxSpider",
        "name": "colgo_white_salmon_lodging_tax",
        "agency": "White Salmon Lodging Tax Committee",
        "meeting_keyword": "lodging-tax-committee",
        "classification": COMMITTEE,
    },
    {
        "class_name": "ColgoWhiteSalmonTreeBoardSpider",
        "name": "colgo_white_salmon_tree_board",
        "agency": "White Salmon Tree Board",
        "meeting_keyword": "tree-board",
        "classification": BOARD,
    },
    {
        "class_name": "ColgoWhiteSalmonCityLABBoardSpider",
        "name": "colgo_white_salmon_citylab_board",
        "agency": "White Salmon CityLAB Board",
        "meeting_keyword": "citylab-board",
        "classification": BOARD,
    },
    {
        "class_name": "ColgoWhiteSalmonWildfireEmergencyPreparednessSpider",
        "name": "colgo_white_salmon_wildfire_emergency_preparedness",
        "agency": "White Salmon Wildfire and Emergency Preparedness Committee",
        "meeting_keyword": "wildfire-emergency-preparedness-committee",
        "classification": COMMITTEE,
    },

]


def _create_spiders():
    """
    Dynamically create spider classes using the spider_configs list
    and register them in the global namespace.
    """
    for config in spider_configs:
        class_name = config["class_name"]

        if class_name not in globals():
            attrs = {k: v for k, v in config.items() if k != "class_name"}

            spider_class = type(
                class_name,
                (WhiteSalmonMixin,),
                attrs,
            )

            globals()[class_name] = spider_class


_create_spiders()
