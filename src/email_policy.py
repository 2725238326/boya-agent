"""Process-wide email policy; missing or invalid configuration fails closed."""

import os


def email_delivery_enabled(kind: str = "notify") -> bool:
    """Allow auth-only rollout without draining or broadcasting notifications."""
    if os.getenv("EMAIL_DELIVERY_ENABLED", "").strip().lower() != "true":
        return False
    scope = os.getenv("EMAIL_DELIVERY_SCOPE", "all").strip().lower()
    return scope == "all" or (scope == "auth" and kind in {"verify", "login"})
