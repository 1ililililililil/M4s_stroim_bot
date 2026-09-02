import logging
import time
from functools import wraps


log = logging.getLogger("app.performance")


def timed_command(name: str):
    def decorator(handler):
        @wraps(handler)
        async def wrapped(*args, **kwargs):
            started = time.perf_counter()
            log.info("PERF command=%s handler_started", name)
            try:
                return await handler(*args, **kwargs)
            finally:
                log.info(
                    "PERF command=%s duration_ms=%.2f",
                    name,
                    (time.perf_counter() - started) * 1000,
                )

        return wrapped

    return decorator


def duration_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000