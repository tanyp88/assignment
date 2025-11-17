from datetime import datetime
from flask import Blueprint, send_from_directory, abort, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import os, yaml, mimetypes
from app.models import User, Class, ProjectTopic, TopicSelection, ProjectSubmission
from app import db, logger
from werkzeug.utils import secure_filename

project_bp = Blueprint('final_project', __name__, url_prefix='/zuoye/final_project')

'''
templates/
├── final_project/
│   ├── create.html
│   ├── manage_topics.html
│   ├── select.html
│   ├── submit.html
│   └── view_submission.html
└── macros.html  # 你已有

graph TD
    A[学生提交 .md 项目] --> B[ProjectSubmission.id = 123]
    B --> C[教师点击 AI 批改]
    C --> D[grade_submission.delay(123)]
    D --> E[任务读取 content_md / file_path]
    E --> F[Gemini 批改]
    F --> G[写入 grade + feedback]
    G --> H[页面刷新 → 显示分数]
'''

# === 教师端：项目仪表盘 ===
'''
@project_bp.route('/class/<int:class_id>/final-project')
def final_project_dashboard(class_id):
    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
        abort(403)

    topics = ProjectTopic.query.filter_by(class_id=class_id).order_by(ProjectTopic.id).all()
    if not topics:
        #return render_template('final_project/no_project.html', cls=cls)
        return redirect(url_for('final_project.create_final_project', class_id=class_id))
    project = topics[0]  # 第一个题目 = 项目代表
    return render_template('final_project/dashboard.html', cls=cls, project=project, topics=topics)
'''    

# === 创建项目 ===
# 在创建题目时，确保第一个题目包含完整项目信息
@project_bp.route('/class/<int:class_id>/create', methods=['GET', 'POST'])
@login_required
def create_final_project(class_id):
    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        topic_title = request.form['topic_title']
        topic_description = request.form['topic_description']
        max_students = request.form.get('max_students', 5, type=int)

        if not topic_title.strip():
            flash('题目名称不能为空！', 'danger')
            return render_template('final_project/create.html', cls=cls)

        topic = ProjectTopic(
            class_id=class_id,
            topic_title=topic_title,
            topic_description=topic_description,
            max_students=max_students
        )
        db.session.add(topic)
        db.session.commit()
        flash('期末项目创建成功！', 'success')
        return redirect(url_for('final_project.final_project_dashboard', class_id=class_id))

    return render_template('final_project/create.html', cls=cls)

