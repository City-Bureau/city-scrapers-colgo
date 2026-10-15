import logging
from datetime import datetime

import pytest
import sentry_sdk
from city_scrapers_core.constants import CANCELLED, TENTATIVE
from city_scrapers_core.extensions.status import FAILING, RUNNING, StatusExtension
from city_scrapers_core.items import Meeting
from city_scrapers_core.pipelines import DiffPipeline
from freezegun import freeze_time
from scrapy import Spider, signals
from scrapy.exceptions import DontCloseSpider, DropItem
from scrapy.settings import Settings
from scrapy.signalmanager import SignalManager
from scrapy.statscollectors import MemoryStatsCollector
from scrapy_sentry_errors.extensions import Errors
from sentry_sdk.transport import Transport

from city_scrapers.pipelines import DiffGuardTripped, GuardedDiffPipeline

NOW = "2026-10-14 14:51:00"
SPIDER_NAME = "colgo_test_spider"


class InMemoryPreviousMixin:
    def load_previous_results(self):
        return [dict(item) for item in self.crawler._previous_feed]


class InMemoryGuardedDiffPipeline(InMemoryPreviousMixin, GuardedDiffPipeline):
    pass


class InMemoryCoreDiffPipeline(InMemoryPreviousMixin, DiffPipeline):
    pass


class FakeScraper:
    """Stands in for scrapy's Scraper: DiffPipeline.spider_idle passes each previous
    result to _process_spidermw_output, which runs it through the pipeline."""

    def __init__(self, pipeline, outputs):
        self.pipeline = pipeline
        self.outputs = outputs

    def _process_spidermw_output(self, item, request, response, spider):
        try:
            self.outputs.append(self.pipeline.process_item(item, spider))
        except DropItem:
            pass


class FakeEngine:
    scraper = None


class FakeCrawler:
    def __init__(self, previous_feed, settings=None):
        self.settings = Settings(
            {
                "ITEM_PIPELINES": {
                    "city_scrapers.pipelines.GuardedDiffPipeline": 200,
                    "city_scrapers_core.pipelines.MeetingPipeline": 300,
                    "city_scrapers_core.pipelines.OpenCivicDataPipeline": 400,
                },
                **(settings or {}),
            }
        )
        self.signals = SignalManager(self)
        self.stats = MemoryStatsCollector(self)
        self.spider = Spider(name=SPIDER_NAME)
        self.spider.timezone = "America/Los_Angeles"
        self.engine = FakeEngine()
        self._previous_feed = previous_feed


def previous_item(slug, start_time, status=TENTATIVE):
    """OCD item as stored in the previous feed"""
    scraper_id = f"{SPIDER_NAME}/{slug}"
    return {
        "_type": "event",
        "_id": f"ocd-event/{slug}",
        "updated_at": "2026-10-14T06:42:24-07:00",
        "name": f"Meeting {slug}",
        "status": status,
        "start_time": start_time,
        "extras": {"cityscrapers/id": scraper_id},
    }


def meeting(slug, start):
    return Meeting(
        id=f"{SPIDER_NAME}/{slug}",
        title=f"Meeting {slug}",
        start=start,
        status=TENTATIVE,
    )


UPCOMING = [
    previous_item("u1", "2026-10-15T17:30:00-07:00"),
    previous_item("u2", "2026-11-19T17:30:00-08:00"),
    previous_item("u3", "2026-12-17T17:30:00-08:00"),
    previous_item("u4", "2027-01-21T17:30:00-08:00"),
]
PAST = [
    previous_item("p1", "2026-09-17T17:30:00-07:00"),
    previous_item("p2", "2026-08-20T17:30:00-07:00", status=CANCELLED),
]


def run_pipeline(previous_feed, scraped, settings=None, pipeline_cls=None):
    """Run items through the pipeline the way a crawl does: scraped items first, then
    spider_idle re-injects the previous feed. Returns (outputs, pipeline, errors)."""
    pipeline_cls = pipeline_cls or InMemoryGuardedDiffPipeline
    crawler = FakeCrawler(previous_feed, settings)
    errors = []
    crawler.signals.connect(
        lambda failure: errors.append(failure),
        signal=signals.spider_error,
        weak=False,
    )
    pipeline = pipeline_cls.from_crawler(crawler)
    spider = crawler.spider
    outputs = []
    for item in scraped:
        try:
            outputs.append(pipeline.process_item(item, spider))
        except DropItem:
            pass
    crawler.engine.scraper = FakeScraper(pipeline, outputs)
    with pytest.raises(DontCloseSpider):
        pipeline.spider_idle(spider)
    return outputs, pipeline, errors


