import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.datastructures import FileStorage
from PIL import Image

import src.qrcode_service as qrcode_service
from src.models import Base, Course, QRCodeUpload
from src.time_utils import now as business_now


class QRCodeFeatureTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _seed_course(self, course_id: str, *, check_in_method: str = "常规签到") -> Course:
        anchor = business_now().replace(second=0, microsecond=0)
        course = Course(
            id=course_id,
            name=f"课程 {course_id}",
            category="博雅课程-德育",
            location="学院路校区主南106",
            teacher="测试老师",
            campus="全部校区",
            start_time=anchor + timedelta(days=1),
            end_time=anchor + timedelta(days=1, hours=2),
            enroll_start=anchor - timedelta(hours=1),
            enroll_end=anchor + timedelta(days=1),
            check_in_method=check_in_method,
            sign_method="直接选课",
            capacity=80,
            enrolled=0,
        )
        self.session.add(course)
        self.session.commit()
        return course

    def _file_storage(self, name: str = "qr.png") -> FileStorage:
        seed = sum(ord(char) for char in name)
        stream = BytesIO()
        Image.new(
            "RGB",
            (32, 32),
            color=(seed % 255, (seed * 3) % 255, (seed * 7) % 255),
        ).save(stream, format="PNG")
        stream.seek(0)
        return FileStorage(
            stream=stream,
            filename=name,
            content_type="image/png",
        )

    def test_get_qrcode_course_context_returns_course_summary(self):
        self._seed_course("course-1")

        context = qrcode_service.get_qrcode_course_context(self.session, "course-1")

        self.assertIsNotNone(context)
        self.assertEqual("course-1", context["id"])
        self.assertEqual("课程 course-1", context["name"])
        self.assertEqual("常规签到", context["check_in_label"])
        self.assertIn(self.session.get(Course, "course-1").start_time.strftime("%Y-%m-%d %H:%M"), context["course_time"])

    def test_create_upload_and_list_are_scoped_by_course(self):
        self._seed_course("course-a")
        self._seed_course("course-b")

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            qrcode_service,
            "QRCODE_UPLOAD_ROOT",
            Path(tmpdir),
        ):
            qrcode_service.create_qrcode_upload(
                self.session,
                course_id="course-a",
                contributor_email="alice@example.com",
                contributor_subscriber_id=None,
                course_name="课程 A",
                course_time="2026-03-29 14:00 ~ 2026-03-29 16:00",
                course_location="主南106",
                notes="第一张",
                file_storage=self._file_storage("a.png"),
            )
            qrcode_service.create_qrcode_upload(
                self.session,
                course_id="course-b",
                contributor_email="alice@example.com",
                contributor_subscriber_id=None,
                course_name="课程 B",
                course_time="2026-03-30 14:00 ~ 2026-03-30 16:00",
                course_location="教0-003",
                notes="第二张",
                file_storage=self._file_storage("b.png"),
            )
            self.session.commit()
            self.assertEqual([], qrcode_service.list_public_qrcode_uploads(
                self.session,
                course_id="course-a",
            ))
            for upload in self.session.query(QRCodeUpload).all():
                upload.verification_status = "approved"
                upload.is_active = True
            self.session.commit()

        uploads = qrcode_service.list_public_qrcode_uploads(self.session, course_id="course-a")

        self.assertEqual(1, len(uploads))
        self.assertEqual("course-a", uploads[0]["course_id"])
        self.assertEqual("课程 A", uploads[0]["course_name"])
        self.assertEqual(1, uploads[0]["contributor_upload_count"])
        self.assertIn("@example.com", uploads[0]["masked_contributor_email"])
        self.assertNotIn("contributor_email", uploads[0])
        self.assertNotIn("verification_status", uploads[0])

    def test_leaderboard_supports_course_filter_and_period(self):
        self._seed_course("course-hot")
        self._seed_course("other-course")

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            qrcode_service,
            "QRCODE_UPLOAD_ROOT",
            Path(tmpdir),
        ):
            qrcode_service.create_qrcode_upload(
                self.session,
                course_id="course-hot",
                contributor_email="alice@example.com",
                contributor_subscriber_id=None,
                course_name="课程 Hot",
                course_time="2026-03-29 14:00 ~ 2026-03-29 16:00",
                course_location="主南106",
                notes="本周上传",
                file_storage=self._file_storage("hot1.png"),
            )
            qrcode_service.create_qrcode_upload(
                self.session,
                course_id="course-hot",
                contributor_email="alice@example.com",
                contributor_subscriber_id=None,
                course_name="课程 Hot",
                course_time="2026-03-29 14:00 ~ 2026-03-29 16:00",
                course_location="主南106",
                notes="更早上传",
                file_storage=self._file_storage("hot2.png"),
            )
            qrcode_service.create_qrcode_upload(
                self.session,
                course_id="other-course",
                contributor_email="bob@example.com",
                contributor_subscriber_id=None,
                course_name="其他课程",
                course_time="2026-03-30 14:00 ~ 2026-03-30 16:00",
                course_location="教0-003",
                notes="其他课程上传",
                file_storage=self._file_storage("other.png"),
            )
            for upload in self.session.query(QRCodeUpload).all():
                upload.verification_status = "approved"
                upload.is_active = True
            self.session.commit()

        older_upload = (
            self.session.query(QRCodeUpload)
            .filter_by(course_id="course-hot", notes="更早上传")
            .first()
        )
        older_upload.created_at = business_now() - timedelta(days=10)
        older_upload.updated_at = older_upload.created_at
        self.session.commit()

        weekly_board = qrcode_service.get_contributor_leaderboard(
            self.session,
            course_id="course-hot",
            period="weekly",
        )
        all_time_board = qrcode_service.get_contributor_leaderboard(
            self.session,
            course_id="course-hot",
            period="all",
        )

        self.assertEqual(1, len(weekly_board["items"]))
        self.assertEqual(1, weekly_board["items"][0]["upload_count"])
        self.assertEqual("累计", all_time_board["period_label"])
        self.assertEqual(2, all_time_board["items"][0]["upload_count"])

    def test_invalid_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            qrcode_service,
            "QRCODE_UPLOAD_ROOT",
            Path(tmpdir),
        ):
            with self.assertRaisesRegex(ValueError, "invalid image content"):
                qrcode_service.save_qrcode_file(
                    FileStorage(
                        stream=BytesIO(b"not an image"),
                        filename="not-image.png",
                        content_type="image/png",
                    )
                )

    def test_upload_for_missing_course_is_rejected_and_file_is_removed(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            qrcode_service,
            "QRCODE_UPLOAD_ROOT",
            Path(tmpdir),
        ):
            with self.assertRaisesRegex(ValueError, "invalid course"):
                qrcode_service.create_qrcode_upload(
                    self.session,
                    course_id="missing-course",
                    contributor_email="alice@example.com",
                    contributor_subscriber_id=None,
                    course_name="不存在的课程",
                    course_time="",
                    course_location="",
                    notes="",
                    file_storage=self._file_storage("missing.png"),
                )

            self.assertEqual([], [path for path in Path(tmpdir).rglob("*") if path.is_file()])

    def test_approved_qrcode_for_expired_course_is_not_public(self):
        course = self._seed_course("expired-course")
        course.end_time = business_now() - timedelta(hours=1)
        course.enroll_end = business_now() - timedelta(hours=2)
        self.session.commit()

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            qrcode_service,
            "QRCODE_UPLOAD_ROOT",
            Path(tmpdir),
        ):
            upload = qrcode_service.create_qrcode_upload(
                self.session,
                course_id=course.id,
                contributor_email="alice@example.com",
                contributor_subscriber_id=None,
                course_name=course.name,
                course_time="课程已结束",
                course_location=course.location,
                notes="过期课程",
                file_storage=self._file_storage("expired.png"),
            )
            upload.verification_status = "approved"
            upload.is_active = True
            self.session.commit()

            self.assertEqual([], qrcode_service.list_public_qrcode_uploads(
                self.session,
                course_id=course.id,
            ))

    def test_linked_upload_for_missing_course_is_not_public(self):
        upload = QRCodeUpload(
            course_id="deleted-course",
            contributor_email="alice@example.com",
            course_name="已删除课程",
            file_path="20260902/deleted.png",
            content_hash="a" * 64,
            verification_status="approved",
            is_active=True,
        )
        self.session.add(upload)
        self.session.commit()

        self.assertEqual([], qrcode_service.list_public_qrcode_uploads(self.session))


if __name__ == "__main__":
    unittest.main()
