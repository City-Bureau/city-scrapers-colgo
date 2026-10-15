"""Diff pipeline with a guard against broken scrapes.

``city_scrapers_core.pipelines.DiffPipeline`` marks every previously-published
upcoming meeting that is missing from the current run as cancelled. When a scrape
breaks (the site is down, returns an empty page, or the markup changed) the run
yields few or no meetings, and every upcoming meeting is republished as cancelled.

``DiffGuardMixin`` changes two things.

Waiting for the crawl to finish
-------------------------------
Core re-injects the previous feed on the first ``spider_idle`` signal. Every
``spider_idle`` receiver runs in the same pass, so if another receiver (a spider's
fallback, for example) schedules requests in that pass, core has already counted the
run, injected the previous feed and disconnected, and the fresh items are later
dropped as "already scraped". The mixin only acts on an idle call when the engine
is idle and no request was scheduled and no item was processed since the previous
idle call; otherwise it raises ``DontCloseSpider`` and waits for the next one. That
doesn't depend on receiver order. It costs one extra idle cycle (Scrapy's 5 second
heartbeat) per crawl.

The guard
---------
After that, the guard checks the run. It trips when any of these hold:

* the run scraped zero meetings and the previous feed had any,
* the run scraped zero upcoming meetings and the previous feed had any upcoming,
* the previous feed had at least ``CITY_SCRAPERS_DIFF_GUARD_MIN_PREVIOUS`` (3)
  upcoming meetings and this run scraped fewer than
  ``CITY_SCRAPERS_DIFF_GUARD_MIN_RATIO`` (0.5) times that many upcoming meetings,
* the check itself raised an exception (fail safe).

When it trips, previous items that were not scraped this run are passed through
unchanged (same ``_id``, ``status`` and ``updated_at``), past meetings included, so
the spider's output file, which is both what ``combinefeeds`` publishes and the next
run's baseline, stays what the last good run produced. The pipeline logs an error,
sets the ``diff_guard/tripped`` stat and sends Scrapy's ``spider_error`` signal, so
the status badge extension reports the spider as failing and
``scrapy_sentry_errors`` sends it to Sentry.

Things to know
--------------
* The guard is sticky. A tripped run republishes the old feed, which becomes the
  next run's baseline, so a genuine large drop (an agency really did cancel most of
  its meetings, or the site now lists fewer) keeps tripping on every run until
  someone looks at it.
* Escape hatch: to accept a genuine drop, run the spider once with the guard off,
  ``scrapy crawl <spider> -s CITY_SCRAPERS_DIFF_GUARD_ENABLED=False``, or set it in
  that spider's ``custom_settings`` until the new baseline has been published. The
  missing meetings are then marked cancelled as core always did.
* If a run trips the ratio rule while some meetings changed ID (for example the
  title changed, which changes the ID), the feed carries both the old item,
  unchanged, and the new one, so that meeting appears twice until the guard clears.

Written as a mixin so it can move into city-scrapers-core unchanged.
"""

import logging
from datetime import date, datetime
from typing import Mapping, Optional

from city_scrapers_core.items import Meeting
from city_scrapers_core.pipelines import AzureDiffPipeline, DiffPipeline
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DontCloseSpider, DropItem
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
    if isinstance(start, (datetime, date)):
        return start.isoformat()
    if isinstance(start, str):
        return start
    # Anything else is not counted as upcoming, which errs towards tripping
    return ""


def _is_upcoming(item: Mapping, now_str: str) -> bool:
    return _start_str(item)[:19] >= now_str


class DiffGuardMixin:
    """Mixin for :class:`DiffPipeline` subclasses that waits for the crawl to really
    finish before diffing, and skips cancel-marking when the run looks broken. Must
    come before the DiffPipeline class in the MRO.
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
        pipeline.activity_count = 0
        pipeline.idle_activity_count = None
        for signal in (
            signals.request_scheduled,
            signals.item_scraped,
            signals.item_dropped,
            signals.item_error,
        ):
            crawler.signals.connect(pipeline.record_activity, signal=signal)
        return pipeline

    def record_activity(self):
        self.activity_count += 1

    def process_item(self, item: Mapping, spider: Spider) -> Mapping:
        if _is_current_item(item):
            # Raises DropItem for duplicates, so only unique items are counted
            result = super().process_item(item, spider)
            try:
                if _is_upcoming(result, _now_str()):
                    self.scraped_upcoming_count += 1
            except Exception:
                logger.exception("Could not read start of %s", result.get("id"))
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

    def crawl_finished(self) -> bool:
        """True when the engine is idle and nothing was scheduled or processed since
        the previous idle call. The first idle call always returns False, because
        receivers connected after this one may still schedule requests in the same
        pass."""
        activity = self.activity_count
        quiet = activity == self.idle_activity_count
        self.idle_activity_count = activity
        return quiet and self.crawler.engine.spider_is_idle()

    def spider_idle(self, spider: Spider):
        if not self.crawl_finished():
            raise DontCloseSpider
        try:
            self._check_guard(spider)
        except Exception:
            logger.exception("Diff guard check failed for %s", spider.name)
            if not self.guard_tripped:
                self._trip(spider, "the guard check raised an exception")
        return super().spider_idle(spider)

    def _check_guard(self, spider: Spider):
        if not self.guard_enabled:
            return
        now_str = _now_str()
        previous = spider._previous_results
        previous_upcoming = sum(1 for item in previous if _is_upcoming(item, now_str))
        scraped = len(spider._scraped_ids)
        scraped_upcoming = self.scraped_upcoming_count

        if scraped == 0 and len(previous) > 0:
            reason = "scraped zero meetings"
        elif scraped_upcoming == 0 and previous_upcoming > 0:
            reason = "scraped zero upcoming meetings"
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
        self._trip(
            spider,
            reason,
            f"scraped={scraped}, scraped_upcoming={scraped_upcoming}, "
            f"previous={len(previous)}, previous_upcoming={previous_upcoming}",
        )

    def _trip(self, spider: Spider, reason: str, counts: Optional[str] = None):
        self.guard_tripped = True
        message = f"Diff guard tripped for {spider.name}: {reason}"
        if counts:
            message += f" ({counts})"
        message += (
            ". Previous meetings are re-published unchanged instead of cancelled."
        )
        logger.error(message)
        try:
            self.crawler.stats.set_value("diff_guard/tripped", 1)
            self._report_guard_error(spider, message)
        except Exception:
            logger.exception("Could not report diff guard error for %s", spider.name)

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
