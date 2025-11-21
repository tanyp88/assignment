# app/api/blog.py
from flask import Blueprint, jsonify, request
#from flask_login import login_required, current_user
from datetime import datetime
from app.models import Announcement, Message, User, Class
import os
from app import db, logger, login_required, get_current_user, is_logged_in
from flask_wtf.csrf import generate_csrf

api = Blueprint('api', __name__)

@api.route('/get-csrf-token', methods=['GET'])
@login_required
def get_csrf_token_api():
    # 确保调用 generate_csrf() 来获取当前的 Token
    # 这个函数通常也会负责将 Token 设置到用户的 Session Cookie 中
    token = generate_csrf() 
    
    return jsonify({
        'csrf_token': token
    })

@api.route('/announcements/<int:class_id>')
def api_announcements(class_id):
    # 课程公告 + 全局公告（class_id IS NULL）
    logger.info(f"[API] Fetching announcements for class_id={class_id}")
    anns = Announcement.query.filter(
        db.or_(Announcement.class_id == class_id, Announcement.class_id.is_(None))
    ).order_by(Announcement.created_at.desc()).all()
    logger.info(f"[API] Fetched {len(anns)} announcements for class_id={class_id}")
    return jsonify([{
        'title': a.title or '公告',
        'date': a.created_at.strftime("%Y年%m月%d日 %H:%M"),
        'content': a.content.replace('\n', '<br>'),
        'is_global': a.class_id is None
    } for a in anns])

@api.route('/messages/<int:class_id>', methods=['GET', 'POST'])
@login_required
def handle_messages(class_id):
    # 验证班级存在 + 用户有权限
    cls = Class.query.get_or_404(class_id)
    if get_current_user().role == 'teacher':
        if cls.teacher_id != get_current_user().id:
            return jsonify({'error': '无权限'}), 403
    elif get_current_user().role == 'student':
        if cls not in get_current_user().enrolled_classes:
            return jsonify({'error': '未报名此课'}), 403
    else:
        return jsonify({'error': '角色错误'}), 403

    if request.method == 'POST':
        # CSRF 验证
        content = request.form.get('content', '').strip()
        if not content or len(content) > 1000:
            return jsonify({'error': '内容为空或过长'}), 400

        # 创建私信
        msg = Message(
            content=content,
            sender_id=get_current_user().id,
            class_id=class_id,
            created_at=datetime.utcnow()
        )
        db.session.add(msg)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': '发送成功',
            'data': {
                'content': msg.content,
                'sender': get_current_user().username,
                'student_id': get_current_user().student_id,
                'time': msg.created_at.strftime("%m-%d %H:%M")
            }
        })

    else:  # GET
        msgs = Message.query.filter_by(class_id=class_id)
        if get_current_user().role == 'student':
            msgs = msgs.filter_by(sender_id=get_current_user().id)
        msgs = msgs.order_by(Message.created_at.desc()).limit(50).all()

        return jsonify([{
            'content': m.content,
            'sender': m.sender.username,
            'student_id': m.sender.student_id,
            'time': m.created_at.strftime("%m-%d %H:%M"),
            'is_me': m.sender_id == get_current_user().id
        } for m in msgs])


@api.route('/get_current_user()')
def current_user_api():
    if not is_logged_in():
        return jsonify({'authenticated': False})
    return jsonify({
        'authenticated': True,
        'role': get_current_user().role,
        'username': get_current_user().username,
        'student_id': get_current_user().student_id,
        'is_teacher': get_current_user().role == 'teacher'
    })

@api.route('/presentations/<int:class_id>')
def list_presentations(class_id):
    path = f"/app/uploads/presentations/{class_id}"
    if not os.path.exists(path):
        return jsonify([])
    
    files = []
    for f in sorted(os.listdir(path)):
        if f.lower().endswith(('.html', '.pdf', '.pptx')):
            files.append({
                'name': f,
                'url': f"/zuoye/blog/static/{class_id}/presentations/{f}",
                'is_html': f.lower().endswith('.html')
            })
    return jsonify(files)