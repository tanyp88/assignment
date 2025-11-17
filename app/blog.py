from flask import Blueprint, send_from_directory, abort
from flask_login import login_required, current_user
import os
import yaml
from app.models import User, Class, enrolled_classes

blog = Blueprint('blog', __name__, url_prefix='/zuoye/blog')
STATIC_BLOG_DIR = '/app/static-blog'

def get_courses():
    path = '/app/blog-content/courses.yaml'
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
        raw = data.get('courses', []) or []
        courses = []
        for c in raw:
            courses.append({
                'id': c['id'],
                'title': c['title'],
                'description': c['description'],
                'order': c.get('order', 999),
                'class_id': c.get('class_id')
            })
        return sorted(courses, key=lambda x: x['order'])

def user_can_access_course(course_id):
    course = next((c for c in get_courses() if c['id'] == course_id), None)
    if not course or 'class_id' not in course:
        return False
    class_id = course['class_id']

    if current_user.role == 'teacher':
        return Class.query.filter_by(id=class_id, teacher_id=current_user.id).first() is not None
    else:
        #return current_user.enrolled_classes.filter_by(id=class_id).first() is not None
        return any(cls.id == class_id for cls in current_user.enrolled_classes)

@blog.route('/')
@login_required
def index():
    return send_from_directory(STATIC_BLOG_DIR, 'index.html')

@blog.route('/course/<course_id>.html')
@login_required
def course_page(course_id):
    if not user_can_access_course(course_id):
        abort(403)
    path = os.path.join(STATIC_BLOG_DIR, 'course', f'{course_id}.html')
    if not os.path.exists(path):
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@blog.route('/static/<path:filename>')
@login_required
def static_files(filename):
    parts = filename.split('/')
    if len(parts) < 3:
        abort(403)
    course_id = parts[0]
    if not user_can_access_course(course_id):
        abort(403)
    return send_from_directory(os.path.join(STATIC_BLOG_DIR, 'static'), filename)
