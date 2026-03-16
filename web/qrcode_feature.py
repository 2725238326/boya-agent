"""
Isolated QRCode feature routes.

This module is kept intentionally separate from the main portal so future
moderation and contribution workflows can be iterated with minimal regression
risk to the core course site.
"""

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory
from loguru import logger

from src.models import get_session
from src.qrcode_service import (
    QRCODE_UPLOAD_ROOT,
    REWARD_THRESHOLDS,
    create_qrcode_upload,
    ensure_qrcode_upload_root,
    get_contributor_stats,
    is_allowed_qrcode_file,
    list_public_qrcode_uploads,
    resolve_contributor,
)

qrcode_bp = Blueprint("qrcode_feature", __name__)


def _get_portal_session_token() -> str:
    cookie_name = current_app.config.get("PORTAL_SESSION_COOKIE", "portal_token")
    return (request.cookies.get(cookie_name) or "").strip()


@qrcode_bp.route("/QRcode", strict_slashes=False)
def qrcode_page():
    return render_template("qrcode.html", reward_thresholds=REWARD_THRESHOLDS)


@qrcode_bp.route("/api/qrcode/context")
def api_qrcode_context():
    session = get_session()
    try:
        contributor = resolve_contributor(session, session_token=_get_portal_session_token())
        email = contributor.get("email") or ""
        stats = get_contributor_stats(session, email)
        return jsonify({
            "success": True,
            "data": {
                "logged_in": bool(email),
                "email": email,
                "stats": stats,
                "reward_thresholds": list(REWARD_THRESHOLDS),
            },
        })
    finally:
        session.close()


@qrcode_bp.route("/api/qrcode/uploads", methods=["GET"])
def api_qrcode_uploads():
    session = get_session()
    try:
        uploads = list_public_qrcode_uploads(session)
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

    course_name = (request.form.get("course_name") or "").strip()
    if not course_name:
        return jsonify({"success": False, "error": "请填写课程名称"}), 400

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

        upload = create_qrcode_upload(
            session,
            contributor_email=contributor_email,
            contributor_subscriber_id=contributor.get("subscriber_id"),
            course_name=course_name,
            course_time=request.form.get("course_time") or "",
            course_location=request.form.get("course_location") or "",
            notes=request.form.get("notes") or "",
            file_storage=image,
        )
        session.commit()

        payload = upload.to_dict()
        payload["image_url"] = f"/qrcode/uploads/{upload.file_path}"
        payload["contributor_upload_count"] = get_contributor_stats(session, contributor_email)["total_uploads"]
        return jsonify({
            "success": True,
            "message": "上传成功，二维码已加入共享列表",
            "data": payload,
            "stats": get_contributor_stats(session, contributor_email),
        })
    except Exception as e:
        session.rollback()
        logger.error(f"qrcode upload failed: {e}")
        return jsonify({"success": False, "error": "上传失败，请稍后重试"}), 500
    finally:
        session.close()


@qrcode_bp.route("/qrcode/uploads/<path:relative_path>")
def qrcode_uploaded_file(relative_path: str):
    ensure_qrcode_upload_root()
    return send_from_directory(str(QRCODE_UPLOAD_ROOT), relative_path)
