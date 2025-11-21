# app/attendance.py
from flask import (
    Blueprint, render_template, request, jsonify, url_for, redirect, flash,
    send_file, make_response, current_app
)
#from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from sqlalchemy.orm import selectinload
from app import db, logger, get_redis_client, csrf, login_required, get_current_user
from app.models import Class, Attendance, User, enrolled_classes
from app.push import send_push  # Your existing push system
from datetime import datetime
import uuid, qrcode, io, json

# --- Redis Helper ---
def get_redis():
    from app import get_redis_client
    return get_redis_client()

# --- Helper Functions ---

def is_teacher():
    return get_current_user().is_authenticated and get_current_user().role == 'teacher'

def is_student():
    return get_current_user().is_authenticated and get_current_user().role == 'student'
    
# --- Constants ---
ATTENDANCE_KEY_PREFIX = 'attendance:token:'
TOKEN_EXPIRY_SECONDS = 300  # 5 minutes
QR_BOX_SIZE = 25  # Large, scannable QR code

attendance = Blueprint('attendance', __name__, template_folder='templates')


# ------------------------------------------------------------------
# 1. Teacher: Generate QR Code for Attendance
# ------------------------------------------------------------------
@attendance.route('/generate/<int:class_id>')
@login_required
def generate_qr(class_id):
    if get_current_user().role != 'teacher':
        return "Forbidden: Only teachers can generate QR codes", 403

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != get_current_user().id:
        return "Forbidden: You do not teach this class", 403

    # Generate secure token
    token = str(uuid.uuid4())
    payload = {'class_id': class_id, 'teacher_id': get_current_user().id}
    get_redis().setex(
        ATTENDANCE_KEY_PREFIX + token,
        TOKEN_EXPIRY_SECONDS,
        json.dumps(payload).encode('utf-8')
    )
    logger.info(f"Attendance QR generated: class={class_id}, token={token}")

    # Generate large, scannable QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=QR_BOX_SIZE,
        border=4,
    )
    qr.add_data(token)  # Only token needed
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=False, download_name='attendance_qr.png')


# ------------------------------------------------------------------
# 2. Student: Scan & Check-in Page (via QR code)
# ------------------------------------------------------------------

