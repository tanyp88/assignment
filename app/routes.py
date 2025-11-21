# ./assignment_app/app/routes.py
from flask import g, Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app as app, abort, session
from flask_login import login_user, logout_user #, login_required, current_user
from app import db, logger, mail, get_redis_client, login_manager, is_logged_in , login_required, get_current_user
from app.models import User, Class, Assignment, WeChatUser, QQUser, PushSubscription, Submission,  Device
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload  
from sqlalchemy import text
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField, PasswordField, BooleanField, HiddenField
from wtforms.validators import DataRequired, Length, Email
from user_agents import parse
import uuid, random, os, json
from datetime import timedelta, datetime, timezone
from flask_mail import Mail, Message

#import logging
#logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

def is_teacher():
    return is_logged_in() and get_current_user().role == 'teacher'

def is_student():
    return is_logged_in() and get_current_user().role == 'student'
'''
@main.route('/')
@main.route('/zuoye/')
def index():
    if is_logged_in():
        if is_teacher():
            return redirect(url_for('assignments.manage_assignments'))
        return redirect(url_for('main.student_dashboard'))
    return render_template('index.html', title='Home')

@main.route('/logout')
def logout_user():
    session.clear()
    if hasattr(g, 'user'):
        g.user = None
    return redirect(url_for('main.login'))
'''

# routes.py 终极无敌版 index（复制粘贴即永生）
@main.route('/')
@main.route('/index')
@main.route('/zuoye/')
def index():
    if is_logged_in():   # ← 就这一行，也永不爆炸！is_logged_in()
        return redirect(url_for('main.student_dashboard'))
    return redirect(url_for('main.login'))

def send_verification_code(email):
    code = str(random.randint(100000, 999999))
    session['verification_code'] = code
    session['verification_email'] = email
    session['verification_time'] = datetime.now().timestamp()
    
    msg = Message(subject="【SwiftCheck】登录验证码", recipients=[email])
    msg.body = f"""
您的登录验证码是：{code}

有效期 5 分钟，请勿泄露给他人。

—— 谭也平老师
"""
    mail.send(msg)
    return code

class LoginForm(FlaskForm):
    # Add the HiddenField for the fingerprint
    #device_fingerprint = HiddenField()
    fingerprint = HiddenField()
    fingerprint_token  = HiddenField()
    d = HiddenField()
    email = StringField(
        'Email',
        validators=[
            DataRequired(message="邮箱不能为空"),
            Email(message="请输入有效的邮箱地址"),
            Length(max=120)
        ],
        render_kw={"placeholder": "your@email.com", "autocomplete": "username"}
    )
    
    password = PasswordField(
        'Password',
        validators=[
            DataRequired(message="密码不能为空"),
            Length(min=6, max=256)
        ],
        render_kw={"placeholder": "••••••••", "autocomplete": "current-password"}
    )
    
    remember_me = BooleanField('记住我（7天内免登录）', default=True)
    
    login_btn = SubmitField('Log In')

@main.route('/fingerprint/register', methods=['POST'])
def fingerprint_register():
    data = request.get_json()
    fingerprint = data.get('fingerprint')
    logger.info(f"/fingerprint/register → fingerprint:{fingerprint}")
    if not fingerprint or len(fingerprint) < 32:
        return jsonify({'status': 'error', 'msg': '无效指纹'}), 400
    
    # 生成一个一次性临时 token
    token = str(uuid.uuid4())
    
    # 存进 Redis，10 分钟有效
    get_redis_client().setex(
        f"fp_temp:{token}",
        timedelta(minutes=10),
        fingerprint
    )
    
    logger.info(f"匿名指纹注册成功 → token={token[:8]}... fp={fingerprint[:16]}...")
    
    return jsonify({
        'status': 'ok',
        'token': token
    })
def get_real_device_name(client_info: dict, ua_string: str = None) -> str:
    """
    把原始的 desktop / mobile 变成真正的设备名称
    优先级：明确机型 > 品牌+型号 > 通用描述 > 兜底
    """
    if not client_info:
        return "Unknown Device"

    # 1. iOS 设备（最精确）
    if client_info.get('platform', '').lower() == 'iphone':
        return "iPhone"
    if client_info.get('platform', '').lower() == 'ipad':
        return "iPad"
    if 'iphone' in ua_string.lower():
        return "iPhone"
    if 'ipad' in ua_string.lower():
        return "iPad"

    # 2. Android 设备（可以用 UA 里常见的机型关键词）
    if client_info.get('is_mobile') and 'android' in ua_string.lower():
        model_map = {
            'pixel 8': 'Google Pixel 8',
            'pixel 7': 'Google Pixel 7',
            'pixel 6': 'Google Pixel 6',
            'sm-g998': 'Samsung Galaxy S21 Ultra',
            'sm-g991': 'Samsung Galaxy S21',
            'sm-s908': 'Samsung Galaxy S22 Ultra',
            'xiaomi': 'Xiaomi Device',
            'redmi': 'Redmi Device',
            'oneplus': 'OnePlus Device',
        }
        ul = ua_string.lower()
        for key, name in model_map.items():
            if key in ul:
                return name
        return "Android Phone"

    # 3. Windows 电脑（你现在最关心的）
    if client_info.get('platform') == 'Win32' or 'windows' in ua_string.lower():
        screen = client_info.get('screen', '')
        touch = client_info.get('touch', False)

        # 典型 Surface 特征：高分屏 + 支持触摸
        if touch and screen in ['1920x1280', '2736x1824', '2880x1920']:
            return "Microsoft Surface Pro"

        # 高端笔记本常见分辨率
        if screen in ['2560x1440', '2560x1600', '3440x1440', '3840x2160']:
            if 'xps' in ua_string.lower():
                return "Dell XPS"
            if 'spectre' in ua_string.lower():
                return "HP Spectre"
            if 'thinkpad' in ua_string.lower():
                return "Lenovo ThinkPad"
            if 'rog' in ua_string.lower() or 'strix' in ua_string.lower():
                return "ASUS ROG Gaming Laptop"
            return "High-end Windows Laptop"

        # 普通台式机
        if not touch:
            return "Windows Desktop PC"

        return "Windows PC"

    # 4. Mac
    if 'macintosh' in ua_string.lower() or client_info.get('platform') == 'MacIntel':
        return "Apple MacBook" if client_info.get('is_mobile') else "Apple Mac"

    # 5. 兜底
    if client_info.get('is_mobile'):
        return "Mobile Device"
    return "Desktop Computer"

