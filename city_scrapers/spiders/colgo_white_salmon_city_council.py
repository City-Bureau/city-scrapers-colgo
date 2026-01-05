from city_scrapers_core.constants import CITY_COUNCIL

from city_scrapers.mixins.white_salmon import WhiteSalmonMixin


class ColgoWhiteSalmonCityCouncilSpider(WhiteSalmonMixin):
    name = "colgo_white_salmon_city_council"
    agency = "City Council of White Salmon"
    agency_id = "27"
    meeting_keyword = "city-council"
    classification = CITY_COUNCIL