@attendance.route('/student_scan/<int:class_id>')
@login_required
def student_scan(class_id):
    """
    Renders the QR scanner page for a specific class.     
    The student must still scan a *time-sensitive token* to complete check-in.
    """
    #logger = current_app.logger

    # 1. Basic role check
    if get_current_user().role != 'student':
        logger.warning(f"Non-student {get_current_user().id} tried to access class {class_id} scan page.")
        flash("Permission denied.", 'danger')
        return redirect(url_for('main.student_dashboard'))

    # 2. Look up the class to get its name
    cls = Class.query.get(class_id)
    
    if not cls:
        logger.error(f"Class ID {class_id} not found for scan request.")
        flash("签到失败: 课程不存在或链接无效。 (Check-in failed: Class not found or link is invalid.)", 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    # 3. Render the scanner page with the class context
    logger.debug(f"Rendering scan page for class {cls.id} ({cls.name}).")
    return render_template('attendance/scan.html', 
                           class_name=cls.name,
                           token='',
                           # Pass class_id for potential future logic, though not used in your current JS
                           class_id=cls.id)

# ------------------------------------------------------------------
# 3. Student: Submit Check-in (with Geolocation)
# ------------------------------------------------------------------

@attendance.route('/checkin', methods=['POST'])
@login_required
def checkin():
    if get_current_user().role != 'student':
        return jsonify(success=False, message="Only students can check in"), 403
    # ------------------------------------------------------------------
    # 0. Student identity (never trust the payload)
    # ------------------------------------------------------------------
    student_id_str = get_current_user().student_id          # string ID from user table
    student_pk     = get_current_user().id                  # integer PK

    # 1. 必须从登录态拿指纹（前端传的指纹一律不信！）
    current_fingerprint = get_current_user().login_fingerprint  # 你之前存的 SHA-256 指纹
    if not current_fingerprint:
        return jsonify(success=False, message="Device not registered"), 403

    # ------------------------------------------------------------------
    # 2. JSON payload – token + location
    # ------------------------------------------------------------------
    payload = request.get_json(silent=True) or {}
    token   = payload.get('token')
    lat     = payload.get('latitude')
    lng     = payload.get('longitude')

    if not token:
        return jsonify(success=False, message="Missing check-in token"), 400
    if lat is None or lng is None:
        return jsonify(success=False, message="Location data is required"), 400

    # --- VALIDATE TOKEN ---
    # ------------------------------------------------------------------
    # 3. Redis → class_id
    # ------------------------------------------------------------------
    raw = get_redis().get(f"{ATTENDANCE_KEY_PREFIX}{token}")
    if not raw:
        return jsonify(success=False, message="Invalid or expired QR code"), 400

    try:
        class_id = json.loads(raw).get('class_id')
    except Exception:
        return jsonify(success=False, message="Corrupted QR data"), 400

    if not class_id:
        return jsonify(success=False, message="QR payload missing class_id"), 400

    # ------------------------------------------------------------------
    # 4. Verify class exists
    # ------------------------------------------------------------------
    cls = Class.query.get(class_id)
    if not cls:
        return jsonify(success=False, message="Class not found"), 404

    # ------------------------------------------------------------------
    # 5. Verify enrollment (use PK for safety)
    # ------------------------------------------------------------------
    enrolled = db.session.execute(
        text("""
            SELECT 1 FROM enrolled_classes
            WHERE user_id = :uid AND class_id = :cid
        """),
        {"uid": student_pk, "cid": class_id}
    ).scalar()

    if not enrolled:
        return jsonify(success=False, message="Not enrolled in this class"), 403

    # ------------------------------------------------------------------
    # 6. PER-STUDENT duplicate guard (same student cannot scan twice today)
    # ------------------------------------------------------------------
    today = datetime.now().date()
    existing = Attendance.query.filter(
        Attendance.student_id == student_id_str,
        Attendance.class_id == class_id,
        db.func.date(Attendance.checkin_time) == today
    ).with_for_update().first()

    if existing:
        return jsonify(success=True, message="You have already checked in today"), 200
        

    # ------------------------------------------------------------------
    # 7. Record attendance
    # ------------------------------------------------------------------
    att = Attendance(
        class_id=class_id,
        student_id=student_id_str,
        checkin_time=datetime.now(),
        latitude=lat,
        longitude=lng,
        fingerprint=current_fingerprint  # 核弹级绑定
    )
    try:
        db.session.add(att)
        db.session.commit()
        logger.info(f"Check-in recorded: student={student_id_str}, class={class_id}")
    except IntegrityError:
        db.session.rollback()
        # Race condition – another request inserted the row first
        return jsonify(success=True, message="Already recorded (concurrent scan)"), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"DB error in checkin: {e}", exc_info=True)
        return jsonify(success=False, message="Server error"), 500

    return jsonify(success=True, message="Check-in successful!"), 200

# ------------------------------------------------------------------
# 4. Teacher: Real-time Attendance Status (Polling)
# ------------------------------------------------------------------
@attendance.route('/status/<int:class_id>')
@login_required
def get_attendance_status(class_id):
    
    if get_current_user().role != 'teacher':
        return jsonify(success=False, message="Unauthorized"), 403

    cls = Class.query.get(class_id)
    if not cls or cls.teacher_id != get_current_user().id:
        return jsonify(success=False, message="Class not found or unauthorized"), 403

    today = datetime.now().date()

    results = db.session.execute(
        db.text("""
            SELECT a.checkin_time, u.id, u.username, u.student_id
            FROM attendance a
            JOIN user u ON a.student_id = u.student_id
            WHERE a.class_id = :cid AND DATE(a.checkin_time) = :today
            ORDER BY a.checkin_time ASC
        """),
        {'cid': class_id, 'today': today}
    ).fetchall()

    attendees = [
        {
            'checkin_time': row.checkin_time.isoformat(),
            'user_id': row.id,
            'name': row.username,
            'student_id': row.student_id
        }
        for row in results
    ]

    return jsonify(success=True, attendees=attendees)

# --------------------------------------------------------------
# 8. Teacher – Toggle Attendance via AJAX (POST)
# --------------------------------------------------------------