@main.route('/cancel_2fa', methods=['GET', 'POST'])
def cancel_2fa():
    # 强制清除所有 2FA 相关 session
    session.pop('waiting_for_2fa', None)
    session.pop('2fa_user_id', None)
    session.pop('fingerprint_token', None)
    
    # 可选：也清除 Redis 中的临时指纹
    if '2fa_user_id' in session:
        redis_client.delete(f"fingerprint_pending:{session['2fa_user_id']}")
    
    flash('已退出登录，请重新登录', 'info')
    return redirect(url_for('main.login'))

# 可选：如果你想加验证码或 2FA 预留字段
# totp_code = StringField('2FA 验证码', validators=[Optional(), Length(6,6)])
@main.route('/login', methods=['GET', 'POST'])
def login():
    logger.info(f"waiting_for_2fa: {session.get('waiting_for_2fa')}, get_current_user(): {is_logged_in()}")

    if is_logged_in():
        return redirect(url_for('main.student_dashboard'))
    
    # ============ 未激活用户直接死 ============
    if session.get('waiting_for_2fa'):
        user_id = session.get('2fa_user_id')
        if user_id:
            user = User.query.get(user_id)
            if user and not user.is_active:
                # 未激活就敢来输验证码？直接清掉！
                session.clear()
                flash('你的账号还未激活！请先点击注册邮件中的链接完成邮箱验证', 'danger')
                return redirect(url_for('main.login'))
    form = LoginForm()
    # ============ 2FA 阶段 ============
    if session.get('waiting_for_2fa'):
        logger.info("进入 2FA 阶段")

        if request.method == 'POST':
            submitted_code = request.form.get('code', '').strip()
            user_id = session.get('2fa_user_id')
            if not user_id:
                flash('会话已失效，请重新登录', 'danger')
                session.clear()
                return redirect(url_for('main.login'))

            user = User.query.get(user_id)
            if not user:
                session.clear()
                return redirect(url_for('main.login'))
           
            REDIS_CLIENT = get_redis_client()
            stored_hash = REDIS_CLIENT.get(f"2fa:{user_id}")

            if not stored_hash or not check_password_hash(stored_hash.decode(), submitted_code):
                flash('验证码错误', 'danger')
                return render_template('login_2fa.html', email=user.email,form=form)

            # 验证码正确 → 登录成功！
            REDIS_CLIENT.delete(f"2fa:{user_id}")
            '''
            fingerprint_token = (
                request.form.get('fingerprint_token') or
                session.get('fingerprint_token') or
                request.args.get('fingerprint_token')  # 备用
            )
            fingerprint = request.form.get('fingerprint')
            logger.info(f"匿名指纹提取 → fingerprint_token={fingerprint_token}")
            real_fingerprint= ''
            if fingerprint_token:
                real_fingerprint = REDIS_CLIENT.get(f"fp_temp:{fingerprint_token}")
                if real_fingerprint:
                    user.device_fingerprint = real_fingerprint
                    REDIS_CLIENT.delete(f"fp_temp:{fingerprint_token}")
                    logger.info(f"指纹永久绑定成功 ← token={fingerprint_token[:8]} fp={real_fingerprint[:16]}...")
                else:
                    logger.warning(f"指纹 token 无效或过期: {fingerprint_token}")
            else:
                logger.warning("2FA阶段未收到 fingerprint_token，设备未绑定")
            '''
            real_fingerprint = request.form.get('device_fingerprint')

            if real_fingerprint and len(real_fingerprint) > 32:
                user.device_fingerprint = real_fingerprint
                db.session.commit()
                logger.info(f"设备永久绑定成功 → {real_fingerprint[:16]}...")


            new_fingerprint = real_fingerprint
            # ================ 核心：设备存在性 + 类型判断 + 智能策略 ================
            device = Device.query.filter_by(
                #user_id=user.id, 
                fingerprint=new_fingerprint
            ).first()

            # 1. 获取前端 + 后端双重验证的设备类型
            #client_info = session.get('client_device_info', {})
            client_info = request.form.get('d') or request.get_data(as_text=True) #
            client_info_dict = json.loads(client_info)
            is_mobile_frontend = client_info_dict.get('is_mobile', False)
            ua = request.user_agent
            ua_string = request.headers.get('User-Agent', '').lower()
            is_mobile_ua = any(kw in ua_string for kw in ['iphone', 'ipad', 'android', 'mobile'])
            final_is_mobile =   is_mobile_ua and is_mobile_frontend
            
            logger.info(f"client_info → {client_info} ua {ua_string}")
            # 2. 情况一：这台设备已经存在过（老设备回来）
            if device:
                # 已经存在 → 不管是手机还是电脑，都直接激活，不踢任何人
                device.is_active = True
                device.last_seen = datetime.now()
                
                # 可选：更新设备信息（防止 UA 伪造）
                device.platform = ua.platform or device.platform
                device.browser = ua.browser or device.browser
                device.is_mobile = final_is_mobile  # 以最新判断为准
                
                logger.info(f"老设备回归 → {real_fingerprint[:16]}... {'手机' if final_is_mobile else '电脑'}")
                
            # 3. 情况二：新设备首次登录
            else:
                # 新设备 → 看是不是手机
                if final_is_mobile:
                    # 新手机 → 踢掉所有其他手机（包括已失效的）
                    kicked = Device.query.filter(
                        Device.user_id == user.id,
                        Device.is_mobile == True,
                        Device.fingerprint != real_fingerprint
                    ).update({Device.is_active: False})
                    
                    if kicked > 0:
                        flash('检测到新手机登录，已踢掉其他手机设备', 'info')
                    else:
                        flash('手机登录成功', 'success')
                else:
                    # 新电脑 → 完全不踢任何设备
                    flash('电脑登录成功，可以多开', 'success')
                
                # 创建新设备记录
                device = Device(
                    user_id=user.id,
                    fingerprint=real_fingerprint,
                    platform=client_info_dict.get('platform','Unknown'),
                    browser=getattr(ua, 'browser', 'Unknown') or 'Unknown',
                    is_mobile=final_is_mobile,
                    device_name=get_real_device_name(client_info_dict,ua_string),
                    is_active=True
                )
                db.session.add(device)
                logger.info(f"新设备加入 → {'手机' if final_is_mobile else '电脑'} {real_fingerprint[:16]}...")
                logger.info(f"真实设备名称 → {Device.device_name}")
            try:
                device.last_seen = datetime.now()
                device.is_active = True
                db.session.commit()
            except:
                db.session.rollback()
                if "Duplicate entry" in str(e) and "fingerprint" in str(e.orig):
                    # 经典场景：别人用你的设备登录 / 你用别人的设备登录
                    logger.warning(f"设备绑定冲突 → user={user.username} fingerprint={fingerprint[:12]}...")
                    flash("此设备已被其他账号绑定，禁止登录（如需解绑请联系管理员）", "danger")
                    return redirect(url_for('login'))
                else:
                    # 其他数据库错误，抛出来让 sentry 抓
                    raise

            # 反作弊 + 踢旧设备
            ua_string = request.headers.get('User-Agent', '')
            user_agent = parse(ua_string)
            #fingerprint = f"{ua_string}|{request.remote_addr}|{user_agent.os.family}|{user_agent.browser.family}"
            #fingerprint = request.form.get('fingerprint')
            new_token = str(uuid.uuid4())

            user.login_token = new_token
            user.login_fingerprint = real_fingerprint
            user.login_ip = request.remote_addr
            user.login_ua = ua_string
            user.login_at = datetime.now()
            #user.force_logout = True
            db.session.commit()

            # 登录 + 写入 session
            login_user(user, remember=session.get('remember_me', False))
            session['user_id'] = user.id
            g.user = user  # 手动存
            # 只清理 2FA 垃圾，绝不 clear 整个 session！
            for key in ['waiting_for_2fa', '2fa_user_id', '2fa_code', '2fa_expires', 'remember_me']:
                session.pop(key, None)

            session['login_token'] = new_token
            session['fingerprint'] = real_fingerprint
            session['just_logged_in'] = True
            session['logged_in'] = True
            flash(f'user{user_id} 登录成功！fingerprint:{real_fingerprint}', 'success')

            logger.info(f"session['logged_in']:{session['logged_in']} session['user_id']: {session['user_id']}" )
            logger.info(f'user {user_id} 登录成功！。fingerprint:{real_fingerprint} success redirect to main.student_dashboard' )
            return redirect(url_for('main.student_dashboard'))

        # GET 请求：显示 2FA 页面
        user_id = session.get('2fa_user_id')
        if not user_id:
            session.clear()
            return redirect(url_for('main.login'))
        user = User.query.get(user_id)
        return render_template('login_2fa.html', email=user.email,form=form)

    # ============ 密码阶段 ============
    else:
        logger.info("进入密码登录阶段")
        
        if form.validate_on_submit():
            logger.info("进入密码验证阶段")
            user = User.query.filter_by(email=form.email.data.lower()).first()
            if user and check_password_hash(user.password_hash, form.password.data):
                # 发验证码
                code = send_verification_code(user.email)
                hashed = generate_password_hash(code)
                get_redis_client().set(f"2fa:{user.id}", hashed, ex=300)

                session['waiting_for_2fa'] = True
                session['2fa_user_id'] = user.id
                session['remember_me'] = form.remember_me.data

                flash('验证码已发送到邮箱！', 'info')
                # 不 redirect！直接掉到上面分支显示 2FA 页面


                # 在你密码验证成功、准备跳转 2FA 的地方，加上这几行
                fingerprint = request.form.get('device_fingerprint') or request.form.get('fingerprint')
                if fingerprint and len(fingerprint) > 32:
                    session['pending_fingerprint'] = fingerprint
                    logger.info(f"真实指纹已缓存 → {fingerprint[:16]}...")
                else:
                    session['pending_fingerprint'] = None

                client_info_json = request.form.get('client_device_info', '{}')
                try:
                    client_info = json.loads(client_info_json)
                    session['client_device_info'] = client_info
                    logger.info(f"设备信息已缓存 → {client_info.get('type', 'unknown').upper()}")
                except:
                    logger.info(f"设备信息已缓存 → 获取失败")
                    session['client_device_info'] = {}

                return render_template('login_2fa.html', email=user.email,form=form)

            flash('邮箱或密码错误', 'danger')
        logger.info("进入登陆页面")
        return render_template('login.html', title='Login', form=form)


