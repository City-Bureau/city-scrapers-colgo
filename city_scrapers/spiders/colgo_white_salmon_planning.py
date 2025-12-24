from city_scrapers_core.constants import COMMISSION

from city_scrapers.mixins.white_salmon import WhiteSalmonMixin


class ColgoWhiteSalmonPlanningSpider(WhiteSalmonMixin):
    name = "colgo_white_salmon_planning"
    agency = "White Salmon Planning Commission"
    agency_id = "28"
    classification = COMMISSION
