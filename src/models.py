"""
SQLAlchemy 鏁版嵁妯″瀷 - 璇剧▼淇℃伅 & 绛涢€夐厤缃?
"""

import json
import os
import time
import secrets
from pathlib import Path
from sqlalchemy import inspect, text, event
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from src.course_state import (
    course_fill_ratio,
    get_check_in_display_label,
    get_hot_reason,
    is_enrollment_open,
    is_course_expired,
    is_hot_course,
    is_self_check_in,
)
from src.time_utils import now as business_now

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    str(Path(__file__).resolve().parents[1] / "boya_agent.db"),
)

Base = declarative_base()
engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    echo=False,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,
    },
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


class Course(Base):
    """鍗氶泤璇剧▼淇℃伅"""
    __tablename__ = "courses"

    id = Column(String, primary_key=True)  # 鍚嶇О+鏃堕棿 hash
    name = Column(String, nullable=False)
    category = Column(String, default="")
    location = Column(String, default="")
    teacher = Column(String, default="")
    college = Column(String, default="")       # 瀛﹂櫌
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    enroll_start = Column(DateTime, nullable=True)
    enroll_end = Column(DateTime, nullable=True)
    sign_method = Column(String, default="")     # 鑷富绛捐 / 闈炶嚜涓荤璇?
    capacity = Column(Integer, default=0)
    enrolled = Column(Integer, default=0)
    status = Column(String, default="")         # 鍙€?/ 宸叉弧
    campus = Column(String, default="")
    open_college = Column(String, default="")   # 寮€鏀惧闄?
    open_grade = Column(String, default="")     # 寮€鏀惧勾绾?
    open_group = Column(String, default="")     # 寮€鏀句汉缇?
    has_homework = Column(String, default="")   # 璇剧▼浣滀笟
    check_in_method = Column(String, default="")  # 绛惧埌鏂瑰紡锛堣鎯呴〉锛?
    description = Column(Text, default="")        # 璇剧▼浠嬬粛锛堣鎯呴〉锛?
    organizer = Column(String, default="")        # 璇剧▼缁勭粐璐熻矗浜?

    first_seen = Column(DateTime, default=business_now)
    last_seen = Column(DateTime, default=business_now)
    pushed = Column(Boolean, default=False)
    expired = Column(Boolean, default=False)  # 閫夎宸叉埅姝?澶辨晥
    enrolled_by_bot = Column(Boolean, default=False)  # 鏄惁琚嚜鍔ㄩ€夎

    @property
    def remaining(self) -> int:
        return max(0, self.capacity - self.enrolled)

    @property
    def is_enrollable(self) -> bool:
        return (
            self.enroll_start is not None
            and self.enroll_end is not None
            and is_enrollment_open(self, business_now())
            and self.remaining > 0
        )

    def to_dict(self) -> dict:
        now = business_now()
        seconds_since_last_seen = None
        if self.last_seen:
            seconds_since_last_seen = max(0, int((now - self.last_seen).total_seconds()))
        fill_ratio = course_fill_ratio(self)
        hot_reason = get_hot_reason(self, now)
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "location": self.location,
            "teacher": self.teacher,
            "college": self.college,
            "start_time": self.start_time.strftime("%Y-%m-%d %H:%M") if self.start_time else "",
            "end_time": self.end_time.strftime("%Y-%m-%d %H:%M") if self.end_time else "",
            "enroll_start": self.enroll_start.strftime("%Y-%m-%d %H:%M") if self.enroll_start else "",
            "enroll_end": self.enroll_end.strftime("%Y-%m-%d %H:%M") if self.enroll_end else "",
            "sign_method": self.sign_method,
            "capacity": self.capacity,
            "enrolled": self.enrolled,
            "remaining": self.remaining,
            "fill_ratio": round(fill_ratio, 4),
            "fill_percent": round(fill_ratio * 100, 1),
            "status": self.status,
            "campus": self.campus,
            "check_in_method": self.check_in_method,
            "display_check_in_method": get_check_in_display_label(self),
            "is_self_check_in": is_self_check_in(self),
            "is_enrollable": self.is_enrollable,
            "enrollment_open": is_enrollment_open(self, now),
            "is_hot_course": is_hot_course(self, now),
            "hot_reason": hot_reason,
            "pushed": self.pushed,
            "expired": is_course_expired(self, now),
            "first_seen": self.first_seen.strftime("%Y-%m-%d %H:%M") if self.first_seen else "",
            "last_seen": self.last_seen.strftime("%Y-%m-%d %H:%M:%S") if self.last_seen else "",
            "last_seen_seconds_ago": seconds_since_last_seen,
        }


