"""
SQLAlchemy 鏁版嵁妯″瀷 - 璇剧▼淇℃伅 & 绛涢€夐厤缃?
"""

import json
import secrets
from datetime import datetime
from sqlalchemy import inspect, text
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_PATH = "boya_agent.db"

Base = declarative_base()
engine = create_engine(f"sqlite:///{DATABASE_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


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

    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    pushed = Column(Boolean, default=False)
    expired = Column(Boolean, default=False)  # 閫夎宸叉埅姝?澶辨晥
    enrolled_by_bot = Column(Boolean, default=False)  # 鏄惁琚嚜鍔ㄩ€夎

    @property
    def remaining(self) -> int:
        return max(0, self.capacity - self.enrolled)

    @property
    def is_enrollable(self) -> bool:
        now = datetime.now()
        return (
            (not self.expired)
            and
            self.enroll_start is not None
            and self.enroll_end is not None
            and self.enroll_start <= now <= self.enroll_end
            and self.remaining > 0
        )

    def to_dict(self) -> dict:
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
            "status": self.status,
            "campus": self.campus,
            "check_in_method": self.check_in_method,
            "is_enrollable": self.is_enrollable,
            "pushed": self.pushed,
            "expired": self.expired,
            "first_seen": self.first_seen.strftime("%Y-%m-%d %H:%M") if self.first_seen else "",
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
    pushed_at = Column(DateTime, default=datetime.now)
    success = Column(Boolean, default=True)
    message = Column(Text, default="")


class EnrollLog(Base):
    """閫夎鎿嶄綔鏃ュ織"""
    __tablename__ = "enroll_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(String, nullable=False)
    course_name = Column(String, nullable=False)
    attempted_at = Column(DateTime, default=datetime.now)
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
    created_at = Column(DateTime, default=datetime.now)
    push_paused_until = Column(DateTime, nullable=True)
    last_portal_seen_at = Column(DateTime, nullable=True)
    verify_code = Column(String, nullable=True)
    verify_code_expires_at = Column(DateTime, nullable=True)

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
        return datetime.now() < self.push_paused_until

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
    created_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)
    verified_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)


class CourseReminder(Base):
    """Reminder created from the email action link."""
    __tablename__ = "course_reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscriber_id = Column(Integer, nullable=False)   # 鍏宠仈 EmailSubscriber.id
    course_id = Column(String, nullable=False)         # 鍏宠仈 Course.id
    remind_before_minutes = Column(Integer, default=5)
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)


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
    sent_at = Column(DateTime, default=datetime.now)
    success = Column(Boolean, default=True)
    message = Column(Text, default="")



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

