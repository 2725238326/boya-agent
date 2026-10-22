"""Exercise real staging database, auth, fixtures and outbox without network."""

import asyncio
from datetime import timedelta
from email import policy
from email.parser import BytesParser
import os
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def exercise():
    from src import models, scheduler
    from src.notification_jobs import (
        enqueue_notification_job, drain_notification_jobs, claim_next_notification_job,
    )
    from src.push.email_push import deliver_email_notification_job
    from src.time_utils import now as business_now
    from web.app import app, _login_email_last_sent_at, _login_ip_last_sent_at

    # Every real socket connect is a test failure, including browser and SMTP.
    with patch("socket.create_connection", side_effect=AssertionError("external network")), patch(
        "socket.socket.connect", side_effect=AssertionError("external socket")
    ), patch.object(scheduler, "_ensure_browser", side_effect=AssertionError("real browser")):
        models.init_db()
        models.init_db()
        session = models.get_session()
        assert session.query(models.Course).count() == 0
        session.close()
        os.environ["STAGING_SCENARIO"] = "empty"
        result = await scheduler.run_scrape_task()
        assert result["success"], result
        os.environ["STAGING_SCENARIO"] = "healthy"
        result = await scheduler.run_scrape_task()
        assert result["success"], result
        session = models.get_session()
        before = session.query(models.Course).count()
        session.close()
        assert before == 3, before
        for scenario in ("auth_expired", "upstream_unavailable", "parse_failed", "timeout"):
            os.environ["STAGING_SCENARIO"] = scenario
            result = await scheduler.run_scrape_task()
            assert not result["success"], result
            session = models.get_session()
            assert session.query(models.Course).count() == before
            session.close()
        os.environ["STAGING_SCENARIO"] = "healthy"
        assert (await scheduler.run_scrape_task())["success"]
        print("PASS fixtures: empty, healthy, auth, upstream, parse, timeout, recovery; previous courses retained")

        client = app.test_client()
        os.environ["EMAIL_DELIVERY_SCOPE"] = "auth"
        address = "fixture@example.test"
        response = client.post("/api/subscribe", json={"email": address})
        assert response.status_code == 200, response.get_json()
        preview = next(Path(os.environ["MAIL_PREVIEW_DIR"]).glob("*.eml"))
        message = BytesParser(policy=policy.default).parsebytes(preview.read_bytes())
        html = message.get_body(preferencelist=("html",)).get_content()
        code = re.search(r">(\d{6})</p>", html).group(1)
        response = client.post("/api/subscribe/verify-code", json={"email": address, "code": code})
        assert response.status_code == 200 and response.get_json()["success"], response.get_json()
        assert client.get("/api/subscriber/session").status_code == 200
        assert client.post("/api/subscribe/verify-code", json={"email": address, "code": code}).status_code != 200
        session = models.get_session()
        sub = session.query(models.EmailSubscriber).filter_by(email=address).one()
        sub_id = sub.id
        assert not sub.verify_code
        # A notification cannot be claimed while only auth mail is enabled.
        job = enqueue_notification_job(session, channel="email", subscriber_id=sub_id,
                                       subscriber_email=address, course_ids=["fixture-open"])
        same = enqueue_notification_job(session, channel="email", subscriber_id=sub_id,
                                        subscriber_email=address, course_ids=["fixture-open"])
        assert same.id == job.id
        job_id = job.id
        session.commit()
        session.close()
        summary = await drain_notification_jobs({"email": deliver_email_notification_job})
        assert summary["claimed"] == 0, summary
        print("PASS auth: local MIME preview, verification, session, code replay rejected; auth scope leaves outbox untouched")

        os.environ["EMAIL_DELIVERY_SCOPE"] = "all"
        # Fault at the adapter boundary, not at the queue repository.
        with patch("src.runtime.write_preview_email", side_effect=OSError("fixture disk error")):
            summary = await drain_notification_jobs({"email": deliver_email_notification_job}, limit=1)
        assert summary["failed"] == 1, summary
        session = models.get_session()
        stored = session.get(models.NotificationJob, job_id)
        assert stored.attempts == 1 and stored.available_at > business_now()
        stored.available_at = business_now() - timedelta(seconds=1)
        session.commit()
        session.close()
        summary = await drain_notification_jobs({"email": deliver_email_notification_job}, limit=1)
        assert summary["succeeded"] == 1, summary
        assert (await drain_notification_jobs({"email": deliver_email_notification_job}))["claimed"] == 0

        session = models.get_session()
        recovered = enqueue_notification_job(session, channel="email", subscriber_id=sub_id,
                                             subscriber_email=address, course_ids=["fixture-upcoming"],
                                             event_type="recovery-fixture")
        session.commit()
        claimed = claim_next_notification_job(session, channels=["email"])
        assert claimed.id == recovered.id
        stored = session.get(models.NotificationJob, recovered.id)
        stored.locked_at = business_now() - timedelta(minutes=10)
        session.commit()
        session.close()
        # Closing sessions and reopening exercises a persisted expired lease.
        summary = await drain_notification_jobs({"email": deliver_email_notification_job}, limit=1)
        assert summary["succeeded"] == 1, summary
        print("PASS outbox: idempotency, failed file delivery, backoff, retry, expired lease recovery, no second successful claim")

        _login_email_last_sent_at.clear()
        _login_ip_last_sent_at.clear()
        os.environ["EMAIL_DELIVERY_SCOPE"] = "auth"
        previous_previews = set(Path(os.environ["MAIL_PREVIEW_DIR"]).glob("*.eml"))
        assert client.post("/api/login/request", json={"email": address}).status_code == 200
        new_previews = set(Path(os.environ["MAIL_PREVIEW_DIR"]).glob("*.eml")) - previous_previews
        assert len(new_previews) == 1
        login = BytesParser(policy=policy.default).parsebytes(new_previews.pop().read_bytes())
        text = login.get_body(preferencelist=("plain",)).get_content()
        login_code = re.search(r"本次验证码：\s*(\d{6})", text).group(1)
        fresh_client = app.test_client()
        assert fresh_client.get("/api/subscriber/session").status_code == 401
        response = fresh_client.post("/api/subscribe/verify-code", json={"email": address, "code": login_code})
        assert response.status_code == 200, response.get_json()
        assert fresh_client.get("/api/subscriber/session").status_code == 200
        assert client.get("/api/status").status_code == 401
        print("PASS login preview, fresh browser login, administrator boundary; no external connections")


def main():
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="ERROR", format="{level}: {message}")
    from src.runtime import configure_staging
    with tempfile.TemporaryDirectory(prefix="boya-staging-check-") as directory:
        configure_staging(Path(directory))
        try:
            asyncio.run(exercise())
        finally:
            from src.models import engine
            engine.dispose()


if __name__ == "__main__":
    main()
