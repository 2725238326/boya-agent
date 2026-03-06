"""
Flask Web 控制台
提供筛选配置、课程查看、自动选课开关、系统状态等功能
"""

import os
import asyncio
import threading
from flask import Flask, render_template, jsonify, request, Response, redirect
from flask_cors import CORS
from loguru import logger

from src.models import Course, FilterConfig, PushLog, EnrollLog, EmailSubscriber, CourseReminder, get_session, init_db
from src.push.rss_feed import generate_rss_feed, generate_atom_feed
from src.scheduler import (
    get_run_status,
    run_scrape_task,
    update_scheduler_interval,
    update_daily_summary_schedule,
)


app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
)
app.secret_key = os.getenv("WEB_SECRET_KEY", "boya-agent-secret-key")
CORS(app)


# ========== 页面路由 ==========

@app.route("/")
def index():
    """控制台主页"""
    return render_template("index.html")


@app.route("/subscribe")
def subscribe_page():
    """订阅页面（公开访问）"""
    return render_template("subscribe.html")


# ========== API 路由 ==========

@app.route("/api/courses")
def api_courses():
    """获取课程列表"""
    session = get_session()
    try:
        query = session.query(Course).order_by(Course.first_seen.desc())

        category = request.args.get("category")
        campus = request.args.get("campus")
        self_sign = request.args.get("self_sign")
        keyword = request.args.get("keyword")
        include_expired = request.args.get("include_expired", "false").lower() == "true"

        if not include_expired:
            query = query.filter(Course.expired == False)  # noqa: E712

        if category:
            query = query.filter(Course.category.contains(category))
        if campus:
            query = query.filter(Course.campus.contains(campus))
        if self_sign == "true":
            query = query.filter(Course.check_in_method.contains("自主"))
        if keyword:
            query = query.filter(Course.name.contains(keyword))

        courses = query.limit(200).all()
        return jsonify({
            "success": True,
            "data": [c.to_dict() for c in courses],
            "total": len(courses),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/config", methods=["GET"])
def api_get_config():
    """获取筛选配置"""
    session = get_session()
    try:
        config = session.query(FilterConfig).first()
        if not config:
            config = FilterConfig(id=1)
            session.add(config)
            session.commit()
        return jsonify({"success": True, "data": config.to_dict()})
    finally:
        session.close()


@app.route("/api/config", methods=["PUT"])
def api_update_config():
    """更新筛选配置"""
    session = get_session()
    try:
        data = request.get_json()
        config = session.query(FilterConfig).first()
        if not config:
            config = FilterConfig(id=1)
            session.add(config)

        if "categories" in data:
            config.categories = data["categories"]
        if "self_sign_only" in data:
            config.self_sign_only = data["self_sign_only"]
        if "strict_boya_only" in data:
            config.strict_boya_only = data["strict_boya_only"]
        if "min_remaining" in data:
            config.min_remaining = int(data["min_remaining"])
        if "campus_filter" in data:
            config.campus_filter = data["campus_filter"]
        if "keyword_whitelist" in data:
            config.keyword_whitelist = data["keyword_whitelist"]
        if "keyword_blacklist" in data:
            config.keyword_blacklist = data["keyword_blacklist"]
        if "auto_enroll_enabled" in data:
            config.auto_enroll_enabled = data["auto_enroll_enabled"]
        if "priority_keywords" in data:
            config.priority_keywords = data["priority_keywords"]
        if "confirm_before_enroll" in data:
            config.confirm_before_enroll = data["confirm_before_enroll"]
        if "max_auto_enroll_per_day" in data:
            config.max_auto_enroll_per_day = int(data["max_auto_enroll_per_day"])
        if "telegram_enabled" in data:
            config.telegram_enabled = data["telegram_enabled"]
        if "email_enabled" in data:
            config.email_enabled = data["email_enabled"]
        if "rss_enabled" in data:
            config.rss_enabled = data["rss_enabled"]
        if "daily_summary_enabled" in data:
            config.daily_summary_enabled = data["daily_summary_enabled"]
        if "daily_summary_time" in data:
            config.daily_summary_time = str(data["daily_summary_time"]).strip()
        if "interval_minutes" in data:
            config.interval_minutes = int(data["interval_minutes"])
            update_scheduler_interval(config.interval_minutes)

        session.commit()
        if "daily_summary_enabled" in data or "daily_summary_time" in data:
            update_daily_summary_schedule()
        logger.info("配置已更新")
        return jsonify({"success": True, "message": "配置已保存"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/enroll/toggle", methods=["POST"])
def api_toggle_enroll():
    """切换自动选课开关"""
    session = get_session()
    try:
        config = session.query(FilterConfig).first()
        config.auto_enroll_enabled = not config.auto_enroll_enabled
        session.commit()
        status = "已开启" if config.auto_enroll_enabled else "已关闭"
        return jsonify({
            "success": True,
            "enabled": config.auto_enroll_enabled,
            "message": f"自动选课{status}",
        })
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/trigger", methods=["POST"])
def api_trigger_scrape():
    """手动触发一次抓取（在独立线程中运行，避免 event loop 冲突）"""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_scrape_task())
        finally:
            loop.close()

    try:
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({"success": True, "message": "抓取任务已触发"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/status")
def api_status():
    """获取系统运行状态"""
    from src.scheduler import _browser_state, _push_buffer
    status = get_run_status()

    session = get_session()
    try:
        status["total_courses_in_db"] = session.query(Course).count()
        status["total_push_logs"] = session.query(PushLog).count()
        status["total_enroll_logs"] = session.query(EnrollLog).count()
    finally:
        session.close()

    # 浏览器和推送缓冲区状态
    browser_alive = False
    try:
        page = _browser_state.get("page")
        if page:
            _ = page.url
            browser_alive = True
    except Exception:
        pass

    status["browser_alive"] = browser_alive
    status["push_buffer_urgent"] = len(_push_buffer.get("urgent", []))
    status["push_buffer_soon"] = len(_push_buffer.get("soon", []))

    return jsonify({"success": True, "data": status})


@app.route("/api/categories")
def api_categories():
    """获取所有已知课程类别"""
    session = get_session()
    try:
        categories = session.query(Course.category).distinct().all()
        return jsonify({
            "success": True,
            "data": [c[0] for c in categories if c[0]],
        })
    finally:
        session.close()


@app.route("/api/logs/push")
def api_push_logs():
    """获取推送日志"""
    session = get_session()
    try:
        logs = (
            session.query(PushLog)
            .order_by(PushLog.pushed_at.desc())
            .limit(50)
            .all()
        )
        return jsonify({
            "success": True,
            "data": [{
                "id": l.id,
                "course_id": l.course_id,
                "push_type": l.push_type,
                "pushed_at": l.pushed_at.strftime("%Y-%m-%d %H:%M"),
                "success": l.success,
            } for l in logs],
        })
    finally:
        session.close()


@app.route("/api/logs/enroll")
def api_enroll_logs():
    """获取选课日志"""
    session = get_session()
    try:
        logs = (
            session.query(EnrollLog)
            .order_by(EnrollLog.attempted_at.desc())
            .limit(50)
            .all()
        )
        return jsonify({
            "success": True,
            "data": [{
                "id": l.id,
                "course_id": l.course_id,
                "course_name": l.course_name,
                "attempted_at": l.attempted_at.strftime("%Y-%m-%d %H:%M"),
                "success": l.success,
                "message": l.message,
            } for l in logs],
        })
    finally:
        session.close()


# ========== RSS 端点 ==========

@app.route("/rss")
def rss_feed():
    """RSS 2.0 Feed"""
    session = get_session()
    try:
        courses = (
            session.query(Course)
            .order_by(Course.first_seen.desc())
            .limit(50)
            .all()
        )
        base_url = request.host_url.rstrip("/")
        xml = generate_rss_feed(courses, base_url)
        return Response(xml, mimetype="application/rss+xml; charset=utf-8")
    finally:
        session.close()


@app.route("/atom")
def atom_feed():
    """Atom Feed"""
    session = get_session()
    try:
        courses = (
            session.query(Course)
            .order_by(Course.first_seen.desc())
            .limit(50)
            .all()
        )
        base_url = request.host_url.rstrip("/")
        xml = generate_atom_feed(courses, base_url)
        return Response(xml, mimetype="application/atom+xml; charset=utf-8")
    finally:
        session.close()


# ========== 邮件订阅 API ==========

@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    """用户提交邮件订阅"""
    from src.push.email_push import send_verification_email

    data = request.get_json()
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"success": False, "error": "请输入有效的邮箱地址"}), 400

    session = get_session()
    try:
        existing = session.query(EmailSubscriber).filter_by(email=email).first()
        if existing:
            if existing.active and existing.verified:
                return jsonify({"success": False, "error": "该邮箱已订阅"})
            # 重新激活
            existing.active = True
            existing.verified = False
            existing.campus_filter = data.get("campus_filter", "")
            existing.self_sign_only = data.get("self_sign_only", True)
            existing.categories = data.get("categories", [])
            session.commit()
            token = existing.token
        else:
            sub = EmailSubscriber(
                email=email,
                campus_filter=data.get("campus_filter", ""),
                self_sign_only=data.get("self_sign_only", True),
            )
            sub.categories = data.get("categories", [])
            session.add(sub)
            session.commit()
            token = sub.token

        # 发送验证邮件
        base_url = request.host_url.rstrip("/")
        verify_url = f"{base_url}/api/verify/{token}"
        ok = send_verification_email(email, verify_url)

        if ok:
            return jsonify({"success": True, "message": "验证邮件已发送，请查收并点击验证链接"})
        else:
            return jsonify({"success": True, "message": "订阅成功，但验证邮件发送失败，请联系管理员"})
    except Exception as e:
        session.rollback()
        logger.error(f"订阅失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/verify/<token>")
def api_verify(token):
    """验证邮箱"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(token=token).first()
        if not sub:
            return redirect("/subscribe?result=invalid")
        sub.verified = True
        sub.active = True
        session.commit()
        # 验证成功后直接进入用户门户
        return redirect(f"/portal?token={sub.token}&email={sub.email}")
    except Exception as e:
        session.rollback()
        logger.error(f"验证失败: {e}")
        return redirect("/subscribe?result=invalid")
    finally:
        session.close()


@app.route("/api/unsubscribe/<token>")
def api_unsubscribe(token):
    """退订"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(token=token).first()
        if not sub:
            return redirect("/subscribe?result=invalid")
        sub.active = False
        session.commit()
        return redirect("/subscribe?result=unsubscribed")
    except Exception as e:
        session.rollback()
        logger.error(f"退订失败: {e}")
        return redirect("/subscribe?result=invalid")
    finally:
        session.close()


@app.route("/api/subscribers")
def api_subscribers():
    """管理端：查看所有订阅者"""
    session = get_session()
    try:
        subs = session.query(EmailSubscriber).order_by(EmailSubscriber.created_at.desc()).all()
        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in subs],
            "total": len(subs),
        })
    finally:
        session.close()