def previous_outputs(outputs):
    return {o["_id"]: o for o in outputs if isinstance(o, dict) and "_id" in o}


@freeze_time(NOW)
def test_zero_scraped_reemits_previous_feed_unchanged(caplog):
    previous_feed = UPCOMING + PAST
    with caplog.at_level(logging.ERROR, logger="city_scrapers.pipelines"):
        outputs, pipeline, errors = run_pipeline(previous_feed, [])

    # Every previous item, upcoming and past, comes back exactly as it was
    assert outputs == previous_feed
    assert pipeline.guard_tripped
    assert pipeline.crawler.stats.get_value("diff_guard/tripped") == 1

    assert len(errors) == 1
    assert isinstance(errors[0].value, DiffGuardTripped)
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert SPIDER_NAME in record.getMessage()
    assert "scraped=0" in record.getMessage()
    assert "previous_upcoming=4" in record.getMessage()


@freeze_time(NOW)
def test_core_diff_pipeline_cancels_on_zero_scrape():
    """Documents the behaviour being guarded against"""
    outputs, _, _ = run_pipeline(
        UPCOMING + PAST, [], pipeline_cls=InMemoryCoreDiffPipeline
    )
    assert [o["status"] for o in outputs] == [CANCELLED] * len(UPCOMING)


@freeze_time(NOW)
def test_healthy_run_cancels_single_missing_meeting():
    scraped = [
        meeting("u1", datetime(2026, 10, 15, 17, 30)),
        meeting("u2", datetime(2026, 11, 19, 17, 30)),
        meeting("u3", datetime(2026, 12, 17, 17, 30)),
        meeting("new", datetime(2027, 2, 18, 17, 30)),
        meeting("p1", datetime(2026, 9, 17, 17, 30)),
    ]
    outputs, pipeline, errors = run_pipeline(UPCOMING + PAST, scraped)

    assert not pipeline.guard_tripped
    assert errors == []
    assert pipeline.crawler.stats.get_value("diff_guard/tripped") is None

    # _id merged onto scraped items that were in the previous feed
    scraped_out = [o for o in outputs if isinstance(o, Meeting)]
    assert [o.get("_id") for o in scraped_out] == [
        "ocd-event/u1",
        "ocd-event/u2",
        "ocd-event/u3",
        None,
        "ocd-event/p1",
    ]
    # Only the missing upcoming meeting is re-emitted, marked cancelled; the missing
    # past meeting is dropped
    assert previous_outputs(outputs) == {
        "ocd-event/u4": {**UPCOMING[3], "status": CANCELLED}
    }


@freeze_time(NOW)
def test_sharp_drop_reemits_missing_meetings_unchanged():
    previous_feed = UPCOMING + PAST
    # 1 of 4 previous upcoming meetings scraped, below the default 50%
    scraped = [
        meeting("u1", datetime(2026, 10, 15, 17, 30)),
        meeting("p1", datetime(2026, 9, 17, 17, 30)),
    ]
    outputs, pipeline, errors = run_pipeline(previous_feed, scraped)

    assert pipeline.guard_tripped
    assert len(errors) == 1
    assert "scraped_upcoming=1" in str(errors[0].value)

    scraped_out = [o for o in outputs if isinstance(o, Meeting)]
    assert [o["_id"] for o in scraped_out] == ["ocd-event/u1", "ocd-event/p1"]
    # Items scraped this run are not duplicated; the rest pass through unchanged
    assert previous_outputs(outputs) == {
        item["_id"]: item
        for item in previous_feed
        if item["_id"] not in ("ocd-event/u1", "ocd-event/p1")
    }


@freeze_time(NOW)
def test_drop_at_ratio_boundary_still_cancels():
    # 2 of 4 is not below 50%
    scraped = [
        meeting("u1", datetime(2026, 10, 15, 17, 30)),
        meeting("u2", datetime(2026, 11, 19, 17, 30)),
    ]
    outputs, pipeline, errors = run_pipeline(UPCOMING, scraped)
    assert not pipeline.guard_tripped
    assert errors == []
    assert {k: v["status"] for k, v in previous_outputs(outputs).items()} == {
        "ocd-event/u3": CANCELLED,
        "ocd-event/u4": CANCELLED,
    }


@freeze_time(NOW)
def test_duplicate_scraped_items_counted_once():
    scraped = [meeting("u1", datetime(2026, 10, 15, 17, 30))] * 3
    outputs, pipeline, _ = run_pipeline(UPCOMING, scraped)
    assert pipeline.scraped_upcoming_count == 1
    assert pipeline.guard_tripped