@login_manager.request_loader
def load_user_from_request(request):
    fp = request.form.get('device_fingerprint') or request.args.get('fp')
    if fp and is_logged_in():
        device = Device.query.filter_by(user_id=get_current_user().id, fingerprint=fp, is_active=True).first()
        if not device:
            logout_user()
            session['user_id'] = None
            g.user = None  # 手动存
            flash('此设备已被移除，请重新登录', 'danger')
            return redirect(url_for('main.login'))
    return get_current_user()

SECRET_REGISTRATION_KEY = os.environ.get('SECRET_REGISTRATION_KEY')

@main.route('/register', methods=['GET', 'POST'])
def register():
    if is_logged_in():
        return redirect(url_for('main.student_dashboard'))

    if request.method == 'POST':
        # === 你的原有校验逻辑保持不变 ===
        submitted_key = request.form.get('secret_key')
        if submitted_key != SECRET_REGISTRATION_KEY:
            flash('无效的密钥', 'danger')
            return redirect(url_for('main.register'))

        student_id = request.form.get('student_id')
        username = request.form.get('username')
        email = request.form.get('email').lower()
        password = request.form.get('password')
        role = request.form.get('role', 'student')
        class_ids = request.form.getlist('class_ids')

        password_confirm = request.form.get('password_confirm')
        if password != password_confirm:
            flash('两次输入的密码不一致！', 'danger')
            return redirect(url_for('main.register'))

        if len(password) < 6:
            flash('密码至少需要6位！', 'danger')
            return redirect(url_for('main.register'))

        # 查重
        if User.query.filter_by(email=email).first():
            flash('该邮箱已被注册', 'danger')
            return redirect(url_for('main.register'))
        if User.query.filter_by(student_id=student_id).first():
            flash('学号已被注册', 'danger')
            return redirect(url_for('main.register'))

        # === 创建未激活用户 ===
        user = User(
            student_id=student_id,
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            is_active=False,  # 关键！未验证 = 不能登录
            email_verification_token=str(uuid.uuid4())
        )
        db.session.add(user)
        db.session.flush()  # 获取 user.id

        # 关联班级
        if class_ids:
            for cid in class_ids:
                cls = Class.query.get(int(cid))
                if cls:
                    user.enrolled_classes.append(cls)

        db.session.commit()

        # === 发送验证邮件 ===
        verification_url = url_for('main.verify_email', token=user.email_verification_token, _external=True)

        msg = Message(subject="【SwiftCheck】请激活您的账号", recipients=[email])
        msg.body = f"""
谭同学，您好！

您的账号已创建成功，请点击下方链接激活（30分钟内有效）完成邮箱验证：

{verification_url}

验证后即可使用签到系统。

—— 谭也平老师
2025 年最硬核的课堂系统
"""
        try:
            mail.send(msg)
            flash('注册成功！激活链接已发送到您的邮箱，请查收并点击激活（30分钟有效）', 'success')
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            flash('注册成功，但邮件发送失败，请联系老师手动激活', 'warning')

        return redirect(url_for('main.login'))

    # GET
    classes = Class.query.all()
    return render_template('register.html', classes=classes)

