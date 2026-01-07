from city_scrapers_core.constants import CITY_COUNCIL

from city_scrapers.mixins.white_salmon import WhiteSalmonMixin


class ColgoWhiteSalmonCityCouncilSpider(WhiteSalmonMixin):
    name = "colgo_white_salmon_city_council"
    agency = "City Council of White Salmon"
    agency_id = "27"
    meeting_keyword = "city-council"
    classification = CITY_COUNCIL
    default_description = (
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
    )
