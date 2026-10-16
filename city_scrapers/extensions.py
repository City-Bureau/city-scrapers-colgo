from scrapy import signals

MAX_ERROR_LINES = 5
MAX_ERROR_CHARS = 300


class StatsLine:
    """
    Print a one-line summary for each spider when it closes, plus a line per
    spider error (capped), to stdout:

        [error] colgo_x HoodRiverCalendarError: ...
        [stats] colgo_x items=42 responses=12 2xx=12 non_2xx=0 download_errors=0
        spider_errors=0 log_errors=0 finish_reason=finished

    Cron runs spiders with LOG_ENABLED=False (full logs are large, and Scrapy's
    feed export errors include the storage URI, credentials and all), so this
    is what shows each spider's result in the job output. Printing, rather than
    logging, keeps it visible whatever the log settings are.
    """

    def __init__(self, stats):
        self.stats = stats
        self.errors_printed = 0

    @classmethod
    def from_crawler(cls, crawler):
        ext = cls(crawler.stats)
        crawler.signals.connect(ext.spider_error, signal=signals.spider_error)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    def spider_error(self, failure, spider):
        self.errors_printed += 1
        if self.errors_printed <= MAX_ERROR_LINES:
            message = f"{failure.value.__class__.__name__}: {failure.value}"
            message = " ".join(message.split())[:MAX_ERROR_CHARS]
            print(f"[error] {spider.name} {message}", flush=True)
        elif self.errors_printed == MAX_ERROR_LINES + 1:
            print(f"[error] {spider.name} further errors not shown", flush=True)

    def spider_closed(self, spider, reason):
        print(self.format_line(spider.name, self.stats.get_stats(), reason), flush=True)

    @staticmethod
    def format_line(name, stats, reason):
        def total(prefix):
            return sum(v for k, v in stats.items() if k.startswith(prefix))

        status_prefix = "downloader/response_status_count/"
        responses = total(status_prefix)
        ok = total(status_prefix + "2")
        return (
            f"[stats] {name}"
            f" items={stats.get('item_scraped_count', 0)}"
            f" responses={responses}"
            f" 2xx={ok}"
            f" non_2xx={responses - ok}"
            f" download_errors={stats.get('downloader/exception_count', 0)}"
            f" spider_errors={total('spider_exceptions/')}"
            f" log_errors={stats.get('log_count/ERROR', 0)}"
            f" finish_reason={reason}"
        )
