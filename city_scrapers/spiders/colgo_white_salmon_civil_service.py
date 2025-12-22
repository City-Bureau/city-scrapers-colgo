from city_scrapers_core.constants import COMMISSION

from city_scrapers.mixins.white_salmon import WhiteSalmonMixin


class ColgoWhiteSalmonCivilServiceSpider(WhiteSalmonMixin):
    name = "colgo_white_salmon_civil_service"
    agency = "White Salmon Civil Service Commission"
    agency_id = "231"
    classification = COMMISSION