class FilterConfig(Base):
    """User filter configuration."""
    __tablename__ = "filter_config"

    id = Column(Integer, primary_key=True, default=1)
    categories_json = Column(Text, default="[]")         # 閫変腑鐨勭被鍒垪琛?
    self_sign_only = Column(Boolean, default=True)        # 浠呰嚜涓荤鍒?
    strict_boya_only = Column(Boolean, default=False)     # 涓ユ牸鍗氶泤瑙勫垯锛氶潪鏍″尰闄?+ 鑷富绛惧埌
    min_remaining = Column(Integer, default=1)            # 鏈€灏戝墿浣欏悕棰?
    campus_filter = Column(String, default="")            # 鏍″尯杩囨护
    keyword_whitelist_json = Column(Text, default="[]")   # 鍏抽敭璇嶇櫧鍚嶅崟
    keyword_blacklist_json = Column(Text, default="[]")   # 鍏抽敭璇嶉粦鍚嶅崟

    # 鑷姩閫夎璁剧疆
    auto_enroll_enabled = Column(Boolean, default=False)
    priority_keywords_json = Column(Text, default="[]")   # 鎰忔効浼樺厛绾у叧閿瘝
    confirm_before_enroll = Column(Boolean, default=True)
    max_auto_enroll_per_day = Column(Integer, default=2)

    # 鎺ㄩ€佽缃?
    telegram_enabled = Column(Boolean, default=False)
    email_enabled = Column(Boolean, default=False)
    rss_enabled = Column(Boolean, default=True)
    daily_summary_enabled = Column(Boolean, default=False)   # 鏄惁鍚敤姣忔棩姹囨€绘帹閫?
    daily_summary_time = Column(String, default="21:00")     # 姣忔棩姹囨€绘帹閫佹椂闂达紙HH:MM锛?

    # 璋冨害璁剧疆
    interval_minutes = Column(Integer, default=10)

    @property
    def categories(self) -> list:
        return json.loads(self.categories_json)

    @categories.setter
    def categories(self, value: list):
        self.categories_json = json.dumps(value, ensure_ascii=False)

    @property
    def keyword_whitelist(self) -> list:
        return json.loads(self.keyword_whitelist_json)

    @keyword_whitelist.setter
    def keyword_whitelist(self, value: list):
        self.keyword_whitelist_json = json.dumps(value, ensure_ascii=False)

    @property
    def keyword_blacklist(self) -> list:
        return json.loads(self.keyword_blacklist_json)

    @keyword_blacklist.setter
    def keyword_blacklist(self, value: list):
        self.keyword_blacklist_json = json.dumps(value, ensure_ascii=False)

    @property
    def priority_keywords(self) -> list:
        return json.loads(self.priority_keywords_json)

    @priority_keywords.setter
    def priority_keywords(self, value: list):
        self.priority_keywords_json = json.dumps(value, ensure_ascii=False)

    def to_dict(self) -> dict:
        return {
            "categories": self.categories,
            "self_sign_only": self.self_sign_only,
            "strict_boya_only": self.strict_boya_only,
            "min_remaining": self.min_remaining,
            "campus_filter": self.campus_filter,
            "keyword_whitelist": self.keyword_whitelist,
            "keyword_blacklist": self.keyword_blacklist,
            "auto_enroll_enabled": self.auto_enroll_enabled,
            "priority_keywords": self.priority_keywords,
            "confirm_before_enroll": self.confirm_before_enroll,
            "max_auto_enroll_per_day": self.max_auto_enroll_per_day,
            "telegram_enabled": self.telegram_enabled,
            "email_enabled": self.email_enabled,
            "rss_enabled": self.rss_enabled,
            "daily_summary_enabled": self.daily_summary_enabled,
            "daily_summary_time": self.daily_summary_time,
            "interval_minutes": self.interval_minutes,
        }


class PushLog(Base):
    """Push delivery log."""
    __tablename__ = "push_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(String, nullable=False)
    push_type = Column(String, nullable=False)  # telegram / email / rss
    pushed_at = Column(DateTime, default=business_now)
    success = Column(Boolean, default=True)
    message = Column(Text, default="")


class EnrollLog(Base):
    """閫夎鎿嶄綔鏃ュ織"""
    __tablename__ = "enroll_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(String, nullable=False)
    course_name = Column(String, nullable=False)
    attempted_at = Column(DateTime, default=business_now)
    success = Column(Boolean, default=False)
    message = Column(Text, default="")


