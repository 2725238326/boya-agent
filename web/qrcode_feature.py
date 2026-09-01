"""
Isolated QRCode feature routes.

This module is intentionally kept separate from the main portal/course flow so
future moderation and contribution workflows can evolve without destabilizing
the existing scraping pipeline.
"""

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory
from loguru import logger

from src.course_state import is_course_expired
from src.models import Course, QRCodeUpload, get_session
from src.qrcode_service import (
    QRCODE_UPLOAD_ROOT,
    REWARD_THRESHOLDS,
    create_qrcode_upload,
    ensure_qrcode_upload_root,
    get_contributor_leaderboard,
    get_contributor_stats,
    get_qrcode_course_context,
    is_allowed_qrcode_file,
    list_public_qrcode_uploads,
    mask_contributor_email,
    resolve_contributor,
)
from src.time_utils import now as business_now

qrcode_bp = Blueprint("qrcode_feature", __name__)


def _get_portal_session_token() -> str:
    cookie_name = current_app.config.get("PORTAL_SESSION_COOKIE", "portal_token")
    return (request.cookies.get(cookie_name) or "").strip()


def _render_qrcode_page(course_id: str = ""):
    session = get_session()
    try:
        course = get_qrcode_course_context(session, course_id) if course_id else None
    finally:
        session.close()

    return render_template(
        "qrcode.html",
        reward_thresholds=REWARD_THRESHOLDS,
        qrcode_course=course,
        qrcode_missing_course=bool(course_id and not course),
    )


@qrcode_bp.route("/QRcode", strict_slashes=False)
def qrcode_page():
    course_id = (request.args.get("course_id") or "").strip()
    return _render_qrcode_page(course_id)


@qrcode_bp.route("/QRcode/course/<course_id>", strict_slashes=False)
def qrcode_course_page(course_id: str):
    response = _render_qrcode_page(course_id)
    session = get_session()
    try:
        course = get_qrcode_course_context(session, course_id)
    finally:
        session.close()
    return response, (404 if course is None else 200)


@qrcode_bp.route("/api/qrcode/context")
def api_qrcode_context():
    course_id = (request.args.get("course_id") or "").strip()
    session = get_session()
    try:
        contributor = resolve_contributor(session, session_token=_get_portal_session_token())
        email = contributor.get("email") or ""
        stats = get_contributor_stats(session, email)
        course = get_qrcode_course_context(session, course_id) if course_id else None
        return jsonify({
            "success": True,
            "data": {
                "logged_in": bool(email),
                # 即使是公开上下文，也只返回脱敏地址。
                "email": mask_contributor_email(email) if email else "",
                "stats": stats,
                "reward_thresholds": list(REWARD_THRESHOLDS),
                "course": course,
                "leaderboard_current": get_contributor_leaderboard(session, course_id=course_id, period="weekly"),
                "leaderboard_all_time": get_contributor_leaderboard(session, course_id=course_id, period="all"),
            },
        })
    finally:
        session.close()


@qrcode_bp.route("/api/qrcode/uploads", methods=["GET"])
def api_qrcode_uploads():
    course_id = (request.args.get("course_id") or "").strip()
    session = get_session()
    try:
        uploads = list_public_qrcode_uploads(session, course_id=course_id)
        return jsonify({"success": True, "data": uploads, "total": len(uploads)})
    finally:
        session.close()


