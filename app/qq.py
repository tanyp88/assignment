# app/qq.py
from flask import Blueprint, render_template, request, jsonify, url_for, redirect, flash, send_file
from flask_login import login_required, current_user
import uuid, time, json, qrcode, io
from app import db, logger, get_redis_client as get_redis
from app.models import User, QQUser
from app.qq_login import QQQRLoginService

qq = Blueprint('qq', __name__)



QQ_APP_ID = "1112397047"      # ← CHANGE
QQ_APP_KEY = "gsfpnE8NBUbwwQfK"    # ← CHANGE
QQ_REDIRECT_URI = "https://chunk.cctan.ca/zuoye/qq/callback"  # ← YOUR DOMAIN

service = QQQRLoginService(QQ_APP_ID, QQ_APP_KEY)
BINDING_KEY = 'qq:bind:'

@qq.route('/bind_qq')
@login_required
def bind_qq():
    if QQUser.query.filter_by(student_id=current_user.student_id).first():
        flash("已绑定QQ", 'info')
        return redirect(url_for('main.profile'))

    token = str(uuid.uuid4())
    payload = json.dumps({'user_id': current_user.id})
    get_redis().setex(BINDING_KEY + token, 600, payload.encode())
    logger.info(f"QQ bind token: {token}")

    return render_template(
        'bind_qq.html',
        binding_token=token,
        student_id=current_user.student_id,
        username=current_user.username
    )

@qq.route('/generate_qq_qrcode/<token>')
def generate_qq_qrcode(token):
    data = get_redis().get(BINDING_KEY + token)
    if not data:
        url = "Token expired"
    else:
        callback = url_for('qq.complete_binding', token=token, _external=True)
        state = str(uuid.uuid4())
        url = service.generate_qr_url(QQ_REDIRECT_URI, state)
        get_redis().setex(f"qq:state:{state}", 600, token)  # store token by state
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@qq.route('/callback')
def qq_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    if not code or not state:
        return "Invalid", 400

    token = get_redis().get(f"qq:state:{state}")
    if not token:
        return "Expired", 400

    access_token = service.get_access_token(code, QQ_REDIRECT_URI)
    if not access_token:
        return "Auth failed", 400

    openid = service.get_openid(access_token)
    if not openid:
        return "OpenID failed", 400

    user_info = service.get_user_info(access_token, openid)
    payload = json.loads(get_redis().get(BINDING_KEY + token))
    user = User.query.get(payload['user_id'])

    if QQUser.query.filter_by(student_id=user.student_id).first():
        return "Already bound"

    qq_user = QQUser(
        student_id=user.student_id,
        openid=openid,
        nickname=user_info.get('nickname'),
        avatar_url=user_info.get('avatar')
    )
    db.session.add(qq_user)
    db.session.commit()
    get_redis().delete(BINDING_KEY + token, f"qq:state:{state}")

    return redirect(url_for('qq.bind_success'))

@qq.route('/complete_binding/<token>')
def complete_binding(token):
    return redirect(url_for('qq.bind_success'))

@qq.route('/bind_success')
def bind_success():
    return render_template('bind_success.html')