@attendance.route('/<int:class_id>/toggle', methods=['POST'])
@login_required
def toggle_attendance(class_id):
    logger = current_app.logger
    logger.debug(f"[TOGGLE] User {get_current_user().id} toggling class {class_id}")

    if get_current_user().role != 'teacher':
        logger.warning(f"[TOGGLE] Forbidden: user {get_current_user().id} role {get_current_user().role}")
        return jsonify(success=False, message="Forbidden"), 403

    cls = Class.query.get(class_id)
    if not cls or cls.teacher_id != get_current_user().id:
        logger.warning(f"[TOGGLE] Unauthorized: class {class_id} owner {cls.teacher_id if cls else None}")
        return jsonify(success=False, message="Unauthorized"), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    # This 'user_pk' is likely the integer ID (2) from the URL or form
    user_pk = data.get('student_id')
    action = data.get('action')

    if not user_pk or action not in ('mark_present', 'mark_absent'):
        return jsonify(success=False, message="Invalid request parameters"), 400

    # --- FIX: Query by the User's Primary Key (id), not the student_id string ---
    from app.models import User # Ensure User model is accessible here
    
    # 1. Look up the User object using the primary key (id=2 in this case)
    # We rename the variable to avoid confusion and use the more generic .get()
    student_user = User.query.get(user_pk) 
    
    if not student_user:
        logger.warning(f"[TOGGLE] Integrity Error: User ID {user_pk} not found in User table.")
        return jsonify(success=False, message=f"User ID {user_pk} does not exist in system."), 400

    if student_user.role != 'student':
        logger.warning(f"[TOGGLE] Role Error: ID {user_pk} is not a student.")
        return jsonify(success=False, message="Provided ID is not a student."), 400
    
    # 2. Extract the actual formatted student_id string for the Attendance table
    # This is the value the FK constraint expects (e.g., '00000002')
    db_student_id = student_user.student_id 
    # --- FIX ENDS HERE ---

    today = datetime.now().date()

    try:
        if action == 'mark_present':
            existing = Attendance.query.filter(
                # Use the database-formatted ID for the query
                Attendance.student_id == db_student_id,
                Attendance.class_id == class_id,
                db.func.date(Attendance.checkin_time) == today
            ).with_for_update().first()

            if existing:
               return jsonify(success=True, new_status='present')
    
            if not existing:
                att = Attendance(
                    class_id=class_id,
                    # 3. Use the database-formatted ID for the insertion
                    student_id=db_student_id, 
                    checkin_time=datetime.now() # Use datetime.now() for full timestamp
                )
                # Note: We are ignoring latitude/longitude here as they are optional/defaulted
                db.session.add(att)
                db.session.commit()
                logger.debug(f"[TOGGLE] Marked present: student {db_student_id}")
            return jsonify(success=True, new_status='present')

        else:  # mark_absent
            deleted = Attendance.query.filter(
                # Use the database-formatted ID for the query
                Attendance.student_id == db_student_id,
                Attendance.class_id == class_id,
                db.func.date(Attendance.checkin_time) == today
            ).delete()
            db.session.commit()
            logger.debug(f"[TOGGLE] Marked absent: student {db_student_id}, deleted {deleted}")
            return jsonify(success=True, new_status='absent')

    except Exception as e:
        db.session.rollback()
        logger.error(f"[TOGGLE] DB error: {e}", exc_info=True)
        # 500 is correct for database errors that slip past checks
        return jsonify(success=False, message="Database error"), 500