@project_bp.route('/topic/<int:topic_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_topic(topic_id):
    topic = ProjectTopic.query.get_or_404(topic_id)
    if topic.class_.teacher_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        topic.topic_title = request.form['topic_title']
        topic.topic_description = request.form['topic_description']
        topic.max_students = request.form.get('max_students', 5, type=int)
        logger.info(
            f"topic.topic_title {topic.topic_title}, "
            f"topic.topic_description={topic.topic_description}"
            f"topic.max_students {topic.max_students}"
        )
        db.session.commit()
        flash('题目更新成功！', 'success')
        return redirect(url_for('final_project.final_project_dashboard', class_id=topic.class_id))

    return render_template('final_project/create.html', cls=topic.class_, topic=topic)

# === 管理题目 ===
@project_bp.route('/class/<int:class_id>/final-project', methods=['GET', 'POST'])
@login_required
def final_project_dashboard(class_id):
    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
        abort(403)

    topics = ProjectTopic.query.filter_by(class_id=class_id).order_by(ProjectTopic.id).all()
    if not topics:
        flash('尚未创建期末项目', 'warning')
        #return redirect(url_for('final_project.final_project_dashboard', class_id=class_id))
        return redirect(url_for('final_project.create_final_project', class_id=class_id))

    project = topics[0]  # 第一个题目 = 项目代表

    if request.method == 'POST':
        action = request.form['action']
        if action == 'add':
            topic = ProjectTopic(
                class_id=class_id,
                topic_title=request.form['title'],
                topic_description=request.form['description'],
                max_students=request.form.get('max_students', 5, type=int)
            )
            db.session.add(topic)
        elif action.startswith('delete_'):
            topic_id = int(action.split('_')[1])
            TopicSelection.query.filter_by(topic_id=topic_id).delete()
            ProjectTopic.query.filter_by(id=topic_id).delete()
        db.session.commit()
        flash('操作成功', 'success')

    return render_template(
        'final_project/manage_topics.html',
        cls=cls,
        project=project,  # 传 project
        topics=topics     # 传 topics
    )

@project_bp.route('/class/<int:class_id>/select', methods=['GET', 'POST'])
@login_required
def select_topic(class_id):
    cls = Class.query.get_or_404(class_id)
    if not any(e.id == class_id for e in current_user.enrollments):
        abort(403)

    topics = ProjectTopic.query.filter_by(class_id=class_id).order_by(ProjectTopic.id).all()
    if not topics:
        flash('期末项目尚未开放', 'info')
        return redirect(url_for('main.student_dashboard'))

    selected = TopicSelection.query.filter_by(
        student_id=current_user.id,
        is_selected=True
    ).first()

    if request.method == 'POST':
        topic_id = request.form['topic_id']
        topic = ProjectTopic.query.get_or_404(topic_id)

        if topic.is_full():
            flash('该题目已满', 'danger')
            return redirect(request.url)

        # === 关键修复：先查是否已选 ===
        existing = TopicSelection.query.filter_by(
            student_id=current_user.id,
            topic_id=topic_id
        ).first()

        if existing:
            # 已存在 → 直接更新 is_selected
            if not existing.is_selected:
                # 取消其他选择
                TopicSelection.query.filter_by(
                    student_id=current_user.id,
                    is_selected=True
                ).update({'is_selected': False})
                existing.is_selected = True
                existing.selected_at = datetime.utcnow()
                db.session.commit()
                flash(f'已重新选择：{topic.topic_title}', 'success')
        else:
            # 不存在 → 取消旧选择 + 新增
            TopicSelection.query.filter_by(
                student_id=current_user.id,
                is_selected=True
            ).update({'is_selected': False})

            selection = TopicSelection(
                student_id=current_user.id,
                topic_id=topic_id,
                is_selected=True
            )
            db.session.add(selection)
            db.session.commit()
            flash(f'已选择：{topic.topic_title}', 'success')

        return redirect(url_for('final_project.submit_project', class_id=class_id))

    logger.info(
        f"cls {cls.name}, "
        f"topics={topics}"
        f"Student ID {current_user.student_id} is selected? {selected}"
    )
    return render_template(
        'final_project/select.html',
        cls=cls,
        topics=topics,
        selected=selected
    )

@project_bp.route('/class/<int:class_id>/submit1', methods=['GET', 'POST'])
@login_required
def submit_project1(class_id):
    cls = Class.query.get_or_404(class_id)
    
    if not any(e.id == class_id for e in current_user.enrollments):
        abort(403)

    selection = TopicSelection.query.filter_by(
        student_id=current_user.id,
        is_selected=True
    ).join(ProjectTopic).filter(ProjectTopic.class_id == class_id).first()

    if not selection:
        flash('请先选择题目', 'danger')
        return redirect(url_for('final_project.select_topic', class_id=class_id))

    topic = selection.topic

    if request.method == 'POST':
        # 1. 取消旧的最新提交
        ProjectSubmission.query.filter_by(
            student_id=current_user.id,
            topic_id=topic.id,
            is_latest=True
        ).update({'is_latest': False})

        # 2. 创建新提交
        submission = ProjectSubmission(
            student_id=current_user.id,
            topic_id=topic.id,
            is_latest=True
        )

        # 文件上传
        if 'file' in request.files and request.files['file'].filename:
            file = request.files['file']
            filename = secure_filename(f"{current_user.id}_{int(datetime.utcnow().timestamp())}_{file.filename}")
            upload_dir = os.path.join('uploads', 'final_projects', str(class_id))
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, filename)
            file.save(filepath)
            submission.file_path = filepath

        submission.content_md = request.form.get('content_md', '').strip()

        # 3. 保存
        db.session.add(submission)
        db.session.commit()
        flash('提交成功！', 'success')

    latest = ProjectSubmission.query.filter_by(
        student_id=current_user.id,
        topic_id=topic.id,
        is_latest=True
    ).first()

    return render_template(
        'final_project/submit.html',
        cls=cls,
        topic=topic,
        project_title=topic.project_title,
        latest=latest
    )

from werkzeug.utils import secure_filename
from flask import request, flash, abort
import os
import mimetypes

