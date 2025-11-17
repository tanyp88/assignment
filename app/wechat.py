# -------------------------------------------------
#  WECHAT BINDING & ATTENDANCE  app/wechat.py
# -------------------------------------------------
from flask import Blueprint, render_template, request, jsonify, send_file, url_for, redirect, flash
from flask_login import login_required, current_user
import uuid, time, io, qrcode, json, redis
from datetime import datetime, timedelta
from app import db, logger, get_redis_client
from app.models import User, WeChatUser, Class, Attendance


wechat = Blueprint('wechat', __name__)

# ----------------------------------------------------------------------
# Redis Configuration and Conceptual Client Access
# ----------------------------------------------------------------------
BINDING_EXPIRY_SECONDS = 600   # 10 minutes
TOKEN_EXPIRY_SECONDS   = 120   # 2 minutes
WECHAT_BINDING_KEY_PREFIX = 'wechat:bind:' # Prefix for Redis keys
WECHAT_ATTENDANCE_KEY_PREFIX = 'wechat:attendance:'



# ------------------------------------------------------------------
# 1. Bind page
# ------------------------------------------------------------------
@wechat.route('/bind_wechat')
@login_required # Ensure only logged-in users can generate a binding link
def bind_wechat():
    """
    Generates a unique binding token and stores it in Redis with an expiration.
    """
    
    # Check if the user is already bound using the dedicated WeChatUser model
    wechat_user = WeChatUser.query.filter_by(student_id=current_user.student_id).first()
    
    if wechat_user:
        flash("You are already bound to WeChat.", 'info')
        # User is already bound, redirect to a dashboard with a success message
        return redirect(url_for('main.profile')) 

    # 1. Generate a unique token
    binding_token = str(uuid.uuid4())
    
    # 2. Store the token payload in Redis using SETEX for automatic expiration
    redis = get_redis_client()
    if redis is None:
        flash("System error: Redis service is unavailable. Cannot generate binding QR code.", 'danger')
        return redirect(url_for('main.profile'))
    
    # FIX: Ensure we are storing the ID of the user who is CURRENTLY logged in
    payload = json.dumps({'user_id': current_user.id})
    
    redis.setex(
        WECHAT_BINDING_KEY_PREFIX + binding_token, 
        BINDING_EXPIRY_SECONDS, 
        payload.encode('utf-8') # Redis expects bytes
    )
    
    logger.info(f"Generated Redis binding token for user {current_user.id}: {binding_token}")

    return render_template(
        'bind_wechat.html',
        binding_token=binding_token,
        student_id=current_user.student_id,
        username=current_user.username,
        expiry_display=(datetime.now() + timedelta(seconds=BINDING_EXPIRY_SECONDS)).strftime('%M:%S')
    )


# ------------------------------------------------------------------
# 2. Verify student (step 1)
# ------------------------------------------------------------------
@wechat.route('/verify_student', methods=['POST'])
def verify_student():
    data = request.get_json()
    student_id = data.get('student_id')
    name       = data.get('name')

    if not student_id or not name:
        return jsonify(success=False, message='学号和姓名必填')

    user = User.query.filter_by(student_id=student_id, username=name).first()
    if not user:
        return jsonify(success=False, message='学号或姓名不匹配')

    if WeChatUser.query.filter_by(student_id=student_id).first():
        return jsonify(success=False, message='该学号已绑定微信')

    # create a temporary bind token (in-memory, fine for demo)
    token = str(uuid.uuid4())
    bind_sessions[token] = {
        'student_id': student_id,
        'name': name,
        'status': 'pending',
        'created': datetime.now()
    }
    return jsonify(success=True, bind_token=token)

# ------------------------------------------------------------------
# 3. QR-code image
# ------------------------------------------------------------------
@wechat.route('/generate_bind_qrcode/<string:token>')
def generate_bind_qrcode(token):
    """Generates the QR code image for a given token, checking Redis validity."""
    
    redis = get_redis_client()
    # If the token is expired or non-existent, Redis GET returns None.
    token_data_raw = redis.get(WECHAT_BINDING_KEY_PREFIX + token)

    if not token_data_raw:
        # Handle expired or invalid token
        data_to_encode = "Token Expired or Invalid. Please refresh your browser page."
        logger.warning(f"Attempted to fetch expired or invalid Redis token: {token}")
    else:
        # Token is active
        binding_url = url_for('wechat.complete_binding', token=token, _external=True)
        data_to_encode = binding_url
    
    # 3. Generate the QR code image
    img = qrcode.make(data_to_encode)
    
    # 4. Save the image to an in-memory byte buffer
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    
    # 5. Serve the image file directly
    return send_file(
        buffer,
        mimetype='image/png',
        as_attachment=False
    )