@qrcode_bp.route("/api/qrcode/uploads", methods=["POST"])
def api_qrcode_upload_create():
    image = request.files.get("image")
    if image is None or not (image.filename or "").strip():
        return jsonify({"success": False, "error": "请上传二维码图片"}), 400
    if not is_allowed_qrcode_file(image.filename or ""):
        return jsonify({"success": False, "error": "仅支持 png/jpg/jpeg/gif/webp 图片"}), 400

    course_id = (request.form.get("course_id") or "").strip()

    session = get_session()
    try:
        contributor = resolve_contributor(
            session,
            session_token=_get_portal_session_token(),
        )
        contributor_email = (contributor.get("email") or "").strip().lower()
        if not contributor.get("subscriber_id") or not contributor_email or "@" not in contributor_email:
            return jsonify({"success": False, "error": "请先登录门户，再上传二维码"}), 401

        course_context = get_qrcode_course_context(session, course_id) if course_id else None
        if course_id and not course_context:
            return jsonify({"success": False, "error": "未找到对应课程，请从课程卡片重新进入"}), 400
        if course_context and course_context.get("expired"):
            return jsonify({"success": False, "error": "这门课程已经结束，不能再上传二维码"}), 400

        course_name = (course_context or {}).get("name") or (request.form.get("course_name") or "").strip()
        if not course_name:
            return jsonify({"success": False, "error": "请填写课程名称"}), 400

        upload = create_qrcode_upload(
            session,
            course_id=course_id,
            contributor_email=contributor_email,
            contributor_subscriber_id=contributor.get("subscriber_id"),
            course_name=course_name,
            course_time=(course_context or {}).get("course_time") or (request.form.get("course_time") or ""),
            course_location=(course_context or {}).get("location") or (request.form.get("course_location") or ""),
            notes=request.form.get("notes") or "",
            file_storage=image,
        )
        session.commit()

        payload = upload.to_dict()
        payload["image_url"] = f"/qrcode/uploads/{upload.file_path}"
        payload["contributor_upload_count"] = get_contributor_stats(session, contributor_email)["total_uploads"]
        payload["masked_contributor_email"] = mask_contributor_email(contributor_email)
        payload.pop("verification_status", None)
        payload["review_status"] = "pending"
        return jsonify({
            "success": True,
            "message": "上传成功，等待审核后才会出现在共享列表",
            "data": payload,
            "stats": get_contributor_stats(session, contributor_email),
        })
    except ValueError as exc:
        session.rollback()
        error_messages = {
            "unsupported file type": "仅支持 png/jpg/jpeg/gif/webp 图片",
            "file too large": "图片不能超过 5 MB",
            "invalid image content": "文件不是有效的图片，请重新选择",
            "file extension does not match image content": "文件扩展名与图片内容不一致",
            "duplicate QR code": "这张二维码已经上传过了",
            "invalid course": "未找到对应课程，请从课程卡片重新进入",
        }
        return jsonify({"success": False, "error": error_messages.get(str(exc), "上传内容不符合要求")}), 400
    except Exception as exc:
        session.rollback()
        logger.exception(f"qrcode upload failed: {exc}")
        return jsonify({"success": False, "error": "上传失败，请稍后重试"}), 500
    finally:
        session.close()


@qrcode_bp.route("/qrcode/uploads/<path:relative_path>")
def qrcode_uploaded_file(relative_path: str):
    normalized_path = (relative_path or "").replace("\\", "/").lstrip("/")
    parts = normalized_path.split("/")
    if not normalized_path or any(part in {"", ".", ".."} for part in parts):
        return jsonify({"success": False, "error": "文件不存在"}), 404

    session = get_session()
    try:
        upload = (
            session.query(QRCodeUpload)
            .filter_by(
                file_path=normalized_path,
                is_active=True,
                verification_status="approved",
            )
            .first()
        )
        if not upload:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        if upload.course_id:
            course = session.query(Course).filter_by(id=upload.course_id).first()
            if not course or is_course_expired(course):
                return jsonify({"success": False, "error": "二维码已失效"}), 404
    finally:
        session.close()

    ensure_qrcode_upload_root()
    return send_from_directory(str(QRCODE_UPLOAD_ROOT), normalized_path)


@qrcode_bp.route("/api/admin/qrcode/uploads", methods=["GET"])
def api_admin_qrcode_uploads():
    """管理端查看二维码审核记录。应用层管理员认证由 web.app 统一执行。"""
    status = (request.args.get("status") or "").strip().lower()
    session = get_session()
    try:
        query = session.query(QRCodeUpload).order_by(QRCodeUpload.created_at.desc()).limit(200)
        if status in {"pending", "approved", "rejected", "expired"}:
            query = query.filter_by(verification_status=status)
        rows = query.all()
        return jsonify({
            "success": True,
            "data": [row.to_dict(include_private=True) for row in rows],
            "total": len(rows),
        })
    finally:
        session.close()


@qrcode_bp.route("/api/admin/qrcode/<int:upload_id>", methods=["PATCH", "POST"])
def api_admin_moderate_qrcode(upload_id: int):
    """审核或下架一条二维码记录。"""
    data = request.get_json(silent=True) or {}
    status = (data.get("verification_status") or data.get("status") or "").strip().lower()
    if status not in {"pending", "approved", "rejected", "expired"}:
        return jsonify({"success": False, "error": "审核状态无效"}), 400

    session = get_session()
    try:
        upload = session.query(QRCodeUpload).filter_by(id=upload_id).first()
        if not upload:
            return jsonify({"success": False, "error": "二维码记录不存在"}), 404
        upload.verification_status = status
        upload.is_active = status == "approved"
        upload.deactivated_at = None if upload.is_active else business_now()
        upload.updated_at = business_now()
        session.commit()
        logger.info("qrcode moderation: id={} status={}", upload.id, status)
        return jsonify({
            "success": True,
            "message": "审核状态已保存",
            "data": upload.to_dict(include_private=True),
        })
    except Exception as exc:
        session.rollback()
        logger.exception("qrcode moderation failed: {}", exc)
        return jsonify({"success": False, "error": "保存审核状态失败"}), 500
    finally:
        session.close()