@project_bp.route('/class/<int:class_id>/submit', methods=['GET', 'POST'])
@login_required
def submit_project(class_id):
    cls = Class.query.get_or_404(class_id)
    
    if not any(e.id == class_id for e in current_user.enrollments):
        abort(403)

    selection = TopicSelection.query.filter_by(
        student_id=current_user.id,
        is_selected=True
    ).join(ProjectTopic).filter(ProjectTopic.class_id == class_id).first()

    if not selection:
        flash('请先选择题目', 'danger')
        return redirect(url_for('final_project.select_topic', class_id=class_id))

    topic = selection.topic

    if request.method == 'POST':
        # 1. 取消旧的最新提交
        ProjectSubmission.query.filter_by(
            student_id=current_user.id,
            topic_id=topic.id,
            is_latest=True
        ).update({'is_latest': False})

        # 2. 创建新提交
        submission = ProjectSubmission(
            student_id=current_user.id,
            topic_id=topic.id,
            is_latest=True
        )

        # 文件上传：只允许 .md
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename:
                # 步骤 1：检查扩展名
                if not file.filename.lower().endswith('.md'):
                    flash('错误：只允许上传 .md 文件！', 'danger')
                    return redirect(request.url)

                # 步骤 2：检查 MIME 类型（防止伪装）
                mime_type, _ = mimetypes.guess_type(file.filename)
                if mime_type not in ['text/markdown', 'text/plain', 'application/octet-stream']:
                    flash('错误：文件类型无效，只允许 Markdown 文件！', 'danger')
                    return redirect(request.url)

                # 步骤 3：安全文件名
                original_name = file.filename
                timestamp = int(datetime.utcnow().timestamp())
                safe_name = secure_filename(f"{current_user.id}_{timestamp}_{original_name}")
                
                # 强制 .md 后缀
                if not safe_name.lower().endswith('.md'):
                    safe_name += '.md'

                upload_dir = os.path.join('uploads', 'final_projects', str(class_id))
                os.makedirs(upload_dir, exist_ok=True)
                filepath = os.path.join(upload_dir, safe_name)
                file.save(filepath)
                submission.file_path = filepath

        submission.content_md = request.form.get('content_md', '').strip()

        # 3. 保存
        db.session.add(submission)
        db.session.commit()
        flash('提交成功！', 'success')

    latest = ProjectSubmission.query.filter_by(
        student_id=current_user.id,
        topic_id=topic.id,
        is_latest=True
    ).first()

    return render_template(
        'final_project/submit.html',
        cls=cls,
        topic=topic,
        project_title=topic.project_title,
        latest=latest
    )

# API 批改（可选）
@project_bp.route('/api/grade_final_project', methods=['POST'])
@login_required
def api_grade_final_project():
    if current_user.role != 'teacher':
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json()
    from app.tasks import grade_submission  # ← 复用你的任务！
    task = grade_submission.delay(data['submission_id'])
    return jsonify({'message': '批改任务已提交', 'task_id': task.id})



@project_bp.route('/grade', methods=['POST'])
@login_required
def api_grade_project_submission():
    if current_user.role != 'teacher':
        return jsonify({'error': '仅教师可批改'}), 403

    data = request.get_json()
    submission_id = data.get('submission_id')
    submission = ProjectSubmission.query.get_or_404(submission_id)

    # 确保是本班项目
    if submission.project.class_.teacher_id != current_user.id:
        return jsonify({'error': '无权限'}), 403

    # 触发你的现有任务！
    from app.tasks import grade_submission  # ← 复用你的任务！
    task = grade_submission.delay(submission_id)
    
    return jsonify({
        'success': True,
        'message': 'AI 批改已启动，任务 ID: ' + task.id,
        'task_id': task.id
    })

@project_bp.route('/topic/<int:topic_id>/submissions')
@login_required
def view_submissions(topic_id):
    topic = ProjectTopic.query.get_or_404(topic_id)
    if topic.class_.teacher_id != current_user.id:
        abort(403)

    submissions = ProjectSubmission.query.filter_by(topic_id=topic_id).all()

    return render_template(
        'final_project/submissions.html',
        topic=topic,
        submissions=submissions
    )

@project_bp.route('/submission/<int:submission_id>/grade')
@login_required
def grade_project_submission(submission_id):
    submission = ProjectSubmission.query.get_or_404(submission_id)
    if submission.topic.class_.teacher_id != current_user.id:
        abort(403)

    from app.tasks import grade_submission
    task = grade_submission.delay(submission_id)
    flash(f'AI 批改任务已提交（ID: {task.id}），请稍后刷新查看结果', 'info')
    return redirect(url_for('final_project.view_submissions', topic_id=submission.topic_id))