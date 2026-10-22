"""Upgrade an actual legacy SQLite table, preserving records across retries."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src import models


class DatabaseUpgradeTests(unittest.TestCase):
    def test_legacy_subscriber_survives_repeatable_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{(Path(directory) / 'legacy.db').as_posix()}")
            Session = sessionmaker(bind=engine)
            try:
                with engine.begin() as connection:
                    connection.execute(text("""
                        CREATE TABLE email_subscribers (
                            id INTEGER PRIMARY KEY, email VARCHAR NOT NULL UNIQUE,
                            token VARCHAR UNIQUE, verified BOOLEAN, active BOOLEAN,
                            categories_json TEXT, campus_filter VARCHAR,
                            self_sign_only BOOLEAN, created_at DATETIME
                        )
                    """))
                    connection.execute(text("""
                        INSERT INTO email_subscribers VALUES
                        (1, 'legacy@example.test', 'fixture-token', 1, 1, '[]', '学院路', 1, NULL)
                    """))
                with patch.object(models, "engine", engine), patch.object(models, "SessionLocal", Session):
                    models.init_db()
                    models.init_db()
                with Session() as session:
                    subscriber = session.get(models.EmailSubscriber, 1)
                    self.assertEqual(subscriber.email, "legacy@example.test")
                    self.assertTrue(subscriber.verified)
                    self.assertEqual(subscriber.campus_filter, "学院路")
                    self.assertEqual(subscriber.login_code_attempts, 0)
                    self.assertEqual(session.query(models.EmailSubscriber).count(), 1)
                inspector = inspect(engine)
                self.assertIn("notification_jobs", inspector.get_table_names())
                self.assertIn("ix_email_subscribers_last_portal_seen", {
                    index["name"] for index in inspector.get_indexes("email_subscribers")
                })
            finally:
                engine.dispose()
