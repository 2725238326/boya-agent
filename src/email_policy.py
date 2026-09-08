"""Process-wide email policy; missing or invalid configuration fails closed."""

import os


def email_delivery_enabled() -> bool:
    return os.getenv("EMAIL_DELIVERY_ENABLED", "").strip().lower() == "true"