@main.route('/verify-email/<token>')
def verify_email(token):
    user = User.query.filter_by(email_verification_token=token).first()
    
    if not user:
        flash('无效或已过期的激活链接', 'danger')
        return redirect(url_for('main.login'))

    if user.is_active:
        flash('账号已激活，请直接登录', 'info')
        return redirect(url_for('main.login'))

    # 激活账号
    user.is_active = True
    user.email_verified_at = datetime.now(timezone.utc)
    user.email_verification_token = None  # 用一次就废
    db.session.commit()

    flash('邮箱验证成功！现在可以登录了', 'success')
    return redirect(url_for('main.login'))

@main.route('/register1', methods=['GET', 'POST'])
def register1():
    # 1. Handle authenticated users
    if is_logged_in():
        return redirect(url_for('main.student_dashboard'))
        
    # 2. Handle POST request (Form Submission)
    if request.method == 'POST':
        # Verify secret key
        submitted_key = request.form.get('secret_key')
        if not SECRET_REGISTRATION_KEY:  # Check if secret key is configured
            flash('Registration is currently disabled. Please contact the administrator.', 'danger')
            return redirect(url_for('main.register'))
        if submitted_key != SECRET_REGISTRATION_KEY:
            flash('Invalid secret key. Please use the key provided by your instructor.', 'danger')
            return redirect(url_for('main.register'))

        student_id = request.form.get('student_id')
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role')
        class_ids = request.form.getlist('class_ids')
        
        # Check if email is already registered
        if db.session.scalar(select(User).where(User.email == email)):
            flash('Email already registered.', 'danger')
            return redirect(url_for('main.register'))

        # Check if student_id is already registered
        if db.session.scalar(select(User).where(User.student_id == student_id)):
            flash('Student ID already registered.', 'danger')
            return redirect(url_for('main.register'))

        # Create User object
        user = User(
            student_id=student_id,
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role=role
        )
        db.session.add(user)
        
        # Add classes to the user using the many-to-many relationship
        if class_ids:
            for class_id in class_ids:
                class_obj = db.session.get(Class, int(class_id))
                if class_obj:
                    user.enrolled_classes.append(class_obj)

        try:
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('main.login'))
        except db.exc.IntegrityError as e:
            db.session.rollback()
            if "Duplicate entry" in str(e) and "student_id" in str(e):
                flash('Student ID is already registered.', 'danger')
            elif "Duplicate entry" in str(e) and "username" in str(e):
                flash('Username is already taken.', 'danger')
            elif "Duplicate entry" in str(e) and "email" in str(e):
                flash('Email is already registered.', 'danger')
            else:
                flash('Registration failed due to a database error.', 'danger')
            return redirect(url_for('main.register'))

    # 3. Handle GET request (Display Form)
    classes = db.session.scalars(select(Class)).all()
    return render_template('register.html', title='Register', classes=classes)

@main.route('/cleanup-zombies')
@login_required
def cleanup_zombies():
    if not get_current_user().is_teacher:
        abort(403)

    # 7 天前注册且未激活的账号 → 全部处决
    deadline = datetime.now() - timedelta(days=7)
    
    deleted_users = User.query.filter(
        User.is_active == False,
        User.created_at < deadline
    ).all()

    count = len(deleted_users)
    
    for user in deleted_users:
        # 可选：记录日志
        logger.info(f"清理僵尸账号: {user.email} (ID: {user.student_id}, 注册于 {user.created_at})")
        db.session.delete(user)
    
    db.session.commit()
    
    flash(f'成功清理 {count} 个超过7天未激活的僵尸账号', 'success')
    return redirect(url_for('admin.dashboard'))

@main.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    # ----------------------------------------------------------------------
    # WeChat Binding Status Check
    # ----------------------------------------------------------------------
    wechat_user = WeChatUser.query.filter_by(student_id=get_current_user().student_id).first()
    wechat_is_bound = wechat_user is not None
    wechat_openid = wechat_user.openid if wechat_user else None

    status_msg = "BOUND" if wechat_is_bound else "NOT BOUND"
    openid_log = f" (OpenID: {wechat_openid})" if wechat_openid else ""
    logger.info(
        f"WeChat Status Check for Profile: User ID {get_current_user().id}, "
        f"Student ID {get_current_user().student_id} is {status_msg}{openid_log}"
    )
    # ----------------------------------------------------------------------

    # Get all classes and current enrollments for rendering
    classes = Class.query.all()
    
    result = db.session.execute(
        text("SELECT class_id FROM enrolled_classes WHERE user_id = :user_id"),
        {"user_id": get_current_user().id}
    )
    enrolled_class_ids = [row[0] for row in result.fetchall()]
    
    if request.method == 'POST':
        # Use a hidden field to determine which form was submitted
        form_type = request.form.get('form_type')

        # ----------------------------------------------------------
        # 1. Handle Profile Update (Username, Email, Classes)
        # ----------------------------------------------------------
        if form_type == 'update_profile':
            username = request.form.get('username')
            email = request.form.get('email')
            class_ids = request.form.getlist('class_ids')
            
            # 1. Validate fields
            if not username or not email:
                flash('Username and email are required.', 'error')
            # 2. Check if username is taken by another user
            elif User.query.filter(User.username == username, User.id != get_current_user().id).first():
                flash('Username is already in use.', 'error')
            # 3. Check if email is taken by another user
            elif User.query.filter(User.email == email, User.id != get_current_user().id).first():
                flash('Email is already in use.', 'error')
            
            else:
                # 4. Update core details
                get_current_user().username = username
                get_current_user().email = email
                
                # 5. Clear and add class enrollments
                db.session.execute(
                    text("DELETE FROM enrolled_classes WHERE user_id = :user_id"),
                    {"user_id": get_current_user().id}
                )
                for class_id in class_ids:
                    db.session.execute(
                        text("INSERT INTO enrolled_classes (user_id, class_id) VALUES (:user_id, :class_id)"),
                        {"user_id": get_current_user().id, "class_id": int(class_id)}
                    )
                
                # 6. Commit changes
                try:
                    db.session.commit()
                    flash('Profile updated successfully.', 'success')
                    return redirect(url_for('main.profile'))
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error updating profile: {e}")
                    flash(f'Error updating profile: {str(e)}', 'error')

        # ----------------------------------------------------------
        # 2. Handle Password Change
        # ----------------------------------------------------------
        elif form_type == 'change_password':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            # 1. Validate inputs
            if not current_password or not new_password or not confirm_password:
                flash('All password fields are required.', 'error')
            elif new_password != confirm_password:
                flash('New password and confirmation do not match.', 'error')
            elif len(new_password) < 6:
                flash('New password must be at least 6 characters long.', 'error')
            elif new_password == current_password:
                flash('New password cannot be the same as the current password.', 'error')
            
            # 2. Verify current password against stored hash
            # Assumes User model has a 'password_hash' field storing the scrypt hash
            elif not check_password_hash(get_current_user().password_hash, current_password):
                flash('The current password you entered is incorrect.', 'error')
            
            # 3. Hash and update new password
            else:
                try:
                    new_hash = generate_password_hash(new_password)
                    get_current_user().password_hash = new_hash
                    
                    db.session.commit()
                    flash('Password changed successfully!', 'success')
                    return redirect(url_for('main.profile'))
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error changing password for user {get_current_user().id}: {e}")
                    flash('An error occurred while changing the password. Please try again.', 'error')
        
        # ----------------------------------------------------------
        # 3. Handle Invalid Form
        # ----------------------------------------------------------
        else:
            flash('Invalid form submission.', 'error')

        # Re-render the page on POST failure (Profile or Password change)
        return render_template('profile.html', 
                               classes=classes, 
                               current_class_ids=enrolled_class_ids,
                               wechat_is_bound=wechat_is_bound,
                               wechat_openid=wechat_openid)
    
    # Render page on GET request
    return render_template('profile.html', 
                           classes=classes, 
                           current_class_ids=enrolled_class_ids,
                           wechat_is_bound=wechat_is_bound,
                           wechat_openid=wechat_openid)