@app.route("/api/remind/<token>/<course_id>", methods=["GET", "POST"])
def api_remind(token, course_id):
    """注册选课提醒：用户点击邮件中的「提醒我选课」按钮"""
    is_json = request.is_json or request.headers.get('Accept', '').startswith('application/json')
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(token=token, active=True).first()
        if not sub:
            if is_json:
                return jsonify({"success": False, "error": "无效的 token"}), 404
            return redirect("/subscribe?result=invalid")

        course = session.query(Course).filter_by(id=course_id).first()
        if not course:
            if is_json:
                return jsonify({"success": False, "error": "课程不存在"}), 404
            return redirect("/subscribe?result=invalid")

        # 防止重复注册
        existing = (
            session.query(CourseReminder)
            .filter_by(subscriber_id=sub.id, course_id=course_id, sent=False)
            .first()
        )
        if not existing:
            reminder = CourseReminder(
                subscriber_id=sub.id,
                course_id=course_id,
                remind_before_minutes=5,
            )
            session.add(reminder)
            session.commit()
            logger.info(f"选课提醒已注册: {sub.email} -> {course.name}")

        if is_json:
            return jsonify({"success": True, "message": f"已注册提醒: {course.name}"})
        return redirect("/subscribe?result=reminded")
    except Exception as e:
        session.rollback()
        logger.error(f"注册选课提醒失败: {e}")
        if is_json:
            return jsonify({"success": False, "error": str(e)}), 500
        return redirect("/subscribe?result=invalid")
    finally:
        session.close()


