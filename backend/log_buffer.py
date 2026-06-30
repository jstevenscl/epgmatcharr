import collections
import logging

_buffer: collections.deque = collections.deque(maxlen=500)


class _MemHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append({
                "time":    self.formatTime(record, "%H:%M:%S"),
                "level":   record.levelname,
                "name":    record.name,
                "message": record.getMessage(),
            })
        except Exception:
            pass


def install() -> None:
    h = _MemHandler()
    h.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(h)


def get_logs() -> list[dict]:
    return list(_buffer)