# --------------------------------------------------------------
# 6. Teacher – Scan-to-Check Attendance (re-use the old logic)
# --------------------------------------------------------------
@attendance.route('/attendance/<int:class_id>/scan')
@login_required
def attendance_scan(class_id):
    """Same logic as the old wechat version – only the blueprint changed."""
    if get_current_user().role != 'teacher':
        flash('只有老师可以签到', 'danger')
        return redirect(url_for('main.student_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != get_current_user().id:
        flash('无权管理该班级', 'danger')
        return redirect(url_for('main.student_dashboard'))

    # ---- token generation (unchanged) ----
    attendance_token = str(uuid.uuid4())
    payload = json.dumps({'class_id': class_id})
    redis_client = get_redis_client()
    redis_client.setex(
        ATTENDANCE_KEY_PREFIX + attendance_token,
        TOKEN_EXPIRY_SECONDS,
        payload.encode('utf-8')
    )
    logger.info(f"Generated attendance token for class {class_id}: {attendance_token}")

    # ---- render the same template ----
    return render_template(
        'attendance/attendance_scan.html',
        class_id=class_id,
        class_name=cls.name,
        attendance_token=attendance_token
    )

# --------------------------------------------------------------
# QR-code image endpoint (replaces wechat.generate_attendance_qrcode)
# --------------------------------------------------------------
@attendance.route('/generate_attendance_qrcode/<string:token>')
@login_required
def generate_attendance_qrcode(token):
    """
    Returns a **large, scannable** PNG QR code that points to the
    check-in landing page (the same URL the old WeChat version used).
    """
    # Build the *exact* URL the old page expected
    url = url_for('attendance.student_checkin_landing',
                  token=token,
                  _external=True)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=25,          # ← big, easy to scan
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

# --------------------------------------------------------------
# Internal helper – shared by landing page and API check-in
# --------------------------------------------------------------
@login_required
def record_attendance_internal(student_id: int, class_id: int, token: str = None):
    """
    Records attendance for a *logged-in* student.
    Returns a rendered `attendance/checkin_result.html` template.
    """
    from app.models import Attendance, User, Class

    # 1. Verify student exists
    student = User.query.filter_by(id=student_id).first()
    if not student:
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='签到失败: 学生信息未找到。')

    # 2. Verify enrollment
    enrolled = db.session.execute(
        db.text("SELECT 1 FROM enrolled_classes WHERE user_id = :uid AND class_id = :cid"),
        {"uid": student.id, "cid": class_id}
    ).scalar()
    if not enrolled:
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='签到失败: 您未加入该班级。')

    # 3. Already checked-in today?
    today = datetime.now().date()
    existing = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.class_id == class_id,
        db.func.date(Attendance.checkin_time) == today
    ).first()

    #redis_client = get_redis_client()
    if existing:
        #if token and redis_client:
        #    redis_client.delete(ATTENDANCE_KEY_PREFIX + token)
        return render_template('attendance/checkin_result.html',
                               status='success',
                               message='您今天已成功签到。',
                               user=student)

    # 4. Record new attendance
    try:
        att = Attendance(
            class_id=class_id,
            student_id=student.student_id,
            checkin_time=datetime.now(),
            latitude=None,   # optional – you can collect it elsewhere
            longitude=None
        )
        db.session.add(att)
        db.session.commit()

        # 5. Clean up token
        #if token and redis_client:
        #    redis_client.delete(ATTENDANCE_KEY_PREFIX + token)

        logger.info(f"Student {student.student_id} checked in for class {class_id}")
        return render_template('attendance/checkin_result.html',
                               status='success',
                               message='签到成功！',
                               user=student)

    except Exception as e:
        db.session.rollback()
        logger.error(f"DB error during attendance: {e}", exc_info=True)
        #if token and redis_client:
        #    redis_client.delete(ATTENDANCE_KEY_PREFIX + token)
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='签到失败: 数据库错误。')
    
# --------------------------------------------------------------
# 8. Student Check-in Landing Page (HTTPS QR → Web)
# --------------------------------------------------------------
'''
@attendance.route('/attendance/checkin', methods=['GET'])
@login_required
def student_checkin_landing():
    """
    Student scans the QR code → lands here.
    The token is validated, the *logged-in* student is used,
    and attendance is recorded immediately.
    """
    token = request.args.get('token')
    if not token:
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='错误：缺少签到令牌。')

    redis_client = get_redis_client()
    if not redis_client:
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='签到失败: 系统服务不可用。')

    raw = redis_client.get(ATTENDANCE_KEY_PREFIX + token)
    if not raw:
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='签到失败: 令牌已过期或无效。请重新扫描二维码。')

    try:
        payload = json.loads(raw.decode('utf-8'))
        class_id = payload['class_id']
    except json.JSONDecodeError:
        logger.error(f"Failed to decode Redis payload for token: {token}")
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='签到失败: 内部令牌错误。')

    # --------------------------------------------------------------
    #  **No more WeChat OpenID simulation**
    #  Use the *currently logged-in* student (must be a student role)
    # --------------------------------------------------------------
    if get_current_user().role != 'student':
        return render_template('attendance/checkin_result.html',
                               status='error',
                               message='签到失败: 仅限学生账号。')

    # Pass the *student id* (not openid) to the internal helper
    return record_attendance_internal(
        student_id=get_current_user().id,
        class_id=class_id,
        token=token
    )
'''

