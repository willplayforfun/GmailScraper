"""logging.Handler that enqueues LogRecords for the GUI to consume."""
import logging
import queue


class QueueHandler(logging.Handler):
    """
    Drops every LogRecord onto a queue as {"type": "log", "record": record}.
    Installed on the root logger by Worker.start(), removed by Worker.stop().
    """

    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait({"type": "log", "record": record})
        except queue.Full:
            pass  # drop rather than block the worker thread