@main.route('/profile1', methods=['GET', 'POST'])
@login_required
def profile1():
    # ----------------------------------------------------------------------
    # NEW: QQ Binding Status Check
    # ----------------------------------------------------------------------
    qq_user = QQUser.query.filter_by(student_id=get_current_user().student_id).first()
    qq_is_bound = qq_user is not None
    qq_openid = qq_user.openid if qq_user else None

    status_msg = "BOUND" if qq_is_bound else "NOT BOUND"
    openid_log = f" (OpenID: {qq_openid})" if qq_openid else ""
    logger.info(
        f"QQ Status Check for Profile: User ID {get_current_user().id}, "
        f"Student ID {get_current_user().student_id} is {status_msg}{openid_log}"
    )
    
    # Get all classes
    classes = Class.query.all()
    
    # Get user's current enrolled classes
    result = db.session.execute(
        text("SELECT class_id FROM enrolled_classes WHERE user_id = :user_id"),
        {"user_id": get_current_user().id}
    )
    enrolled_class_ids = [row[0] for row in result.fetchall()]
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        class_ids = request.form.getlist('class_ids')
        
        # Validate fields
        if not username or not email:
            flash('Username and email are required.', 'error')
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Username and email are required.')
        
        # Check if username is taken by another user
        existing_username = User.query.filter(
            User.username == username, 
            User.id != get_current_user().id
        ).first()
        if existing_username:
            flash('Username is already in use.', 'error')
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Username is already in use.')
        
        # Check if email is taken by another user
        existing_email = User.query.filter(
            User.email == email, 
            User.id != get_current_user().id
        ).first()
        if existing_email:
            flash('Email is already in use.', 'error')
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Email is already in use.')
        
        # Update username and email
        get_current_user().username = username
        get_current_user().email = email
        
        # Clear existing class enrollments
        db.session.execute(
            text("DELETE FROM enrolled_classes WHERE user_id = :user_id"),
            {"user_id": get_current_user().id}
        )
        
        # Add new class enrollments
        for class_id in class_ids:
            db.session.execute(
                text("INSERT INTO enrolled_classes (user_id, class_id) VALUES (:user_id, :class_id)"),
                {"user_id": get_current_user().id, "class_id": int(class_id)}
            )
        
        try:
            db.session.commit()
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('main.profile'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {str(e)}', 'error')
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error=f'Error updating profile: {str(e)}')
    
    return render_template('profile.html', 
                           classes=classes, 
                           current_class_ids=enrolled_class_ids,
                           qq_is_bound=qq_is_bound,
                           qq_openid=qq_openid)

@main.route('/profile_wechat', methods=['GET', 'POST'])
@login_required
def profile_wechat():
    # ----------------------------------------------------------------------
    # NEW: WeChat Binding Status Check
    # ----------------------------------------------------------------------
    # Query the WeChatUser table linked by student_id to check for binding.
    # Note: WeChatUser must be imported in this file (e.g., from app.models import WeChatUser).
    wechat_user = WeChatUser.query.filter_by(student_id=get_current_user().student_id).first()
    # Pass a simple boolean or the openid itself to the template
    wechat_is_bound = wechat_user is not None
    wechat_openid = wechat_user.openid if wechat_user else None

        # Log the binding status for debugging and monitoring
    status_msg = "BOUND" if wechat_is_bound else "NOT BOUND"
    openid_log = f" (OpenID: {wechat_openid})" if wechat_openid else ""
    logger.info(
        f"WeChat Status Check for Profile: User ID {get_current_user().id}, "
        f"Student ID {get_current_user().student_id} is {status_msg}{openid_log}"
    )
    
    # Dynamically attach an attribute to get_current_user() for template compatibility.
    # This ensures the template logic `{% if not get_current_user().wechat_openid %}` works.
    #get_current_user().wechat_openid = wechat_user.openid if wechat_user else None
    # ----------------------------------------------------------------------

    # Get all classes
    classes = Class.query.all()
    
    # Get user's current enrolled classes using EXISTING enrolled_classes table
    result = db.session.execute(
        text("SELECT class_id FROM enrolled_classes WHERE user_id = :user_id"),
        {"user_id": get_current_user().id}
    )
    enrolled_class_ids = [row[0] for row in result.fetchall()]
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        class_ids = request.form.getlist('class_ids')
        
        # Validate fields
        if not username or not email:
            flash('Username and email are required.', 'error')
            # FIX: Use current_class_ids for template variable name
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Username and email are required.')
        
        # Check if username is taken by another user
        existing_username = User.query.filter(
            User.username == username, 
            User.id != get_current_user().id
        ).first()
        if existing_username:
            flash('Username is already in use.', 'error')
            # FIX: Use current_class_ids for template variable name
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Username is already in use.')
        
        # Check if email is taken by another user
        existing_email = User.query.filter(
            User.email == email, 
            User.id != get_current_user().id
        ).first()
        if existing_email:
            flash('Email is already in use.', 'error')
            # FIX: Use current_class_ids for template variable name
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Email is already in use.')
        
        # Update username and email
        get_current_user().username = username
        get_current_user().email = email
        
        # Clear existing class enrollments using EXISTING enrolled_classes table
        db.session.execute(
            text("DELETE FROM enrolled_classes WHERE user_id = :user_id"),
            {"user_id": get_current_user().id}
        )
        
        # Add new class enrollments
        for class_id in class_ids:
            db.session.execute(
                text("INSERT INTO enrolled_classes (user_id, class_id) VALUES (:user_id, :class_id)"),
                {"user_id": get_current_user().id, "class_id": int(class_id)}
            )
        
        try:
            db.session.commit()
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('main.profile'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {str(e)}', 'error')
            # FIX: Use current_class_ids for template variable name
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error=f'Error updating profile: {str(e)}')
    
    # FIX: Use current_class_ids for template variable name in GET request
    # FIX: Pass the new template variables in GET request
    return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids,
                           wechat_is_bound=wechat_is_bound, # NEW
                           wechat_openid=wechat_openid) # NEW

