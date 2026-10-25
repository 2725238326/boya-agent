"""Failure policy without browser, database or scheduler dependencies."""

from src.scrape_outcome import ScrapeStatus


def requires_browser_reset(status: str, metadata: dict | None = None) -> bool:
    """Keep healthy sessions for parsing/transport errors; retry is bounded above."""
    return status == ScrapeStatus.AUTH_EXPIRED.value or bool((metadata or {}).get("browser_disconnected"))


def browser_disconnected(page, error: Exception) -> bool:
    try:
        if callable(getattr(page, "is_closed", None)) and page.is_closed() is True:
            return True
        browser = page.context.browser
        if browser is not None and browser.is_connected() is False:
            return True
    except (AttributeError, TypeError):
        pass
    except Exception:
        return True
    message = str(error).lower()
    return any(marker in message for marker in (
        "target page, context or browser has been closed", "browser has been closed",
        "connection closed while reading from the driver", "browser disconnected",
    ))
