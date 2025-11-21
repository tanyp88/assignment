# ./assignment_app/app/models.py
from app import db
from flask_login import UserMixin
from datetime import datetime
from markupsafe import Markup
import markdown
from werkzeug.security import generate_password_hash, check_password_hash

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(10), default='student', nullable=False)

    # === 反作弊神器字段 ===
    login_token = db.Column(db.String(64), unique=True, index=True)  # 当前登录的唯一令牌
    login_fingerprint = db.Column(db.Text)  # 设备指纹（浏览器特征）
    login_ip = db.Column(db.String(45))
    login_ua = db.Column(db.Text)  # User-Agent
    login_at = db.Column(db.DateTime)  # 最后登录时间
    force_logout = db.Column(db.Boolean, default=False)  # 强制踢下线标记
    # === 结束 ===
    
    email_verified = db.Column(db.Boolean, default=False)  # 是否已验证邮箱
    totp_secret = db.Column(db.String(32))  # 可选：支持 Google Authenticator

    is_active = db.Column(db.Boolean, default=False)  # 必须验证邮箱后才为 True
    email_verified_at = db.Column(db.DateTime, nullable=True)
    email_verification_token = db.Column(db.String(100), unique=True, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)  # 加 index 更快！

    # 新增 for reset password
    reset_token = db.Column(db.String(100), unique=True, nullable=True)     # 临时重置令牌
    reset_token_expires = db.Column(db.DateTime, nullable=True)            # 过期时间

    # manage_classes need lazy='select'
    enrolled_classes = db.relationship(
        'Class',
        secondary='enrolled_classes',
        backref=db.backref('students', lazy='select'), # Changed from lazy='dynamic'
        lazy='select'
    )
    taught_classes = db.relationship('Class', backref='teacher', lazy='select')

    # === 新增：兼容属性 enrollments ===
    @property
    def enrollments(self):
        """兼容旧代码：enrollments → enrolled_classes"""
        return self.enrolled_classes

    @property
    def password(self):
        raise AttributeError('password 是只写属性！')

    @password.setter
    def password(self, plaintext):
        """自动 hash 明文密码"""
        self.password_hash = generate_password_hash(plaintext)

    def verify_password(self, plaintext):
        return check_password_hash(self.password_hash, plaintext)
    # === 兼容结束 ===
    
    def __repr__(self):
        return f'<User {self.username}>'


class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    fingerprint = db.Column(db.String(64), nullable=False, unique=True)  # SHA256
    platform = db.Column(db.String(32))      # "Windows", "Android", "iPhone", "Mac"
    browser = db.Column(db.String(64))       # "Chrome/129", "Edge/129"
    device_name = db.Column(db.String(64), default="未命名设备")  # 用户可改
    is_mobile = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)  # 是否被踢




class Class(db.Model):
    __tablename__ = 'classes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assignments = db.relationship('Assignment', backref='class_', lazy='select')
    memo = db.Column(db.Text, nullable=True, default="")

class Assignment(db.Model):
    __tablename__ = 'assignments'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String     (100), nullable=False)
    description = db.Column(db.Text)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)

    # ADD THIS LINE
    weight = db.Column(db.Float, nullable=False, server_default='1.0', default=1.0)
    

class Submission(db.Model):
    __tablename__ = 'submissions'
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignments.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text)
    file_path = db.Column(db.String(200))
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    grade = db.Column(db.Integer)
    feedback = db.Column(db.Text)
    assignment = db.relationship('Assignment', backref='submissions')
    student = db.relationship('User', backref='submissions')

enrolled_classes = db.Table('enrolled_classes',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('class_id', db.Integer, db.ForeignKey('classes.id'), primary_key=True)
)

# app/models.py  (add at the end of the file)

class WeChatUser(db.Model):
    __tablename__ = 'wechat_users'

    id          = db.Column(db.Integer, primary_key=True)
    student_id  = db.Column(db.String(64), db.ForeignKey('user.student_id', onupdate='CASCADE'), nullable=False, unique=True)
    openid      = db.Column(db.String(64), nullable=False, unique=True)
    unionid     = db.Column(db.String(64), unique=True)
    nickname    = db.Column(db.String(64))
    avatar_url  = db.Column(db.String(256))
    bind_time   = db.Column(db.DateTime, default=datetime.now(), nullable=False)

    # relationship back to User (optional, handy for queries)
    user = db.relationship('User', backref=db.backref('wechat', uselist=False))

# app/models.py  (add after WeChatUser)

