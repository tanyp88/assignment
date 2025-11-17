# assignment_app/blog_generator.py
import os
import markdown
import shutil
from datetime import datetime
from flask_frozen import Freezer
from flask import Flask, url_for
from jinja2 import Environment, FileSystemLoader

# 关键：只创建最小化 app，只注册博客路由！
from app import create_app

# 创建完整 app（用于 db 访问）
full_app = create_app()



# 创建一个全新的轻量 app，只用于冻结博客
blog_app = Flask(__name__)
blog_app.config['FREEZER_DESTINATION'] = '/app/static-blog'
blog_app.config['FREEZER_BASE_URL'] = 'https://chunk.cctan.ca/zuoye/blog/'
blog_app.config['FREEZER_RELATIVE_URLS'] = True

# === 关键修复：注册 strftime 过滤器 ===
from markupsafe import Markup
from datetime import datetime

# 注册 Flask 常用过滤器（重点！）
@blog_app.template_filter('strftime')
def _jinja2_filter_strftime(dt, fmt):
    if dt is None:
        dt = datetime.now()
    if not hasattr(dt, 'strftime'):
        dt = datetime.now()
    return dt.strftime(fmt)

# 可选：再加几个常用过滤器，避免以后再踩坑
@blog_app.template_filter('datetime')
def _jinja2_filter_datetime(value, format="%Y-%m-%d %H:%M"):
    if value is None:
        return ""
    return value.strftime(format)

@blog_app.template_filter('markdown')
def _jinja2_filter_markdown(text):
    return Markup(markdown.markdown(text or ""))


freezer = Freezer(blog_app)

CONTENT_DIR = '/app/blog-content'
TEMPLATE_DIR = os.path.join(CONTENT_DIR, 'templates')
env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

# 从数据库读取数据（使用 full_app 的 db 上下文）
def get_all_classes():
    with full_app.app_context():
        from app.models import Class
        classes = Class.query.order_by(Class.id).all()
        return [
            {
                'id': f"class{c.id}",
                'name': c.name or f"班级 {c.id}",
                'instructor': c.teacher.username if c.teacher and c.teacher.username else "谭也平",
                'semester': "2025 秋季学期",
                'description': c.description or "结构力学与数值模拟实践课程",
                'banner': "https://images.unsplash.com/photo-1581093450021-4a7360e9a6b5?w=1600&q=80"
            }
            for c in classes
        ]

def load_announcements_for_class(class_id):
    with full_app.app_context():
        from app.models import Announcement
        announcements = Announcement.query.filter_by(class_id=class_id)\
            .order_by(Announcement.created_at.desc()).all()
        return [
            {
                'title': a.title or "公告",
                'date': a.created_at.strftime("%Y年%m月%d日"),
                'content': markdown.markdown(a.content or "")
            }
            for a in announcements
        ]

def load_presentations_for_class(class_id):
    upload_dir = "/app/uploads/presentations"
    class_dir = os.path.join(upload_dir, str(class_id))
    items = []
    if os.path.exists(class_dir):
        for f in sorted(os.listdir(class_dir)):
            if f.lower().endswith(('.pdf', '.pptx', '.html', '.zip')):
                items.append({'file': f, 'name': f.split('_', 1)[-1] if '_' in f else f})
    return items

# 注册博客路由到轻量 app
@blog_app.route('/')
def index():
    courses = get_all_classes()
    template = env.get_template('index.html.j2')
    return template.render(courses=courses, now=datetime.now())

@blog_app.route('/course/class<int:class_id>.html')
def course_page(class_id):
    with full_app.app_context():
        from app.models import Class
        cls = Class.query.get_or_404(class_id)
        course = {
            'id': f"class{class_id}",
            'name': cls.name,
            'instructor': cls.teacher.username if cls.teacher else "谭也平",
            'semester': "2025 秋季学期",
            'description': cls.description or "暂无简介"
        }
        ann = load_announcements_for_class(class_id)
        pres = load_presentations_for_class(class_id)
        template = env.get_template('course.html.j2')
        return template.render(course=course, class_id=class_id, ann=ann, pres=pres)

# 告诉 freezer 只生成这些路由
@freezer.register_generator
def index():
    yield '/'

@freezer.register_generator
def course_page():
    with full_app.app_context():
        from app.models import Class
        for cls in Class.query.all():
            yield {'class_id': cls.id}

if __name__ == '__main__':
    with full_app.app_context():
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S CST')}] 开始生成静态博客...")
        print(f"检测到 {len(get_all_classes())} 个班级")

        # 清空旧目录
        if os.path.exists(blog_app.config['FREEZER_DESTINATION']):
            shutil.rmtree(blog_app.config['FREEZER_DESTINATION'])

        # 冻结
        freezer.freeze()

        # 复制课件
        static_root = os.path.join(blog_app.config['FREEZER_DESTINATION'], 'static')
        os.makedirs(static_root, exist_ok=True)
        src_base = "/app/uploads/presentations"
        if os.path.exists(src_base):
            for class_id in os.listdir(src_base):
                src = os.path.join(src_base, class_id)
                dst = os.path.join(static_root, class_id, "presentations")
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    print(f"课件复制: {class_id}")

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S CST')}] 全部完成！")
        print("访问：https://chunk.cctan.ca/zuoye/blog/")