# API endpoint for getting user's enrolled classes
@main.route('/api/classes')
@login_required
def get_user_classes():
    result = db.session.execute(
        text("""
        SELECT c.id, c.name 
        FROM classes c 
        JOIN enrolled_classes ec ON c.id = ec.class_id 
        WHERE ec.user_id = :user_id
        """),
        {"user_id": get_current_user().id}
    )
    user_classes = [{'id': row[0], 'name': row[1]} for row in result.fetchall()]
    return jsonify(user_classes)

@main.route('/logout')
@login_required
def logout():
    #logout_user()
    session['user_id'] = None
    g.user = None  # 手动存
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('main.login'))
'''
@main.route('/dashboard')
@login_required
def student_dashboard1():
    if is_teacher():
        title = 'Teacher Dashboard'
        user_query = select(User).where(User.id == get_current_user().id).options(
            selectinload(User.taught_classes).selectinload(Class.assignments)
        )
        user_with_data = db.session.scalars(user_query).first()
        classes = user_with_data.taught_classes if user_with_data else []
    else:
        title = 'Student Dashboard'
        user_query = select(User).where(User.id == get_current_user().id).options(
            selectinload(User.enrolled_classes).selectinload(Class.assignments)
        )
        user_with_data = db.session.scalars(user_query).first()
        classes = user_with_data.enrolled_classes if user_with_data else []
    return render_template('dashboard.html', title=title, classes=classes)

'''
@main.route('/dashboard')
@login_required
def student_dashboard():
    # Load user with associated classes, but DO NOT load assignments
    if is_teacher():
        title = 'Teacher Dashboard'
        user_query = select(User).where(User.id == get_current_user().id).options(
            selectinload(User.taught_classes) # Removed .selectinload(Class.assignments)
        )
        user_with_data = db.session.scalars(user_query).first()
        #user_with_data = user_query.first()
        classes = user_with_data.taught_classes if user_with_data else []
    else:
        title = 'Student Dashboard'
        user_query = User.query.options(
            joinedload(User.enrolled_classes)  # ← EAGER LOAD
        ).filter_by(id=get_current_user().id)
    
        user_with_data = user_query.first()
        classes = user_with_data.enrolled_classes if user_with_data else []

    # If you need to count assignments, you can access the relationship in the template,
    # or use a subquery/scalar_subquery for better performance if the list is huge.
    # For now, we'll use the template for simplicity.

    return render_template('dashboard.html', title=title, classes=classes,is_teacher=is_teacher)
    
@main.route('/manage_classes', methods=['GET'])
@login_required
def manage_classes():
    """
    Displays a list of all classes in the system for teacher management.
    Requires the user to be a teacher.
    """
    # 1. Role Check: Ensure only teachers can access this page
    if get_current_user().role != 'teacher':
        # Use Flask's abort to return a 403 Forbidden error
        abort(403) 

    # 2. Fetch all classes
    # Assuming the Class model has relationships defined to load students and assignments
    # Replace `Class.query.all()` with your actual database query logic.
    all_classes = Class.query.all()

    # 3. Render the new template
    return render_template(
        'classes/manage_classes.html',
        title='Manage All Classes',
        classes=all_classes
    )

@main.route('/manage_classes1', methods=['GET'])
@login_required
def manage_classes1():
    """Displays all classes created by the current teacher."""
    
    if not is_teacher():
        flash('Access Denied: Only teachers can manage classes.', 'danger')
        # Assuming you have a main blueprint and student_dashboard route
        return redirect(url_for('main.student_dashboard')) 

    # CORRECTED: Use SELECTINLOAD to fetch related students and assignments 
    # along with the Class objects in a single efficient query.
    try:
        classes_stmt = (
            select(Class)
            .where(Class.teacher_id == get_current_user().id)
            # FIX 1: Eagerly load the 'students' relationship (used for length calculation)
            .options(selectinload(Class.students))
            # FIX 2: Eagerly load the 'assignments' relationship (used for length calculation)
            .options(selectinload(Class.assignments)) 
            .order_by(Class.name)
        )
        
        classes = db.session.execute(classes_stmt).scalars().all()
        logger.info(
            f"Check for Profile: User ID {get_current_user().id}, "
            f"classes is {classes}"
        )
    except Exception as e:
        logger.error(f"Error fetching classes for teacher {get_current_user().id}: {e}", exc_info=True)
        flash("An error occurred while loading your classes.", 'danger')
        classes = []

    return render_template(
        'classes_manage.html', 
        classes=classes
    )