class EmailSubscriber(Base):
    """Email subscriber."""
    __tablename__ = "email_subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False)
    token = Column(String, unique=True, default=lambda: secrets.token_urlsafe(32))
    verified = Column(Boolean, default=False)
    active = Column(Boolean, default=True)
    # 鍋忓ソ璁剧疆
    categories_json = Column(Text, default="[]")
    campus_filter = Column(String, default="")
    self_sign_only = Column(Boolean, default=True)
    created_at = Column(DateTime, default=business_now)
    push_paused_until = Column(DateTime, nullable=True)
    last_portal_seen_at = Column(DateTime, nullable=True)
    onboarding_seen_at = Column(DateTime, nullable=True)
    verify_code = Column(String, nullable=True)
    verify_code_expires_at = Column(DateTime, nullable=True)
    verify_code_attempts = Column(Integer, default=0)
    login_code = Column(String, nullable=True)
    login_code_expires_at = Column(DateTime, nullable=True)
    login_code_attempts = Column(Integer, default=0)

    @property
    def categories(self) -> list:
        return json.loads(self.categories_json or "[]")

    @categories.setter
    def categories(self, value: list):
        self.categories_json = json.dumps(value, ensure_ascii=False)

    @property
    def push_is_paused(self) -> bool:
        if not self.push_paused_until:
            return False
        return business_now() < self.push_paused_until

    def to_dict(self) -> dict:
        paused_until_str = None
        if self.push_paused_until and self.push_is_paused:
            paused_until_str = self.push_paused_until.strftime("%Y-%m-%d %H:%M")
        return {
            "id": self.id,
            "email": self.email,
            "verified": self.verified,
            "active": self.active,
            "categories": self.categories,
            "campus_filter": self.campus_filter,
            "self_sign_only": self.self_sign_only,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "push_paused_until": paused_until_str,
            "push_is_paused": self.push_is_paused,
            "last_portal_seen_at": self.last_portal_seen_at.strftime("%Y-%m-%d %H:%M") if self.last_portal_seen_at else None,
            "onboarding_seen_at": self.onboarding_seen_at.strftime("%Y-%m-%d %H:%M") if self.onboarding_seen_at else None,
        }


class LoginBridgeTicket(Base):
    """Cross-device login bridge ticket."""
    __tablename__ = "login_bridge_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket = Column(String, unique=True, default=lambda: secrets.token_urlsafe(24), nullable=False)
    subscriber_id = Column(Integer, nullable=False)
    subscriber_email = Column(String, nullable=False)
    subscriber_token = Column(String, nullable=False)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=business_now)
    expires_at = Column(DateTime, nullable=False)
    verified_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)


class EmailAuthChallenge(Base):
    """短期、一次性邮箱验证/登录链接。"""

    __tablename__ = "email_auth_challenges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 数据库只保存摘要；原始值只出现在邮件链接中。
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    subscriber_id = Column(Integer, nullable=False, index=True)
    purpose = Column(String, nullable=False, default="verify")  # verify / login
    bridge_ticket = Column(String, nullable=True)
    created_at = Column(DateTime, default=business_now)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


class CourseReminder(Base):
    """Reminder created from the email action link."""
    __tablename__ = "course_reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscriber_id = Column(Integer, nullable=False)   # 鍏宠仈 EmailSubscriber.id
    course_id = Column(String, nullable=False)         # 鍏宠仈 Course.id
    remind_before_minutes = Column(Integer, default=5)
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=business_now)