@attendance.route('/attendance/<int:class_id>/list')
@login_required
def attendance_list(class_id):
    if get_current_user().role != 'teacher':
        flash('只有老师可以查看签到名单', 'danger')
        return redirect(url_for('main.teacher_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != get_current_user().id:
        flash('无权管理该班级', 'danger')
        return redirect(url_for('main.teacher_dashboard'))

    # Enrolled students
    enrolled = db.session.execute(
        db.text("""
            SELECT u.id, u.student_id, u.username
            FROM user u
            JOIN enrolled_classes ec ON u.id = ec.user_id
            WHERE ec.class_id = :cid
            ORDER BY u.username
        """),
        {'cid': class_id}
    ).fetchall()

    # Today's attendance
    today = datetime.now().date()
    formatted_date = today.strftime('%B %d, %Y')

    attended = db.session.execute(
        db.text("""
            SELECT student_id
            FROM attendance
            WHERE class_id = :cid
              AND DATE(checkin_time) = :today
        """),
        {'cid': class_id, 'today': today}
    ).fetchall()
    attended_ids = {row[0] for row in attended}

    students = [
        {
            'id': r[0],
            'student_id': r[1],
            'name': r[2],
            'present': r[1] in attended_ids
        }
        for r in enrolled
    ]

    # ADD THIS
    total_students = len(students)
    if students == None:
        total_students = 0

    return render_template(
        'attendance/list.html',
        class_name=cls.name,
        cls=cls,
        current_date_display=formatted_date,
        students=students,
        total_students=total_students   # PASS IT
    )

# app/attendance.py
import csv
from io import StringIO
import urllib.parse
from flask import Response, render_template, redirect, url_for, flash
from datetime import date

@attendance.route('/class/<int:class_id>/attendance/report')
@login_required
def attendance_report(class_id):
    if not is_teacher():
        flash('Only teachers can view reports.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    cls = db.session.get(Class, class_id)
    if not cls or cls.teacher_id != get_current_user().id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('main.teacher_dashboard'))

    # Get all attendance dates (unique dates from checkin_time)
    dates = db.session.execute(
        db.text("""
            SELECT DISTINCT DATE(checkin_time) as session_date
            FROM attendance
            WHERE class_id = :cid
            ORDER BY session_date
        """),
        {'cid': class_id}
    ).fetchall()
    session_dates = [row.session_date for row in dates]

    if not session_dates:
        return render_template('attendance/report.html', cls=cls, session_dates=[], students=[])

    # Get students (exclude teachers)
    students = db.session.execute(
        db.text("""
            SELECT u.id, u.student_id, u.username
            FROM user u
            JOIN enrolled_classes ec ON u.id = ec.user_id
            WHERE ec.class_id = :cid AND u.role != 'teacher'
            ORDER BY u.username
        """),
        {'cid': class_id}
    ).fetchall()

    # Build attendance map: {student_id: {date: True/False}}
    attendance_map = {}
    for student in students:
        sid = student.student_id  # Note: student_id is String(64)
        attendance_map[sid] = {
            'student_id': student.student_id,
            'username': student.username,
            'present_count': 0,
            'total_sessions': len(session_dates),
            'attendance': {}
        }

    # Fill attendance
    records = Attendance.query.filter_by(class_id=class_id).all()
    for rec in records:
        sid = rec.student_id
        sess_date = rec.checkin_time.date()
        if sid in attendance_map:
            attendance_map[sid]['attendance'][sess_date] = True
            attendance_map[sid]['present_count'] += 1

    # Mark absent
    for sid, data in attendance_map.items():
        for d in session_dates:
            data['attendance'].setdefault(d, False)

    return render_template(
        'attendance/report.html',
        cls=cls,
        session_dates=session_dates,
        attendance_map=attendance_map
    )

@attendance.route('/class/<int:class_id>/attendance/report/csv')
@login_required
def attendance_report_csv(class_id):
    if not is_teacher():
        return redirect(url_for('main.student_dashboard'))

    cls = db.session.get(Class, class_id)
    if not cls or cls.teacher_id != get_current_user().id:
        return redirect(url_for('main.teacher_dashboard'))

    session_dates = db.session.execute(
        db.text("""
            SELECT DISTINCT DATE(checkin_time) as session_date
            FROM attendance WHERE class_id = :cid ORDER BY session_date
        """),
        {'cid': class_id}
    ).fetchall()
    session_dates = [row.session_date for row in session_dates]

    if not session_dates:
        flash('No attendance data.', 'info')
        return redirect(url_for('attendance.attendance_report', class_id=class_id))

    students = db.session.execute(
        db.text("""
            SELECT u.student_id, u.username
            FROM user u
            JOIN enrolled_classes ec ON u.id = ec.user_id
            WHERE ec.class_id = :cid AND u.role != 'teacher'
            ORDER BY u.username
        """),
        {'cid': class_id}
    ).fetchall()

    output = StringIO()
    writer = csv.writer(output)

    # Header
    headers = ['Student ID', 'Name']
    headers.extend([d.strftime('%Y-%m-%d') for d in session_dates])
    headers.extend(['Present', 'Total', 'Rate (%)'])
    writer.writerow(headers)

    # Rows
    records = Attendance.query.filter_by(class_id=class_id).all()
    present_count = {s.student_id: 0 for s in students}

    for rec in records:
        sid = rec.student_id
        if sid in present_count:
            present_count[sid] += 1

    for student in students:
        sid = student.student_id
        row = [sid, student.username]
        presents = 0
        for d in session_dates:
            has_record = any(
                r.student_id == sid and r.checkin_time.date() == d
                for r in records
            )
            row.append('Present' if has_record else 'Absent')
            if has_record:
                presents += 1

        rate = round(presents / len(session_dates) * 100, 1) if session_dates else 0
        row.extend([presents, len(session_dates), rate])
        writer.writerow(row)

    output.seek(0)
    safe_name = urllib.parse.quote(f"{cls.name}_attendance_report.csv", safe=' _-')

    return Response(
        output,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{safe_name}"',
            'Cache-Control': 'no-cache'
        }
    )

def checkin_internal(token: str, latitude, longitude):
    """Shared logic for both GET+JS and POST"""
    raw = get_redis().get(f"{ATTENDANCE_KEY_PREFIX}{token}")
    if not raw:
        return jsonify(success=False, message="Invalid or expired QR code"), 400

    try:
        class_id = json.loads(raw).get('class_id')
    except:
        return jsonify(success=False, message="Corrupted QR data"), 400

    # ... [reuse your existing checkin() logic] ...
    # enrollment, duplicate check, DB insert

    att = Attendance(
        class_id=class_id,
        student_id=get_current_user().student_id,
        checkin_time=datetime.now(),
        latitude=latitude,
        longitude=longitude
    )
    try:
        db.session.add(att)
        db.session.commit()
        return jsonify(success=True, message="Check-in successful!"), 200
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message="Server error"), 500
    