# ------------------------------------------------------------------
# 4. Simulated WeChat OAuth callback
# ------------------------------------------------------------------
# 3. The binding completion route (WeChat callback)
@wechat.route('/complete_binding/<string:token>')
def complete_binding(token):
    """
    Conceptual route that WeChat hits after the user scans the QR code.
    Completes the binding by creating a WeChatUser record.
    """
    redis = get_redis_client()
    token_data_raw = redis.get(WECHAT_BINDING_KEY_PREFIX + token)
    
    if token_data_raw:
        # Token is valid and not expired (Redis handled the time check)
        try:
            payload = json.loads(token_data_raw)
            user_id = payload['user_id']
        except json.JSONDecodeError:
            logger.error(f"Failed to decode Redis payload for token: {token}")
            return "Binding failed: Internal token error.", 500

        # 1. Fetch the user object
        user = User.query.get(user_id)
        
        if not user:
             return "Binding failed: User not found.", 400
             
        # --- Conceptual Binding Logic ---
        
        # 2. Get WeChat OpenID from the request (Requires WeChat API setup)
        wechat_openid = f"openid_{str(uuid.uuid4())[:16]}" # Placeholder
        wechat_unionid = f"unionid_{str(uuid.uuid4())[:16]}" # Placeholder
        
        # 3. Check for existing bond again to prevent race conditions
        existing_bond = WeChatUser.query.filter_by(student_id=user.student_id).first()
        if existing_bond:
            # Clean up the token, even if the bond already exists
            redis.delete(WECHAT_BINDING_KEY_PREFIX + token) 
            return "WeChat account already bound to this user.", 200

        try:
            # 4. Create new WeChatUser record and commit
            new_wechat_user = WeChatUser(
                student_id=user.student_id,
                openid=wechat_openid,
                unionid=wechat_unionid,
                nickname="WeChat User", 
                avatar_url="", 
                bind_time=datetime.now()
            )
            db.session.add(new_wechat_user)
            db.session.commit()
            
            # 5. Clean up the token using Redis DELETE
            redis.delete(WECHAT_BINDING_KEY_PREFIX + token) 
            
            #return "WeChat binding successful! You can now use the mini-program for attendance."
            # Use HTML with a viewport tag and large heading for mobile compatibility
            html_content = """
            <!DOCTYPE html>
            <html lang="zh">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>绑定成功</title>
                <style>
                    body {
                        font-family: sans-serif;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                        background-color: #f4f7f6;
                        text-align: center;
                        padding: 20px;
                    }
                    .message-box {
                        background-color: #ffffff;
                        padding: 30px;
                        border-radius: 12px;
                        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
                    }
                    h1 {
                        color: #28a745; /* Success color */
                        font-size: 1.8rem; /* Use relative font size */
                        margin-bottom: 10px;
                    }
                    p {
                        color: #555;
                        font-size: 1.1rem;
                    }
                </style>
            </head>
            <body>
                <div class="message-box">
                    <h1>微信绑定成功!</h1>
                    <p>您现在可以使用小程序进行考勤了。</p>
                </div>
            </body>
            </html>
            """
            # Flask will automatically treat the string as HTML if it contains tags
            return html_content            
        except Exception as e:
            db.session.rollback()
            logger.error(f"DB Error during WeChat binding for user {user.id}: {e}")
            # Ensure the token is deleted if the DB transaction fails
            redis.delete(WECHAT_BINDING_KEY_PREFIX + token) 
            return "Server error during binding.", 500
    
    # Token was not found in Redis (expired or invalid)
    return "Binding failed or token expired. Please refresh the page on your browser.", 400

# ------------------------------------------------------------------
# 5. Poll bind status
# ------------------------------------------------------------------
@wechat.route('/check_bind_status')
def check_bind_status():
    student_id = request.args.get('student_id')
    for token, sess in bind_sessions.items():
        if sess['student_id'] == student_id:
            if sess['status'] == 'success':
                return jsonify(status='success', student_id=student_id, name=sess['name'])
            else:
                return jsonify(status='pending')
    return jsonify(status='not_found')