# ========== 测试工具 ==========

@app.route("/api/test-email", methods=["POST"])
def api_test_email():
    """发送测试邮件：用数据库中真实课程数据构建邮件并发送到指定邮箱"""
    from src.push.email_push import _build_notification_html, _send_raw_email

    data = request.get_json() or {}
    to_email = (data.get("email") or "").strip()
    if not to_email or "@" not in to_email:
        return jsonify({"success": False, "error": "请提供有效的目标邮箱"}), 400

    session = get_session()
    try:
        courses = session.query(Course).filter(Course.expired == False).limit(4).all()  # noqa: E712
        if not courses:
            return jsonify({"success": False, "error": "数据库中没有可用课程"}), 404

        base_url = request.host_url.rstrip("/")
        html = _build_notification_html(
            courses,
            unsubscribe_url=f"{base_url}/api/unsubscribe/test",
            sub_token="test",
            base_url=base_url,
        )
        ok = _send_raw_email(to_email, f"[测试] 博雅课程通知 ({len(courses)} 门)", html)
        if ok:
            logger.info(f"测试邮件发送成功 -> {to_email}")
            return jsonify({"success": True, "message": f"测试邮件已发送到 {to_email}，共 {len(courses)} 门课程"})
        else:
            return jsonify({"success": False, "error": "邮件发送失败，请检查 SMTP 配置"}), 500
    except Exception as e:
        logger.error(f"测试邮件失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


# ========== 用户门户 ==========

@app.route("/portal")
def portal_page():
    """用户门户页面"""
    return render_template("portal.html")


@app.route("/api/subscriber/lookup", methods=["POST"])
def api_subscriber_lookup():
    """根据邮箱查询订阅者信息（已验证且活跃的）"""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"success": False, "error": "请输入有效的邮箱"}), 400

    session = get_session()
    try:
        sub = (
            session.query(EmailSubscriber)
            .filter_by(email=email, verified=True, active=True)
            .first()
        )
        if not sub:
            return jsonify({"success": False, "error": "该邮箱尚未订阅或未验证"})

        return jsonify({
            "success": True,
            "data": {
                **sub.to_dict(),
                "token": sub.token,
            },
        })
    finally:
        session.close()


