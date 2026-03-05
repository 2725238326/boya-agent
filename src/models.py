"""
SQLAlchemy 数据模型 - 课程信息 & 筛选配置
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
    """博雅课程信息"""
    __tablename__ = "courses"

    id = Column(String, primary_key=True)  # 名称+时间 hash
    name = Column(String, nullable=False)
    category = Column(String, default="")
    location = Column(String, default="")
    teacher = Column(String, default="")
    college = Column(String, default="")       # 学院
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    enroll_start = Column(DateTime, nullable=True)
    enroll_end = Column(DateTime, nullable=True)
    sign_method = Column(String, default="")     # 自主签课 / 非自主签课
    capacity = Column(Integer, default=0)
    enrolled = Column(Integer, default=0)
    status = Column(String, default="")         # 可选 / 已满
    campus = Column(String, default="")
    open_college = Column(String, default="")   # 开放学院
    open_grade = Column(String, default="")     # 开放年级
    open_group = Column(String, default="")     # 开放人群
    has_homework = Column(String, default="")   # 课程作业
    check_in_method = Column(String, default="")  # 签到方式（详情页）
    description = Column(Text, default="")        # 课程介绍（详情页）
    organizer = Column(String, default="")        # 课程组织负责人

    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    pushed = Column(Boolean, default=False)
    expired = Column(Boolean, default=False)  # 选课已截止/失效
    enrolled_by_bot = Column(Boolean, default=False)  # 是否被自动选课

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
    """用户筛选配置"""
    __tablename__ = "filter_config"

    id = Column(Integer, primary_key=True, default=1)
    categories_json = Column(Text, default="[]")         # 选中的类别列表
    self_sign_only = Column(Boolean, default=True)        # 仅自主签到
    strict_boya_only = Column(Boolean, default=False)     # 严格博雅规则：非校医院 + 自主签到
    min_remaining = Column(Integer, default=1)            # 最少剩余名额
    campus_filter = Column(String, default="")            # 校区过滤
    keyword_whitelist_json = Column(Text, default="[]")   # 关键词白名单
    keyword_blacklist_json = Column(Text, default="[]")   # 关键词黑名单

    # 自动选课设置
    auto_enroll_enabled = Column(Boolean, default=False)
    priority_keywords_json = Column(Text, default="[]")   # 意愿优先级关键词
    confirm_before_enroll = Column(Boolean, default=True)
    max_auto_enroll_per_day = Column(Integer, default=2)

    # 推送设置
    telegram_enabled = Column(Boolean, default=False)
    email_enabled = Column(Boolean, default=False)
    rss_enabled = Column(Boolean, default=True)
    daily_summary_enabled = Column(Boolean, default=False)   # 是否启用每日汇总推送
    daily_summary_time = Column(String, default="21:00")     # 每日汇总推送时间（HH:MM）

    # 调度设置
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
    """推送日志"""
    __tablename__ = "push_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(String, nullable=False)
    push_type = Column(String, nullable=False)  # telegram / email / rss
    pushed_at = Column(DateTime, default=datetime.now)
    success = Column(Boolean, default=True)
    message = Column(Text, default="")


class EnrollLog(Base):
    """选课操作日志"""
    __tablename__ = "enroll_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(String, nullable=False)
    course_name = Column(String, nullable=False)
    attempted_at = Column(DateTime, default=datetime.now)
    success = Column(Boolean, default=False)
    message = Column(Text, default="")


class EmailSubscriber(Base):
    """邮件订阅者"""
    __tablename__ = "email_subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False)
    token = Column(String, unique=True, default=lambda: secrets.token_urlsafe(32))
    verified = Column(Boolean, default=False)
    active = Column(Boolean, default=True)
    # 偏好设置
    categories_json = Column(Text, default="[]")
    campus_filter = Column(String, default="")
    self_sign_only = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    @property
    def categories(self) -> list:
        return json.loads(self.categories_json or "[]")

    @categories.setter
    def categories(self, value: list):
        self.categories_json = json.dumps(value, ensure_ascii=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "verified": self.verified,
            "active": self.active,
            "categories": self.categories,
            "campus_filter": self.campus_filter,
            "self_sign_only": self.self_sign_only,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
        }

class CourseReminder(Base):
    """选课提醒：用户通过邮件中的“提醒我选课”按钮注册"""
    __tablename__ = "course_reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscriber_id = Column(Integer, nullable=False)   # 关联 EmailSubscriber.id
    course_id = Column(String, nullable=False)         # 关联 Course.id
    remind_before_minutes = Column(Integer, default=5)
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)



def init_db():
    """初始化数据库表，如果不存在则创建"""
    Base.metadata.create_all(engine)
    _migrate_schema_if_needed()
    # 确保至少有一条 FilterConfig 记录
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
    """获取数据库会话"""
    return SessionLocal()


def _migrate_schema_if_needed():
    """最小化迁移：为旧库补充新列"""
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