@freeze_time(NOW)
def test_sharp_drop_ignored_below_min_previous():
    # Only 2 upcoming meetings in the previous feed, under the minimum of 3, and the
    # run did scrape something, so the missing meetings are cancelled as before
    scraped = [meeting("p1", datetime(2026, 9, 17, 17, 30))]
    outputs, pipeline, errors = run_pipeline(UPCOMING[:2] + PAST, scraped)
    assert not pipeline.guard_tripped
    assert errors == []
    assert {k: v["status"] for k, v in previous_outputs(outputs).items()} == {
        "ocd-event/u1": CANCELLED,
        "ocd-event/u2": CANCELLED,
    }


@freeze_time(NOW)
def test_zero_scraped_trips_even_below_min_previous():
    outputs, pipeline, errors = run_pipeline(UPCOMING[:1], [])
    assert pipeline.guard_tripped
    assert outputs == UPCOMING[:1]
    assert len(errors) == 1


@freeze_time(NOW)
def test_thresholds_are_configurable():
    scraped = [
        meeting("u1", datetime(2026, 10, 15, 17, 30)),
        meeting("u2", datetime(2026, 11, 19, 17, 30)),
    ]
    # 2 of 4 is below 75%
    _, pipeline, _ = run_pipeline(
        UPCOMING, scraped, settings={"CITY_SCRAPERS_DIFF_GUARD_MIN_RATIO": 0.75}
    )
    assert pipeline.guard_tripped
    # Ratio guard needs at least 5 previous upcoming meetings
    _, pipeline, _ = run_pipeline(
        UPCOMING,
        scraped[:1],
        settings={"CITY_SCRAPERS_DIFF_GUARD_MIN_PREVIOUS": 5},
    )
    assert not pipeline.guard_tripped


@freeze_time(NOW)
def test_guard_can_be_disabled():
    outputs, pipeline, errors = run_pipeline(
        UPCOMING, [], settings={"CITY_SCRAPERS_DIFF_GUARD_ENABLED": False}
    )
    assert not pipeline.guard_tripped
    assert errors == []
    assert [o["status"] for o in outputs] == [CANCELLED] * len(UPCOMING)


@freeze_time(NOW)
def test_zero_scraped_with_empty_previous_feed_does_not_trip():
    outputs, pipeline, errors = run_pipeline([], [])
    assert outputs == []
    assert not pipeline.guard_tripped
    assert errors == []


class RecordingStatusExtension(StatusExtension):
    def __init__(self, crawler):
        super().__init__(crawler)
        self.statuses = []

    def create_status_svg(self, spider, status):
        return status

    def update_status_svg(self, spider, svg):
        self.statuses.append(svg)


class RecordingTransport(Transport):
    events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            event = item.get_event()
            if event is not None:
                RecordingTransport.events.append(event)


@pytest.fixture
def sentry_events():
    RecordingTransport.events = []
    yield RecordingTransport.events
    sentry_sdk.init()


@freeze_time(NOW)
def test_guard_marks_status_failing_and_reports_to_sentry(sentry_events):
    crawler = FakeCrawler(UPCOMING)
    status_ext = RecordingStatusExtension.from_crawler(crawler)
    # Same wiring as scrapy_sentry_errors.extensions.Errors.from_crawler, with a
    # transport that records events instead of sending them
    sentry_ext = Errors(
        dsn="https://key@sentry.invalid/1",
        transport=RecordingTransport,
        default_integrations=False,
    )
    crawler.signals.connect(sentry_ext.spider_error, signal=signals.spider_error)

    pipeline = InMemoryGuardedDiffPipeline.from_crawler(crawler)
    outputs = []
    crawler.engine.scraper = FakeScraper(pipeline, outputs)
    with pytest.raises(DontCloseSpider):
        pipeline.spider_idle(crawler.spider)
    # Re-emitted items count as scraped, which alone would make the badge "running"
    crawler.stats.set_value("item_scraped_count", len(outputs))
    crawler.signals.send_catch_log(signal=signals.spider_closed)
    sentry_sdk.flush()

    assert status_ext.statuses == [FAILING]
    assert RUNNING not in status_ext.statuses
    assert len(sentry_events) == 1
    exception = sentry_events[0]["exception"]["values"][-1]
    assert exception["type"] == "DiffGuardTripped"
    assert SPIDER_NAME in exception["value"]


@freeze_time(NOW)
def test_guard_check_failure_falls_back_to_core_behaviour(caplog, monkeypatch):
    def broken_check(self, spider):
        raise RuntimeError("boom")

    monkeypatch.setattr(InMemoryGuardedDiffPipeline, "_check_guard", broken_check)
    outputs, pipeline, _ = run_pipeline(UPCOMING, [])
    assert "Diff guard check failed" in caplog.text
    assert [o["status"] for o in outputs] == [CANCELLED] * len(UPCOMING)