@attendance.route('/attendance/checkin', methods=['GET', 'POST'])
@csrf.exempt
@login_required
def student_checkin_landing():
    token = request.args.get('token')
    if not token and request.method == 'POST':
        data = request.get_json() or {}
        token = data.get('token')

    if not token:
        return render_template('attendance/checkin_result.html', status='error', message='缺少令牌')

    if request.method == 'POST':
        data = request.get_json() or {}
        return checkin_internal(token, data.get('latitude'), data.get('longitude'))

    # GET → WeChat landing
    raw = get_redis().get(f"{ATTENDANCE_KEY_PREFIX}{token}")
    if not raw:
        return render_template('attendance/checkin_result.html', status='error', message='令牌无效')

    try:
        payload = json.loads(raw.decode('utf-8'))
        class_id = payload['class_id']
    except:
        return render_template('attendance/checkin_result.html', status='error', message='内部错误')

    return render_template('attendance/wechat_checkin.html', token=token, class_id=class_id)

@attendance.route('/checkin_status/<int:class_id>')
@login_required
def checkin_status(class_id):
    if get_current_user().role != 'student':
        return jsonify(checked_in=False)

    today = datetime.now().date()
    exists = Attendance.query.filter(
        Attendance.student_id == get_current_user().student_id,
        Attendance.class_id == class_id,
        db.func.date(Attendance.checkin_time) == today
    ).first()

    return jsonify(checked_in=bool(exists))