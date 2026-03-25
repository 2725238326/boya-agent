"""
Isolated QRCode feature routes.

This module is intentionally kept separate from the main portal/course flow so
future moderation and contribution workflows can evolve without destabilizing
the existing scraping pipeline.
"""

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory
from loguru import logger

from src.models import get_session
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
                "email": email,
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
            submitted_email=request.form.get("email") or "",
        )
        contributor_email = (contributor.get("email") or "").strip().lower()
        if not contributor_email or "@" not in contributor_email:
            return jsonify({"success": False, "error": "请先登录门户，或填写可联系邮箱"}), 400

        course_context = get_qrcode_course_context(session, course_id) if course_id else None
        if course_id and not course_context:
            return jsonify({"success": False, "error": "未找到对应课程，请从课程卡片重新进入"}), 400

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
        payload["masked_contributor_email"] = mask_contributor_email(payload["contributor_email"])
        return jsonify({
            "success": True,
            "message": "上传成功，二维码已加入共享列表",
            "data": payload,
            "stats": get_contributor_stats(session, contributor_email),
        })
    except Exception as exc:
        session.rollback()
        logger.exception(f"qrcode upload failed: {exc}")
        return jsonify({"success": False, "error": "上传失败，请稍后重试"}), 500
    finally:
        session.close()


@qrcode_bp.route("/qrcode/uploads/<path:relative_path>")
def qrcode_uploaded_file(relative_path: str):
    ensure_qrcode_upload_root()
    return send_from_directory(str(QRCODE_UPLOAD_ROOT), relative_path)