@app.route("/api/subscriber/<token>", methods=["PUT"])
def api_subscriber_update(token):
    """更新订阅者偏好设置"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(token=token).first()
        if not sub:
            return jsonify({"success": False, "error": "无效的 token"}), 404

        data = request.get_json() or {}
        if "categories" in data:
            sub.categories = data["categories"]
        if "campus_filter" in data:
            sub.campus_filter = data["campus_filter"]
        if "self_sign_only" in data:
            sub.self_sign_only = bool(data["self_sign_only"])
        if "active" in data:
            sub.active = bool(data["active"])

        session.commit()
        logger.info(f"订阅者偏好已更新: {sub.email}")
        return jsonify({"success": True, "message": "偏好已保存", "data": sub.to_dict()})
    except Exception as e:
        session.rollback()
        logger.error(f"更新订阅者偏好失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/subscriber/<token>/reminders")
def api_subscriber_reminders(token):
    """获取订阅者的选课提醒列表（含课程详情）"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(token=token).first()
        if not sub:
            return jsonify({"success": False, "error": "无效的 token"}), 404

        reminders = (
            session.query(CourseReminder)
            .filter_by(subscriber_id=sub.id)
            .order_by(CourseReminder.created_at.desc())
            .all()
        )

        result = []
        for r in reminders:
            course = session.query(Course).filter_by(id=r.course_id).first()
            result.append({
                "id": r.id,
                "course_id": r.course_id,
                "course_name": course.name if course else "未知课程",
                "course_category": course.category if course else "",
                "course_teacher": course.teacher if course else "",
                "enroll_start": course.enroll_start.strftime("%Y-%m-%d %H:%M") if course and course.enroll_start else "",
                "remind_before_minutes": r.remind_before_minutes,
                "sent": r.sent,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            })

        return jsonify({"success": True, "data": result, "total": len(result)})
    finally:
        session.close()


# ========== 管理工具 ==========

@app.route("/api/manual-push", methods=["POST"])
def api_manual_push():
    """手动推送指定课程给所有活跃邮件订阅者"""
    from src.push.email_push import send_email_notification
    import asyncio

    data = request.get_json() or {}
    course_id = data.get("course_id")
    if not course_id:
        return jsonify({"success": False, "error": "缺少 course_id"}), 400

    session = get_session()
    try:
        course = session.query(Course).filter_by(id=course_id).first()
        if not course:
            return jsonify({"success": False, "error": "课程不存在"}), 404

        # 发送邮件推送
        try:
            loop = asyncio.new_event_loop()
            ok = loop.run_until_complete(send_email_notification([course]))
            loop.close()
        except Exception as e:
            return jsonify({"success": False, "error": f"推送失败: {e}"}), 500

        if ok:
            course.pushed = True
            session.commit()
            return jsonify({"success": True, "message": f"已推送: {course.name}"})
        else:
            return jsonify({"success": False, "error": "邮件发送失败"}), 500
    finally:
        session.close()


@app.route("/api/cleanup-expired", methods=["POST"])
def api_cleanup_expired():
    """清理 30 天以上的过期课程"""
    from datetime import datetime, timedelta

    days = request.get_json(silent=True) or {}
    max_days = days.get("days", 30)

    session = get_session()
    try:
        cutoff = datetime.now() - timedelta(days=max_days)
        old_courses = (
            session.query(Course)
            .filter(Course.expired == True)  # noqa: E712
            .filter(Course.enroll_end < cutoff)
            .all()
        )

        count = len(old_courses)
        for c in old_courses:
            session.delete(c)
        session.commit()

        logger.info(f"清理了 {count} 门过期超过 {max_days} 天的课程")
        return jsonify({"success": True, "deleted": count, "message": f"已清理 {count} 门课程"})
    except Exception as e:
        session.rollback()
        logger.error(f"清理过期课程失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()