class NotificationJob(Base):
    """持久化通知任务，作为外部投递前的 outbox。"""

    __tablename__ = "notification_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    job_type = Column(String(32), nullable=False, default="course_push")
    channel = Column(String(32), nullable=False, default="email", index=True)
    subscriber_id = Column(Integer, nullable=True, index=True)
    subscriber_email = Column(String, default="")
    course_ids_json = Column(Text, default="[]")
    event_type = Column(String(32), default="new")
    delivery_mode = Column(String(32), default="instant")
    payload_json = Column(Text, default="{}")
    priority = Column(Integer, default=0)
    status = Column(String(16), nullable=False, default="pending", index=True)
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    available_at = Column(DateTime, default=business_now, index=True)
    locked_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_error = Column(Text, default="")
    created_at = Column(DateTime, default=business_now)
    updated_at = Column(DateTime, default=business_now, onupdate=business_now)

    @property
    def course_ids(self) -> list:
        try:
            value = json.loads(self.course_ids_json or "[]")
        except (TypeError, ValueError):
            return []
        return value if isinstance(value, list) else []

    @course_ids.setter
    def course_ids(self, value: list):
        self.course_ids_json = json.dumps(value or [], ensure_ascii=False)

    @property
    def payload(self) -> dict:
        try:
            value = json.loads(self.payload_json or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @payload.setter
    def payload(self, value: dict):
        self.payload_json = json.dumps(value or {}, ensure_ascii=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "job_type": self.job_type,
            "channel": self.channel,
            "subscriber_id": self.subscriber_id,
            "subscriber_email": self.subscriber_email,
            "course_ids": self.course_ids,
            "event_type": self.event_type,
            "delivery_mode": self.delivery_mode,
            "priority": self.priority,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "available_at": self.available_at.strftime("%Y-%m-%d %H:%M:%S") if self.available_at else None,
            "locked_at": self.locked_at.strftime("%Y-%m-%d %H:%M:%S") if self.locked_at else None,
            "completed_at": self.completed_at.strftime("%Y-%m-%d %H:%M:%S") if self.completed_at else None,
            "last_error": self.last_error or "",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else None,
        }


class NotificationEvent(Base):
    """Subscriber-facing notification event."""
    __tablename__ = "notification_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscriber_id = Column(Integer, nullable=False)
    subscriber_email = Column(String, nullable=False)
    course_id = Column(String, nullable=False)
    course_name = Column(String, default="")
    course_category = Column(String, default="")
    event_type = Column(String, default="new")          # new / snipe
    delivery_mode = Column(String, default="")          # priority / digest_urgent / digest_soon / digest_daily
    channel = Column(String, default="email")
    sent_at = Column(DateTime, default=business_now)
    success = Column(Boolean, default=True)
    message = Column(Text, default="")


class QRCodeUpload(Base):
    """Uploaded check-in QR codes kept isolated from the main course flow."""
    __tablename__ = "qrcode_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(String, default="", index=True)
    contributor_email = Column(String, nullable=False, index=True)
    contributor_subscriber_id = Column(Integer, nullable=True)
    course_name = Column(String, nullable=False, default="")
    course_time = Column(String, default="")
    course_location = Column(String, default="")
    notes = Column(Text, default="")
    file_path = Column(String, nullable=False)
    original_filename = Column(String, default="")
    mime_type = Column(String, default="")
    file_size = Column(Integer, default=0)
    content_hash = Column(String(64), default="", index=True)
    verification_status = Column(String, default="pending")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=business_now)
    updated_at = Column(DateTime, default=business_now)
    deactivated_at = Column(DateTime, nullable=True)

    def to_dict(self, *, include_private: bool = False) -> dict:
        payload = {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": self.course_name,
            "course_time": self.course_time,
            "course_location": self.course_location,
            "notes": self.notes,
            "file_size": self.file_size,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }
        if include_private:
            payload.update({
                "contributor_email": self.contributor_email,
                "file_path": self.file_path,
                "original_filename": self.original_filename,
                "mime_type": self.mime_type,
                "content_hash": self.content_hash,
                "verification_status": self.verification_status,
                "is_active": self.is_active,
            })
        return payload



def init_db():
    """鍒濆鍖栨暟鎹簱琛紝濡傛灉涓嶅瓨鍦ㄥ垯鍒涘缓"""
    Base.metadata.create_all(engine)
    _migrate_schema_if_needed()
    # 纭繚鑷冲皯鏈変竴鏉?FilterConfig 璁板綍
    session = SessionLocal()
    try:
        config = session.query(FilterConfig).first()
        if not config:
            config = FilterConfig(id=1)
            session.add(config)
            session.commit()
    finally:
        session.close()


def get_session():
    """Get a database session."""
    return SessionLocal()


def commit_with_retry(session, retries: int = 4, base_delay: float = 0.15):
    """Commit with short retries for transient SQLite lock contention."""
    last_error = None
    for attempt in range(retries):
        try:
            session.commit()
            return
        except OperationalError as exc:
            session.rollback()
            last_error = exc
            if "database is locked" not in str(exc).lower() or attempt == retries - 1:
                raise
            time.sleep(base_delay * (attempt + 1))
    if last_error:
        raise last_error


def is_database_locked_error(exc) -> bool:
    """Return True when an exception chain indicates transient SQLite lock contention."""
    current = exc
    checked = set()
    while current is not None and id(current) not in checked:
        checked.add(id(current))
        if "database is locked" in str(current).lower():
            return True
        current = getattr(current, "orig", None) or getattr(current, "__cause__", None)
    return False


