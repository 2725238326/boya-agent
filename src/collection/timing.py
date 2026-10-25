"""Per-attempt monotonic timings, isolated between asynchronous tasks."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import monotonic


_current = ContextVar("collection_stage_timings", default=None)


@contextmanager
def attempt_timings():
    values = {}
    token = _current.set(values)
    try:
        yield values
    finally:
        _current.reset(token)


@contextmanager
def measure_stage(name: str):
    started = monotonic()
    try:
        yield
    finally:
        values = _current.get()
        if values is not None:
            values[name] = values.get(name, 0) + max(0, int((monotonic() - started) * 1000))


def timed_stage(name: str):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with measure_stage(name):
                return await function(*args, **kwargs)
        return wrapped
    return decorate