class Attendance(db.Model):
    __tablename__ = 'attendance'

    id          = db.Column(db.Integer, primary_key=True)
    class_id    = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    student_id  = db.Column(db.String(64), db.ForeignKey('user.student_id', onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)
    checkin_time= db.Column(db.DateTime, default=datetime.now(), nullable=False)

    # NEW: Geolocation Fields
    latitude = db.Column(db.Float, nullable=True)  
    longitude = db.Column(db.Float, nullable=True) 
    
    # 核弹级字段
    fingerprint  = db.Column(db.String(64), nullable=True, index=True)  # SHA-256 前64位或完整

    # Ensure a student can only check in once per class per session
    __table_args__ = (db.UniqueConstraint('class_id', 'student_id', 'checkin_time', name='uix_class_student_time'),)

class QQUser(db.Model):
    __tablename__ = 'qq_users'

    id          = db.Column(db.Integer, primary_key=True)
    student_id  = db.Column(db.String(64), db.ForeignKey('user.student_id'), nullable=False, unique=True)
    openid      = db.Column(db.String(64), nullable=False, unique=True)
    nickname    = db.Column(db.String(64))
    avatar_url  = db.Column(db.String(256))
    bind_time   = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', backref=db.backref('qq', uselist=False))

# app/models.py (add at the end)
class PushSubscription(db.Model):
    __tablename__ = 'push_subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='push_subscriptions')

class Announcement(db.Model):
    __tablename__ = 'announcements'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=True)  # NULL = global

    # Relationships
    author = db.relationship('User', backref='announcements', lazy='select')
    class_ = db.relationship('Class', backref='announcements', lazy='select')

    def __repr__(self):
        return f'<Announcement {self.title} ({self.class_id or "Global"})>'
    

class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)

    sender = db.relationship('User', backref='sent_messages')
    class_ = db.relationship('Class', backref='messages')

    def __repr__(self):
        return f'<Message from {self.sender.username} in class {self.class_id}>'


# === 期末项目系统模型 ===

class ProjectTopic(db.Model):
    __tablename__ = 'project_topics'
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    
    topic_title = db.Column(db.String(200), nullable=False)
    topic_description = db.Column(db.Text)
    max_students = db.Column(db.Integer, default=5)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # === 关系：双向 back_populates ===
    selections = db.relationship(
        'TopicSelection',
        back_populates='topic',
        cascade='all, delete-orphan'
    )
    submissions = db.relationship(
        'ProjectSubmission',
        back_populates='topic',
        cascade='all, delete-orphan'
    )

    class_ = db.relationship('Class', backref='project_topics')

    def current_students(self):
        return TopicSelection.query.filter_by(topic_id=self.id, is_selected=True).count()

    def is_full(self):
        return self.current_students() >= self.max_students

    @property
    def project_title(self):
        return self.topic_title

    @property
    def project_description(self):
        return self.topic_description

    @property
    def rendered_description(self):
        if not self.topic_description:
            return Markup('<em class="text-muted">无描述</em>')
        # 使用 markdown 渲染 + 安全转义
        html = markdown.markdown(
            self.topic_description,
            extensions=['fenced_code', 'tables', 'nl2br']
        )
        return Markup(html)
    
class TopicSelection(db.Model):
    __tablename__ = 'topic_selections'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic_id = db.Column(db.Integer, db.ForeignKey('project_topics.id'), nullable=False)
    selected_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_selected = db.Column(db.Boolean, default=True)

    # 唯一约束：一个学生只能选一个题
    __table_args__ = (db.UniqueConstraint('student_id', 'topic_id', name='unique_student_topic'),)

    student = db.relationship('User', backref='topic_selections')
    #topic = db.relationship('ProjectTopic', backref='selections')
    # 修复后: 使用 back_populates，明确指向 ProjectTopic 中的 'selections' 属性
    topic = db.relationship(
        'ProjectTopic', 
        back_populates='selections' # <-- 使用 back_populates 明确双向连接
    )

# models.py → ProjectSubmission
class ProjectSubmission(db.Model):
    __tablename__ = 'project_submissions'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic_id = db.Column(db.Integer, db.ForeignKey('project_topics.id'), nullable=False)  # ← 唯一外键    
    file_path = db.Column(db.String(500), nullable=True)
    content_md = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_latest = db.Column(db.Boolean, default=True)

    grade = db.Column(db.Integer)
    feedback = db.Column(db.Text)
    graded_at = db.Column(db.DateTime)

    # === 关系：双向 back_populates ===
    student = db.relationship('User', backref='project_submissions')
    topic = db.relationship('ProjectTopic', back_populates='submissions')  # ← 正确！

    def save(self, *args, **kwargs):
        if self.id:
            ProjectSubmission.query.filter(
                ProjectSubmission.student_id == self.student_id,
                ProjectSubmission.topic_id == self.topic_id,  # ← 改这里！
                ProjectSubmission.is_latest == True,
                ProjectSubmission.id != self.id
            ).update({'is_latest': False})
        else:
            ProjectSubmission.query.filter(
                ProjectSubmission.student_id == self.student_id,
                ProjectSubmission.topic_id == self.topic_id,
                ProjectSubmission.is_latest == True
            ).update({'is_latest': False})
        self.is_latest = True
        super().save(*args, **kwargs)