def _migrate_schema_if_needed():
    """鏈€灏忓寲杩佺Щ锛氫负鏃у簱琛ュ厖鏂板垪"""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as conn:
        if "filter_config" in table_names:
            columns = {col["name"] for col in inspector.get_columns("filter_config")}
            if "strict_boya_only" not in columns:
                conn.execute(text(
                    "ALTER TABLE filter_config "
                    "ADD COLUMN strict_boya_only BOOLEAN DEFAULT 0"
                ))
            if "daily_summary_enabled" not in columns:
                conn.execute(text(
                    "ALTER TABLE filter_config "
                    "ADD COLUMN daily_summary_enabled BOOLEAN DEFAULT 0"
                ))
            if "daily_summary_time" not in columns:
                conn.execute(text(
                    "ALTER TABLE filter_config "
                    "ADD COLUMN daily_summary_time VARCHAR DEFAULT '21:00'"
                ))

        if "courses" in table_names:
            course_columns = {col["name"] for col in inspector.get_columns("courses")}
            if "expired" not in course_columns:
                conn.execute(text(
                    "ALTER TABLE courses "
                    "ADD COLUMN expired BOOLEAN DEFAULT 0"
                ))

        if "notification_events" in table_names:
            ne_columns = {col["name"] for col in inspector.get_columns("notification_events")}
            if "delivery_mode" not in ne_columns:
                conn.execute(text(
                    "ALTER TABLE notification_events "
                    "ADD COLUMN delivery_mode VARCHAR DEFAULT ''"
                ))

        if "email_subscribers" in table_names:
            sub_columns = {col["name"] for col in inspector.get_columns("email_subscribers")}
            if "push_paused_until" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN push_paused_until DATETIME"
                ))
            if "last_portal_seen_at" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN last_portal_seen_at DATETIME"
                ))
            if "onboarding_seen_at" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN onboarding_seen_at DATETIME"
                ))
            if "verify_code" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN verify_code VARCHAR"
                ))
            if "verify_code_expires_at" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN verify_code_expires_at DATETIME"
                ))
            if "verify_code_attempts" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN verify_code_attempts INTEGER DEFAULT 0"
                ))
            if "login_code" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN login_code VARCHAR"
                ))
            if "login_code_expires_at" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN login_code_expires_at DATETIME"
                ))
            if "login_code_attempts" not in sub_columns:
                conn.execute(text(
                    "ALTER TABLE email_subscribers "
                    "ADD COLUMN login_code_attempts INTEGER DEFAULT 0"
                ))

        if "qrcode_uploads" in table_names:
            qr_columns = {col["name"] for col in inspector.get_columns("qrcode_uploads")}
            if "course_id" not in qr_columns:
                conn.execute(text(
                    "ALTER TABLE qrcode_uploads "
                    "ADD COLUMN course_id VARCHAR DEFAULT ''"
                ))
            if "content_hash" not in qr_columns:
                conn.execute(text(
                    "ALTER TABLE qrcode_uploads "
                    "ADD COLUMN content_hash VARCHAR(64) DEFAULT ''"
                ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_qrcode_uploads_course_id "
                "ON qrcode_uploads (course_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_qrcode_uploads_content_hash "
                "ON qrcode_uploads (content_hash)"
            ))

        # 高频列表和门户接口使用的组合索引。IF NOT EXISTS 让升级可重复执行，
        # 不修改现有业务数据，也不要求停机做手工迁移。
        for statement in (
            "CREATE INDEX IF NOT EXISTS ix_courses_expired_first_seen "
            "ON courses (expired, first_seen DESC)",
            "CREATE INDEX IF NOT EXISTS ix_courses_enroll_window "
            "ON courses (expired, enroll_start, enroll_end)",
            "CREATE INDEX IF NOT EXISTS ix_course_reminders_subscriber_sent_created "
            "ON course_reminders (subscriber_id, sent, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_notification_events_subscriber_sent_at "
            "ON notification_events (subscriber_id, sent_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_notification_events_sent_at_course "
            "ON notification_events (sent_at DESC, course_id)",
            "CREATE INDEX IF NOT EXISTS ix_notification_jobs_status_available_at "
            "ON notification_jobs (status, available_at, priority DESC, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_notification_jobs_channel_status "
            "ON notification_jobs (channel, status, available_at)",
            "CREATE INDEX IF NOT EXISTS ix_email_subscribers_verified_active "
            "ON email_subscribers (verified, active)",
            "CREATE INDEX IF NOT EXISTS ix_email_subscribers_last_portal_seen "
            "ON email_subscribers (last_portal_seen_at)",
        ):
            conn.execute(text(statement))