@main.route('/view_students/<int:class_id>')
@login_required
def view_students(class_id):
    # ------------------------------------------------------------------
    # 1. Teacher-only + own class
    # ------------------------------------------------------------------
    if get_current_user().role != 'teacher':
        flash('Access denied.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != get_current_user().id:
        flash('You do not teach this class.', 'danger')
        return redirect(url_for('main.teacher_dashboard'))

    # ------------------------------------------------------------------
    # 2. Build the query
    # ------------------------------------------------------------------
    # Assuming 'wechat_users' (aliased as 'w') is the correct table for 'w.openid'
    #students = db.session.execute(
    #    db.text("""
    #        SELECT
    #            u.id,
    #            u.username,
    #            u.student_id,
    #            w.openid,  -- This now correctly refers to the table aliased as 'w'
    #            COUNT(DISTINCT DATE(a.checkin_time)) AS present_days,
    #            (SELECT COUNT(DISTINCT DATE(checkin_time))
    #             FROM attendance
    #             WHERE class_id = :class_id) AS total_days
    #        FROM user u
    #        JOIN enrolled_classes ec ON u.id = ec.user_id
    #        -- FIX 1: Removed the problematic LEFT JOIN qq_users q ON u.student_id = q.student_id
    #        -- FIX 2: Added the LEFT JOIN for 'w' (wechat_users) which contains the openid
    #        LEFT JOIN wechat_users w ON u.student_id = w.student_id 
    #        LEFT JOIN attendance a
    #            ON a.student_id = u.student_id
    #           AND a.class_id   = :class_id
    #        WHERE ec.class_id = :class_id
    #        GROUP BY u.id, u.username, u.student_id, w.openid
    #        ORDER BY u.username
    #    """),
    #    {'class_id': class_id}
    #).fetchall()
    students = db.session.query(User).join(User.enrolled_classes).filter(
        Class.id == class_id,
        User.role != 'teacher'
    ).order_by(User.student_id).all()

    return render_template(
        'view_students.html',
        class_obj=cls,
        students=students
    )

@main.route('/student/<int:class_id>/<int:student_id>')
@login_required
def student_assignment_detail(class_id, student_id):
    if get_current_user().role != 'teacher':
        return redirect(url_for('main.student_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != get_current_user().id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    student = User.query.get_or_404(student_id)
    if not db.session.execute(
        db.text("SELECT 1 FROM enrolled_classes WHERE user_id = :sid AND class_id = :cid"),
        {'sid': student_id, 'cid': class_id}
    ).scalar():
        flash('Student not in class.', 'danger')
        return redirect(url_for('main.view_students', class_id=class_id))

    assignments = Assignment.query.filter_by(class_id=class_id).order_by(Assignment.due_date).all()
    submissions = {
        sub.assignment_id: sub for sub in Submission.query.filter_by(
            student_id=student_id
        ).all()
    }

    return render_template(
        'student_assignment_detail.html',
        class_obj=cls,
        student=student,
        assignments=assignments,
        submissions=submissions
    )


@main.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    data = request.get_json()
    sub = PushSubscription.query.filter_by(endpoint=data['endpoint'], user_id=get_current_user().id).first()
    if not sub:
        sub = PushSubscription(
            user_id=get_current_user().id,
            endpoint=data['endpoint'],
            p256dh=data['keys']['p256dh'],
            auth=data['keys']['auth']
        )
        db.session.add(sub)
        db.session.commit()
    return "", 201

@main.route('/vapid_public_key')
def vapid_public_key():
    return app.config['VAPID_PUBLIC_KEY']

@main.route('/update_memo/<int:class_id>', methods=['GET', 'POST'])
@login_required
def update_memo(class_id):
    if get_current_user().role != 'teacher':
        flash('Access denied.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    class_ = Class.query.get_or_404(class_id)
    if class_.teacher_id != get_current_user().id:
        flash('You can only edit your own class.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    if request.method == 'GET':
        return render_template('memo_edit.html', class_=class_)

    # POST: Append new memo
    new_text = request.form.get("new_memo", "").strip()
    if new_text:

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"[{timestamp}] {new_text}"
        class_.memo = (class_.memo or "") + ("\n" if class_.memo else "") + entry
        db.session.commit()
        flash("新备忘已添加！", "success")
    else:
        flash("内容不能为空", "danger")

    return redirect(url_for('main.student_dashboard'))


@main.route('/announcement')
@login_required
def announcement_index():
    # === ANNOUNCEMENTS ===
    from app.models import Announcement, Message
    global_announcements = Announcement.query.filter_by(class_id=None).order_by(Announcement.created_at.desc()).all()

    class_announcements = []
    if get_current_user().role == 'student':
        enrolled_classes = get_current_user().enrolled_classes
        for cls in enrolled_classes:
            anns = Announcement.query.filter_by(class_id=cls.id).order_by(Announcement.created_at.desc()).all()
            class_announcements.append({'class': cls, 'announcements': anns})
    # Teachers see all
    elif get_current_user().role == 'teacher':
        classes = Class.query.filter_by(teacher_id=get_current_user().id).all()
        for cls in classes:
            anns = Announcement.query.filter_by(class_id=cls.id).order_by(Announcement.created_at.desc()).all()
            class_announcements.append({'class': cls, 'announcements': anns})

    # === PRIVATE MESSAGES ===
    messages = []
    if get_current_user().role == 'teacher':
        # Teacher sees all messages in their classes
        messages = Message.query.join(Class).filter(
            Class.teacher_id == get_current_user().id
        ).order_by(Message.created_at.desc()).all()
    elif get_current_user().role == 'student':
        # Student sees only their own messages
        messages = Message.query.filter_by(sender_id=get_current_user().id).order_by(Message.created_at.desc()).all()

    return render_template(
        'announcements/index.html',
        global_announcements=global_announcements,
        class_announcements=class_announcements,
        messages=messages
    )

@main.route('/announcement/create', methods=['GET', 'POST'])
@login_required
def announcement_create():
    from app.models import Announcement, Message
    if get_current_user().role != 'teacher':
        flash('Only teachers can create announcements.', 'danger')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        title = request.form.get('title').strip()
        content = request.form.get('content').strip()
        class_id_str = request.form.get('class_id')  # Keep as string first

        if not title or not content:
            flash('Title and content are required.', 'danger')
            return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=get_current_user().id).all())

        # Convert class_id safely
        class_id = None
        if class_id_str:
            try:
                class_id = int(class_id_str)
                # Optional: verify teacher owns class
                if not Class.query.filter_by(id=class_id, teacher_id=get_current_user().id).first():
                    flash('Invalid class selected.', 'danger')
                    return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=get_current_user().id).all())
            except ValueError:
                flash('Invalid class ID.', 'danger')
                return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=get_current_user().id).all())

        try:
            announcement = Announcement(
                title=title.strip(),
                content=content.strip(),
                author_id=get_current_user().id,
                class_id=class_id  # NULL allowed
            )
            db.session.add(announcement)
            db.session.commit()
            flash('Announcement created!', 'success')
            return redirect(url_for('main.announcement_index'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Failed to create announcement: {e}", exc_info=True)
            flash('Failed to save announcement. Check server logs.', 'danger')
            return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=get_current_user().id).all())

    # GET
    classes = Class.query.filter_by(teacher_id=get_current_user().id).all()
    return render_template('announcements/create.html', classes=classes)
@main.route('/announcement/message', methods=['POST'])
@login_required
def send_message():
    if get_current_user().role != 'student':
        return jsonify(success=False, message="Only students can send messages"), 403

    class_id = request.form.get('class_id', type=int)
    content = request.form.get('content', '').strip()

    if not class_id or not content:
        return jsonify(success=False, message="Class and message required"), 400

    # Verify enrollment
    enrolled = db.session.execute(
        text("SELECT 1 FROM enrolled_classes WHERE user_id = :uid AND class_id = :cid"),
        {"uid": get_current_user().id, "cid": class_id}
    ).scalar()

    if not enrolled:
        return jsonify(success=False, message="Not enrolled in this class"), 403

    try:
        msg = Message(
            content=content,
            sender_id=get_current_user().id,
            class_id=class_id
        )
        db.session.add(msg)
        db.session.commit()
        return jsonify(success=True, message="Message sent!")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Message failed: {e}")
        return jsonify(success=False, message="Server error"), 500
    
