"""Diff pipeline with a guard against broken scrapes.

``city_scrapers_core.pipelines.DiffPipeline`` marks every previously-published
upcoming meeting that is missing from the current run as cancelled. When a scrape
breaks (the site is down, returns an empty page, or the markup changed) the run
yields few or no meetings, and every upcoming meeting is republished as cancelled.

``DiffGuardMixin`` adds a check that runs on ``spider_idle``, after every item the
spider yielded has gone through the pipeline and before the previous results are
re-injected. If the run looks broken, previous items that were not scraped this run
are passed through unchanged (same ``_id``, ``status`` and ``updated_at``), so the
published feed for that spider stays what the last good run produced.

The guard trips when either:

* the run scraped zero meetings and the previous feed had any, or
* the previous feed had at least ``CITY_SCRAPERS_DIFF_GUARD_MIN_PREVIOUS`` upcoming
  meetings and this run scraped fewer than
  ``CITY_SCRAPERS_DIFF_GUARD_MIN_RATIO`` times that many upcoming meetings.

When it trips, the pipeline logs an error and sends Scrapy's ``spider_error``
signal, so the status badge extension reports the spider as failing and the
``scrapy_sentry_errors`` extension sends the error to Sentry.

Written as a mixin so it can move into city-scrapers-core unchanged.
"""

import logging
from datetime import datetime
from typing import Mapping

from city_scrapers_core.items import Meeting
from city_scrapers_core.pipelines import AzureDiffPipeline, DiffPipeline
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from twisted.python.failure import Failure

logger = logging.getLogger(__name__)

DEFAULT_GUARD_ENABLED = True
DEFAULT_GUARD_MIN_RATIO = 0.5
DEFAULT_GUARD_MIN_PREVIOUS = 3


class DiffGuardTripped(Exception):
    """Reported through ``spider_error`` when the diff guard suppresses
    cancellations."""


def _now_str() -> str:
    # Same "now" and comparison DiffPipeline.process_item uses to decide whether a
    # previous item is in the past.
    return datetime.now().isoformat()[:19]


def _is_current_item(item) -> bool:
    # Mirrors the check at the top of DiffPipeline.process_item: items yielded by
    # the spider are Meeting objects or dicts without "_id"; re-injected previous
    # results are OCD dicts with "_id".
    return isinstance(item, Meeting) or (isinstance(item, dict) and "_id" not in item)


def _previous_scraper_id(item: Mapping) -> str:
    extras = item.get("extras") or item.get("extra") or {}
    return extras.get("cityscrapers/id") or extras.get("cityscrapers.org/id") or ""


def _start_str(item: Mapping) -> str:
    start = item.get("start", item.get("start_time"))
    if isinstance(start, datetime):
        return start.isoformat()
    return start or ""


class DiffGuardMixin:
    """Mixin for :class:`DiffPipeline` subclasses that skips cancel-marking when the
    current run looks broken. Must come before the DiffPipeline class in the MRO.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        pipeline = super().from_crawler(crawler)
        settings = crawler.settings
        pipeline.guard_enabled = settings.getbool(
            "CITY_SCRAPERS_DIFF_GUARD_ENABLED", DEFAULT_GUARD_ENABLED
        )
        pipeline.guard_min_ratio = settings.getfloat(
            "CITY_SCRAPERS_DIFF_GUARD_MIN_RATIO", DEFAULT_GUARD_MIN_RATIO
        )
        pipeline.guard_min_previous = settings.getint(
            "CITY_SCRAPERS_DIFF_GUARD_MIN_PREVIOUS", DEFAULT_GUARD_MIN_PREVIOUS
        )
        pipeline.scraped_upcoming_count = 0
        pipeline.guard_tripped = False
        return pipeline

    def process_item(self, item: Mapping, spider: Spider) -> Mapping:
        if _is_current_item(item):
            # Raises DropItem for duplicates, so only unique items are counted
            result = super().process_item(item, spider)
            if _start_str(result)[:19] >= _now_str():
                self.scraped_upcoming_count += 1
            return result
        if not self.guard_tripped:
            return super().process_item(item, spider)
        # Guard tripped: keep previous items that weren't scraped this run as they
        # were, past or upcoming, instead of dropping or cancelling them
        scraper_id = _previous_scraper_id(item)
        if scraper_id in spider._scraped_ids:
            raise DropItem("Previous item is in scraped results")
        spider._scraped_ids.add(scraper_id)
        return item

    def spider_idle(self, spider: Spider):
        # Scrapy only fires spider_idle once the scraper slot is idle, which means
        # every item the spider yielded has finished the item pipelines, so the
        # counts here are final for the current run.
        try:
            self._check_guard(spider)
        except Exception:
            logger.exception("Diff guard check failed for %s", spider.name)
        return super().spider_idle(spider)

    def _check_guard(self, spider: Spider):
        if not self.guard_enabled:
            return
        now_str = _now_str()
        previous = spider._previous_results
        previous_upcoming = sum(
            1 for item in previous if _start_str(item)[:19] >= now_str
        )
        scraped = len(spider._scraped_ids)
        scraped_upcoming = self.scraped_upcoming_count

        if scraped == 0 and len(previous) > 0:
            reason = "scraped zero meetings"
        elif (
            previous_upcoming >= self.guard_min_previous
            and scraped_upcoming < self.guard_min_ratio * previous_upcoming
        ):
            reason = (
                f"scraped upcoming meetings fell below {self.guard_min_ratio:.0%} "
                "of the previous feed"
            )
        else:
            return

        self.guard_tripped = True
        message = (
            f"Diff guard tripped for {spider.name}: {reason} "
            f"(scraped={scraped}, scraped_upcoming={scraped_upcoming}, "
            f"previous={len(previous)}, previous_upcoming={previous_upcoming}). "
            "Previous meetings are re-published unchanged instead of cancelled."
        )
        logger.error(message)
        self.crawler.stats.set_value("diff_guard/tripped", 1)
        self._report_guard_error(spider, message)

    def _report_guard_error(self, spider: Spider, message: str):
        """Send the spider_error signal so the status badge extension marks the
        spider failing and scrapy_sentry_errors reports it. send_catch_log logs
        handler exceptions instead of raising them."""
        try:
            raise DiffGuardTripped(message)
        except DiffGuardTripped:
            failure = Failure()
        self.crawler.signals.send_catch_log(
            signal=signals.spider_error,
            failure=failure,
            response=None,
            spider=spider,
        )


class GuardedAzureDiffPipeline(DiffGuardMixin, AzureDiffPipeline):
    """:class:`AzureDiffPipeline` with :class:`DiffGuardMixin`"""


class GuardedDiffPipeline(DiffGuardMixin, DiffPipeline):
    """Base :class:`DiffPipeline` with :class:`DiffGuardMixin`, for subclasses that
    implement ``load_previous_results``"""