# ------------------------------------------------------------------
# 6. Teacher – scan to check attendance
# ------------------------------------------------------------------
@wechat.route('/attendance/<int:class_id>/scan')
@login_required
def attendance_scan(class_id):
    if current_user.role != 'teacher':
        flash('只有老师可以签到', 'danger')
        return redirect(url_for('main.student_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
        flash('无权管理该班级', 'danger')
        return redirect(url_for('main.student_dashboard'))

    # Generate a unique, time-limited attendance token
    attendance_token = str(uuid.uuid4())
    
    # Store token with class_id payload in Redis
    payload = json.dumps({'class_id': class_id})
    redis_client = get_redis_client()
    redis_client.setex(
        WECHAT_ATTENDANCE_KEY_PREFIX + attendance_token, 
        TOKEN_EXPIRY_SECONDS, 
        payload.encode('utf-8')
    )
    
    logger.info(f"Generated attendance token for class {class_id}: {attendance_token}")


    return render_template('attendance_scan.html', class_id=class_id, class_name=cls.name, attendance_token=attendance_token)

# ------------------------------------------------------------------
# 7. WeChat → record attendance (called from WeChat Mini-Program)
# ------------------------------------------------------------------
@wechat.route('/record_attendance', methods=['POST'])
def record_attendance():
    data = request.get_json()
    openid   = data.get('openid')
    class_id = data.get('class_id')

    wc = WeChatUser.query.filter_by(openid=openid).first()
    if not wc:
        return jsonify(success=False, message='未绑定微信')

    # Record attendance (one per day per class)
    today = datetime.now().date()
    existing = Attendance.query.filter(
        Attendance.class_id == class_id,
        Attendance.student_id == wc.student_id,
        db.func.date(Attendance.checkin_time) == today
    ).first()

    if existing:
        return jsonify(success=True, message='已签到')

    att = Attendance(class_id=class_id, student_id=wc.student_id)
    db.session.add(att)
    db.session.commit()

    return jsonify(success=True, message='签到成功')


# ------------------------------------------------------------------
# 7. Poll attendance status (FIXED/RECONFIRMED)
# ------------------------------------------------------------------
@wechat.route('/attendance/<int:class_id>/today_status')
@login_required
def get_today_attendance_status(class_id):
    """
    API endpoint for the teacher's page to poll for current attendance status.
    Returns a JSON list of students who have checked in today.
    """
    # Authorization check (Teacher must own the class)
    cls = Class.query.get(class_id)
    if not cls or cls.teacher_id != current_user.id:
        # Returning 403 (Forbidden) if unauthorized
        return jsonify(success=False, message="Unauthorized or Class not found"), 403

    today = datetime.now().date()
    
    # Use raw SQL to join Attendance and User tables to get student details and check-in time
    try:
        # We query the User table using the student_id linked in the Attendance table
        results = db.session.execute(
            db.text("""
                SELECT 
                    a.checkin_time, 
                    u.student_id, 
                    u.username 
                FROM attendance a
                JOIN user u ON a.student_id = u.student_id
                WHERE a.class_id = :cid
                  AND DATE(a.checkin_time) = :today
                ORDER BY a.checkin_time ASC
            """),
            {'cid': class_id, 'today': today}
        ).fetchall()
        
        attendees = [
            {
                'checkin_time': row.checkin_time.isoformat(), # Use isoformat for easy JS parsing
                'student_id': row.student_id,
                'name': row.username
            }
            for row in results
        ]
        
        return jsonify(success=True, attendees=attendees)
        
    except Exception as e:
        logger.error(f"Error fetching today's attendance status for class {class_id}: {e}")
        return jsonify(success=False, message="Database error"), 500
    
# ------------------------------------------------------------------
# 8. Teacher – view attendance list
# ------------------------------------------------------------------
@wechat.route('/attendance/<int:class_id>/list')
@login_required
def attendance_list(class_id):
    if current_user.role != 'teacher':
        return redirect(url_for('main.student_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
        return redirect(url_for('main.student_dashboard'))

    # All students enrolled in the class (via enrolled_classes)
    enrolled = db.session.execute(
        db.text("""
            SELECT u.id, u.student_id, u.username
            FROM user u
            JOIN enrolled_classes ec ON u.id = ec.user_id
            WHERE ec.class_id = :cid
        """),
        {'cid': class_id}
    ).fetchall()

    # Attendance today
    today = datetime.now().date()
    formatted_date = today.strftime('%B %d, %Y')
    attended = db.session.execute(
        db.text("""
            SELECT a.student_id
            FROM attendance a
            WHERE a.class_id = :cid
              AND DATE(a.checkin_time) = :today
        """),
        {'cid': class_id, 'today': today}
    ).fetchall()
    attended_ids = {row[0] for row in attended}

    return render_template(
        'attendance_list.html',
        class_name=cls.name,
        cls=cls,
        current_date_display=formatted_date,
        students=[{'id': r[0], 'student_id': r[1], 'name': r[2],
                   'present': r[1] in attended_ids} for r in enrolled]
    )

@wechat.route('/generate_attendance_qrcode/<string:token>')
def generate_attendance_qrcode(token):
    """
    Generates the QR code image using a standard HTTPS URL pointing to the check-in landing page.
    """

    # Mini-program deep link (replace with your real Mini-program path)
    #url = f"weixin://dl/business/?appid=YOUR_APPID&path=pages/checkin/checkin?class_id={class_id}"

    # The URL now points directly to a Flask route that handles the token.
    # We must include _external=True to ensure the full URL is used for the QR code.
    url = url_for('wechat.student_checkin_landing', token=token, _external=True)

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

# ------------------------------------------------------------------
# 8. Student Check-in Landing Page (NEW ROUTE)
# ------------------------------------------------------------------
@wechat.route('/attendance/checkin', methods=['GET'])
def student_checkin_landing():
    """
    The landing page a student hits after scanning the QR code (which contains an HTTPS URL).
    This route validates the attendance token and attempts to record attendance.
    """
    token = request.args.get('token')
    if not token:
        return render_template('checkin_result.html', status='error', message='错误：缺少签到令牌。')

    redis_client = get_redis_client()
    if redis_client is None:
        return render_template('checkin_result.html', status='error', message='签到失败: 系统服务不可用。')

    token_data_raw = redis_client.get(WECHAT_ATTENDANCE_KEY_PREFIX + token)
    
    if not token_data_raw:
        return render_template('checkin_result.html', status='error', message='签到失败: 令牌已过期或无效。请重新扫描二维码。')
    
    try:
        payload = json.loads(token_data_raw.decode('utf-8'))
        class_id = payload['class_id']
    except json.JSONDecodeError:
        logger.error(f"Failed to decode Redis payload for attendance token: {token}")
        return render_template('checkin_result.html', status='error', message='签到失败: 内部令牌错误。')

    # --- SIMULATION OF WECHAT OPENID RETRIEVAL ---
    # **CRITICAL**: Since a standard Webview does not automatically provide the OpenID, 
    # we must simulate this step for the demo to work. In a live system, this 
    # step would initiate a WeChat OAuth redirect.
    
    # SIMULATION: For demonstration, we assume a single bound user or retrieve
    # the first bound user to simulate a successful identification.
    wechat_user = WeChatUser.query.first() 
    if not wechat_user:
        return render_template('checkin_result.html', status='error', message='签到失败: 数据库中没有已绑定的微信用户用于模拟。')
        
    openid = wechat_user.openid # Simulated OpenID retrieval

    # Proceed to record attendance using the token and the simulated OpenID
    return record_attendance_internal(openid, class_id, token)

def record_attendance_internal(openid, class_id, token):
    """Internal function to handle the database write logic and token cleanup."""
    wc = WeChatUser.query.filter_by(openid=openid).first()
    
    if not wc:
        return render_template('checkin_result.html', status='error', message='签到失败: 您的微信未绑定到学号。')

    # Record attendance (one per day per class)
    today = datetime.now().date()
    existing = Attendance.query.filter(
        Attendance.class_id == class_id,
        Attendance.student_id == wc.student_id,
        db.func.date(Attendance.checkin_time) == today
    ).first()

    # Get Redis client to perform deletion
    redis_client = get_redis_client()
    
    if existing:
        # Delete the token even if already checked in
        if token and redis_client: redis_client.delete(WECHAT_ATTENDANCE_KEY_PREFIX + token)
        return render_template('checkin_result.html', status='success', message='您今天已成功签到。', user=wc)

    try:
        att = Attendance(class_id=class_id, student_id=wc.student_id)
        db.session.add(att)
        db.session.commit()
        
        # IMPORTANT: Delete the token after successful use
        if token and redis_client: redis_client.delete(WECHAT_ATTENDANCE_KEY_PREFIX + token)

        logger.info(f"Student {wc.student_id} checked in for class {class_id} using token.")
        return render_template('checkin_result.html', status='success', message='签到成功！', user=wc)
    except Exception as e:
        db.session.rollback()
        logger.error(f"DB Error during attendance record for user {wc.student_id}: {e}", exc_info=True)
        if token and redis_client: redis_client.delete(WECHAT_ATTENDANCE_KEY_PREFIX + token)
        return render_template('checkin_result.html', status='error', message='签到失败: 数据库操作失败。')