@main.route('/announcement/<int:id>/delete')
@login_required
def announcement_delete(id):
    from app.models import Announcement, Message
    announcement = Announcement.query.get_or_404(id)
    if announcement.author_id != get_current_user().id:
        flash('You can only delete your own announcements.', 'danger')
        return redirect(url_for('main.announcement_index'))

    db.session.delete(announcement)
    db.session.commit()
    flash('Announcement deleted!', 'success')
    return redirect(url_for('main.announcement_index'))




# NOTE: This file assumes you have Flask-WTF installed and configured.

class CreateClassForm(FlaskForm):
    """Form for creating a new Class (Course)."""
    name = StringField(
        'Class Name', 
        validators=[DataRequired(), Length(min=2, max=100)],
        render_kw={"placeholder": "e.g., Algebra I - Block 3"}
    )
    description = TextAreaField(
        'Description', 
        validators=[Length(max=500)],
        render_kw={"rows": 4, "placeholder": "Briefly describe the course content or structure."}
    )
    submit = SubmitField('Create Class')

@main.route('/create_class', methods=['GET', 'POST'])
@login_required
def create_class():
    """Route to handle the creation of a new class."""
    if not is_teacher():
        flash('Access Denied: Only teachers can create classes.', 'danger')
        return redirect(url_for('assignments.manage_classes'))
    
    form = CreateClassForm()
    
    if form.validate_on_submit():
        new_class = Class(
            name=form.name.data,
            description=form.description.data,
            # Assign the currently logged-in teacher as the class owner
            teacher_id=get_current_user().id
        )
        
        try:
            db.session.add(new_class)
            db.session.commit()
            flash(f'Class "{new_class.name}" created successfully!', 'success')
            return redirect(url_for('assignments.manage_classes'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating new class: {e}", exc_info=True)
            flash("An error occurred while trying to save the new class. Please try again.", 'danger')

    # For GET request or failed validation, render the form
    return render_template(
        'class_create.html', 
        title='Create New Class', 
        form=form
    )

@main.route('/class/<int:class_id>/students')
@login_required
def class_students(class_id):
    if get_current_user().role != 'teacher':
        flash('Access denied.', 'danger')
        return redirect(url_for('main.manage_classes'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != get_current_user().id:
        flash('You do not teach this class.', 'danger')
        return redirect(url_for('main.manage_classes'))

    # Get enrolled students
    students = db.session.execute(
        text("""
            SELECT u.id, u.username, u.student_id
            FROM user u
            JOIN enrolled_classes ec ON u.id = ec.user_id
            WHERE ec.class_id = :class_id
            ORDER BY u.username
        """),
        {"class_id": class_id}
    ).fetchall()

    summary = []
    for s in students:
        user_id = s[0]           # ← INTEGER (user.id)
        username = s.username
        student_id = s.student_id  # ← STRING (for display)

        # CORRECT: Use user_id (int), restrict to class_id
        assign_stats = db.session.execute(
            text("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN sub.grade IS NOT NULL THEN 1 ELSE 0 END) as graded
                FROM assignments a
                LEFT JOIN submissions sub 
                    ON a.id = sub.assignment_id 
                    AND sub.student_id = :user_id
                WHERE a.class_id = :class_id
            """),
            {"class_id": class_id, "user_id": user_id}  # ← FIXED
        ).fetchone()

        # Attendance (also use user_id)
        attend_stats = db.session.execute(
            text("""
                SELECT COUNT(*) as present
                FROM attendance
                WHERE class_id = :class_id AND student_id = :student_id
            """),
            {"class_id": class_id, "student_id": student_id}
        ).fetchone()

        summary.append({
            'user_id': user_id,
            'username': username,
            'student_id': student_id,
            'assignments_total': assign_stats.total or 0,
            'assignments_graded': assign_stats.graded or 0,
            'attendance_count': attend_stats.present or 0
        })

    return render_template(
        'classes/students.html',
        cls=cls,
        students=summary
    )



# Step 1：发送重置邮件
@main.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        if not user:
            flash('如果邮箱存在，我们已发送重置链接（防枚举攻击）', 'info')
            return redirect(url_for('main.forgot_password'))
        
        # 生成 token
        from app.utils.auth import generate_reset_token
        token = generate_reset_token(email)
        user.reset_token = token
        user.reset_token_expires = datetime.now() + timedelta(minutes=30)
        db.session.commit()
        
        # 发送邮件（你用自己的 send_email 函数）

        reset_url = url_for('main.reset_password', token=token, _external=True)
        '''
        send_email(
            to=email,
            subject="【SwiftCheck】密码重置",
            template="email/reset_password.html",
            user=user,
            reset_url=reset_url
            # template 里一句就行：点击 <a href="{{ reset_url }}">这里</a> 重置密码，有效期 30 分钟
        )
        '''
        subject = "【SwiftCheck】密码重置"
        expiration_minutes = 30 # From your requirement
        msg = Message(subject=subject, recipients=[email])
        # If you need an HTML version (highly recommended for clickable links):
        msg.html = f"""
        <p>尊敬的用户：</p>
        <p>您好！您正在进行密码重置操作。</p>
        <p>请点击下方链接重置您的密码：</p>
        <p><a href="{reset_url}">点击这里重置密码</a></p>
        <p>此链接有效期为 {expiration_minutes} 分钟。为了您的账户安全，请勿泄露此邮件内容。</p>
        <p>如果您没有请求此操作，请忽略本邮件。</p>
        <br>
        <p>—— 谭也平老师</p>
        """
        # Send the message
        mail.send(msg)
        try:
            mail.send(msg)
            flash('重置链接已发送到你的邮箱，请查收', 'success')
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            flash('重置成功，但邮件发送失败，请联系老师手动激活', 'warning')
        
        return redirect(url_for('main.login'))
    
    return render_template('forgot_password.html')

# Step 2：重置密码页面
@main.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    from app.utils.auth import verify_reset_token
    email = verify_reset_token(token)
    if not email:
        flash('重置链接无效或已过期', 'danger')
        return redirect(url_for('main.login'))
    
    user = User.query.filter_by(email=email).first_or_404()
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm  = request.form.get('confirm')
        
        if password != confirm:
            flash('两次密码不一致', 'danger')
            return render_template('reset_password.html', token=token)
        
        if len(password) < 8:
            flash('密码至少 8 位', 'danger')
            return render_template('reset_password.html', token=token)
        
        user.password = password  # 你有 @password.setter 做 hash
        user.reset_token = None
        user.reset_token_expires = None
        db.session.commit()
        
        # 可选：强制登出所有旧设备（核弹级安全）
        user.login_fingerprint = None                # 旧设备指纹失效
        db.session.commit()

        flash('密码重置成功，请重新登录', 'success')
        return redirect(url_for('main.login'))
    
    return render_template('reset_password.html', token=token)