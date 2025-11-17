# ./assignment_app/app/routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app as app, abort
from flask_login import login_user, logout_user, login_required, current_user
from app import db, logger
from app.models import User, Class, Assignment, WeChatUser, QQUser, PushSubscription, Submission
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload  
from sqlalchemy import text
#import logging
#logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

def is_teacher():
    return current_user.is_authenticated and current_user.role == 'teacher'

def is_student():
    return current_user.is_authenticated and current_user.role == 'student'

@main.route('/')
@main.route('/zuoye/')
def index():
    if current_user.is_authenticated:
        if is_teacher():
            return redirect(url_for('assignments.manage_assignments'))
        return redirect(url_for('main.student_dashboard'))
    return render_template('index.html', title='Home')

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if is_teacher():
            return redirect(url_for('assignments.manage_assignments'))
        return redirect(url_for('main.student_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = db.session.scalar(db.select(User).where(User.email == email))
        if user and user.password_hash and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('Logged in successfully.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        flash('Invalid email, password, or account configuration.', 'danger')
    return render_template('login.html', title='Login')

from dotenv import load_dotenv
import os
# Load environment variables from .env
load_dotenv()
SECRET_REGISTRATION_KEY = os.getenv('SECRET_REGISTRATION_KEY')
SECRET_REGISTRATION_KEY = '19830308'
@main.route('/register', methods=['GET', 'POST'])
def register():
    # 1. Handle authenticated users
    if current_user.is_authenticated:
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

@main.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    # ----------------------------------------------------------------------
    # WeChat Binding Status Check
    # ----------------------------------------------------------------------
    wechat_user = WeChatUser.query.filter_by(student_id=current_user.student_id).first()
    wechat_is_bound = wechat_user is not None
    wechat_openid = wechat_user.openid if wechat_user else None

    status_msg = "BOUND" if wechat_is_bound else "NOT BOUND"
    openid_log = f" (OpenID: {wechat_openid})" if wechat_openid else ""
    logger.info(
        f"WeChat Status Check for Profile: User ID {current_user.id}, "
        f"Student ID {current_user.student_id} is {status_msg}{openid_log}"
    )
    # ----------------------------------------------------------------------

    # Get all classes and current enrollments for rendering
    classes = Class.query.all()
    
    result = db.session.execute(
        text("SELECT class_id FROM enrolled_classes WHERE user_id = :user_id"),
        {"user_id": current_user.id}
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
            elif User.query.filter(User.username == username, User.id != current_user.id).first():
                flash('Username is already in use.', 'error')
            # 3. Check if email is taken by another user
            elif User.query.filter(User.email == email, User.id != current_user.id).first():
                flash('Email is already in use.', 'error')
            
            else:
                # 4. Update core details
                current_user.username = username
                current_user.email = email
                
                # 5. Clear and add class enrollments
                db.session.execute(
                    text("DELETE FROM enrolled_classes WHERE user_id = :user_id"),
                    {"user_id": current_user.id}
                )
                for class_id in class_ids:
                    db.session.execute(
                        text("INSERT INTO enrolled_classes (user_id, class_id) VALUES (:user_id, :class_id)"),
                        {"user_id": current_user.id, "class_id": int(class_id)}
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
            elif not check_password_hash(current_user.password_hash, current_password):
                flash('The current password you entered is incorrect.', 'error')
            
            # 3. Hash and update new password
            else:
                try:
                    new_hash = generate_password_hash(new_password)
                    current_user.password_hash = new_hash
                    
                    db.session.commit()
                    flash('Password changed successfully!', 'success')
                    return redirect(url_for('main.profile'))
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error changing password for user {current_user.id}: {e}")
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
    qq_user = QQUser.query.filter_by(student_id=current_user.student_id).first()
    qq_is_bound = qq_user is not None
    qq_openid = qq_user.openid if qq_user else None

    status_msg = "BOUND" if qq_is_bound else "NOT BOUND"
    openid_log = f" (OpenID: {qq_openid})" if qq_openid else ""
    logger.info(
        f"QQ Status Check for Profile: User ID {current_user.id}, "
        f"Student ID {current_user.student_id} is {status_msg}{openid_log}"
    )
    
    # Get all classes
    classes = Class.query.all()
    
    # Get user's current enrolled classes
    result = db.session.execute(
        text("SELECT class_id FROM enrolled_classes WHERE user_id = :user_id"),
        {"user_id": current_user.id}
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
            User.id != current_user.id
        ).first()
        if existing_username:
            flash('Username is already in use.', 'error')
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Username is already in use.')
        
        # Check if email is taken by another user
        existing_email = User.query.filter(
            User.email == email, 
            User.id != current_user.id
        ).first()
        if existing_email:
            flash('Email is already in use.', 'error')
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Email is already in use.')
        
        # Update username and email
        current_user.username = username
        current_user.email = email
        
        # Clear existing class enrollments
        db.session.execute(
            text("DELETE FROM enrolled_classes WHERE user_id = :user_id"),
            {"user_id": current_user.id}
        )
        
        # Add new class enrollments
        for class_id in class_ids:
            db.session.execute(
                text("INSERT INTO enrolled_classes (user_id, class_id) VALUES (:user_id, :class_id)"),
                {"user_id": current_user.id, "class_id": int(class_id)}
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
    wechat_user = WeChatUser.query.filter_by(student_id=current_user.student_id).first()
    # Pass a simple boolean or the openid itself to the template
    wechat_is_bound = wechat_user is not None
    wechat_openid = wechat_user.openid if wechat_user else None

        # Log the binding status for debugging and monitoring
    status_msg = "BOUND" if wechat_is_bound else "NOT BOUND"
    openid_log = f" (OpenID: {wechat_openid})" if wechat_openid else ""
    logger.info(
        f"WeChat Status Check for Profile: User ID {current_user.id}, "
        f"Student ID {current_user.student_id} is {status_msg}{openid_log}"
    )
    
    # Dynamically attach an attribute to current_user for template compatibility.
    # This ensures the template logic `{% if not current_user.wechat_openid %}` works.
    #current_user.wechat_openid = wechat_user.openid if wechat_user else None
    # ----------------------------------------------------------------------

    # Get all classes
    classes = Class.query.all()
    
    # Get user's current enrolled classes using EXISTING enrolled_classes table
    result = db.session.execute(
        text("SELECT class_id FROM enrolled_classes WHERE user_id = :user_id"),
        {"user_id": current_user.id}
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
            User.id != current_user.id
        ).first()
        if existing_username:
            flash('Username is already in use.', 'error')
            # FIX: Use current_class_ids for template variable name
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Username is already in use.')
        
        # Check if email is taken by another user
        existing_email = User.query.filter(
            User.email == email, 
            User.id != current_user.id
        ).first()
        if existing_email:
            flash('Email is already in use.', 'error')
            # FIX: Use current_class_ids for template variable name
            return render_template('profile.html', classes=classes, current_class_ids=enrolled_class_ids, error='Email is already in use.')
        
        # Update username and email
        current_user.username = username
        current_user.email = email
        
        # Clear existing class enrollments using EXISTING enrolled_classes table
        db.session.execute(
            text("DELETE FROM enrolled_classes WHERE user_id = :user_id"),
            {"user_id": current_user.id}
        )
        
        # Add new class enrollments
        for class_id in class_ids:
            db.session.execute(
                text("INSERT INTO enrolled_classes (user_id, class_id) VALUES (:user_id, :class_id)"),
                {"user_id": current_user.id, "class_id": int(class_id)}
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
        {"user_id": current_user.id}
    )
    user_classes = [{'id': row[0], 'name': row[1]} for row in result.fetchall()]
    return jsonify(user_classes)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('main.index'))
'''
@main.route('/dashboard')
@login_required
def student_dashboard1():
    if is_teacher():
        title = 'Teacher Dashboard'
        user_query = select(User).where(User.id == current_user.id).options(
            selectinload(User.taught_classes).selectinload(Class.assignments)
        )
        user_with_data = db.session.scalars(user_query).first()
        classes = user_with_data.taught_classes if user_with_data else []
    else:
        title = 'Student Dashboard'
        user_query = select(User).where(User.id == current_user.id).options(
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
        user_query = select(User).where(User.id == current_user.id).options(
            selectinload(User.taught_classes) # Removed .selectinload(Class.assignments)
        )
        user_with_data = db.session.scalars(user_query).first()
        #user_with_data = user_query.first()
        classes = user_with_data.taught_classes if user_with_data else []
    else:
        title = 'Student Dashboard'
        user_query = User.query.options(
            joinedload(User.enrolled_classes)  # ← EAGER LOAD
        ).filter_by(id=current_user.id)
    
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
    if current_user.role != 'teacher':
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
            .where(Class.teacher_id == current_user.id)
            # FIX 1: Eagerly load the 'students' relationship (used for length calculation)
            .options(selectinload(Class.students))
            # FIX 2: Eagerly load the 'assignments' relationship (used for length calculation)
            .options(selectinload(Class.assignments)) 
            .order_by(Class.name)
        )
        
        classes = db.session.execute(classes_stmt).scalars().all()
        logger.info(
            f"Check for Profile: User ID {current_user.id}, "
            f"classes is {classes}"
        )
    except Exception as e:
        logger.error(f"Error fetching classes for teacher {current_user.id}: {e}", exc_info=True)
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
    if current_user.role != 'teacher':
        flash('Access denied.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
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
    if current_user.role != 'teacher':
        return redirect(url_for('main.student_dashboard'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
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
    sub = PushSubscription.query.filter_by(endpoint=data['endpoint'], user_id=current_user.id).first()
    if not sub:
        sub = PushSubscription(
            user_id=current_user.id,
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
    if current_user.role != 'teacher':
        flash('Access denied.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    class_ = Class.query.get_or_404(class_id)
    if class_.teacher_id != current_user.id:
        flash('You can only edit your own class.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    if request.method == 'GET':
        return render_template('memo_edit.html', class_=class_)

    # POST: Append new memo
    new_text = request.form.get("new_memo", "").strip()
    if new_text:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"[{timestamp}] {new_text}"
        class_.memo = (class_.memo or "") + ("\n" if class_.memo else "") + entry
        db.session.commit()
        flash("新备忘已添加！", "success")
    else:
        flash("内容不能为空", "danger")

    return redirect(url_for('main.student_dashboard'))

from app.models import Announcement, Message
@main.route('/announcement')
@login_required
def announcement_index():
    # === ANNOUNCEMENTS ===
    global_announcements = Announcement.query.filter_by(class_id=None).order_by(Announcement.created_at.desc()).all()

    class_announcements = []
    if current_user.role == 'student':
        enrolled_classes = current_user.enrolled_classes
        for cls in enrolled_classes:
            anns = Announcement.query.filter_by(class_id=cls.id).order_by(Announcement.created_at.desc()).all()
            class_announcements.append({'class': cls, 'announcements': anns})
    # Teachers see all
    elif current_user.role == 'teacher':
        classes = Class.query.filter_by(teacher_id=current_user.id).all()
        for cls in classes:
            anns = Announcement.query.filter_by(class_id=cls.id).order_by(Announcement.created_at.desc()).all()
            class_announcements.append({'class': cls, 'announcements': anns})

    # === PRIVATE MESSAGES ===
    messages = []
    if current_user.role == 'teacher':
        # Teacher sees all messages in their classes
        messages = Message.query.join(Class).filter(
            Class.teacher_id == current_user.id
        ).order_by(Message.created_at.desc()).all()
    elif current_user.role == 'student':
        # Student sees only their own messages
        messages = Message.query.filter_by(sender_id=current_user.id).order_by(Message.created_at.desc()).all()

    return render_template(
        'announcements/index.html',
        global_announcements=global_announcements,
        class_announcements=class_announcements,
        messages=messages
    )

@main.route('/announcement/create', methods=['GET', 'POST'])
@login_required
def announcement_create():
    if current_user.role != 'teacher':
        flash('Only teachers can create announcements.', 'danger')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        title = request.form.get('title').strip()
        content = request.form.get('content').strip()
        class_id_str = request.form.get('class_id')  # Keep as string first

        if not title or not content:
            flash('Title and content are required.', 'danger')
            return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=current_user.id).all())

        # Convert class_id safely
        class_id = None
        if class_id_str:
            try:
                class_id = int(class_id_str)
                # Optional: verify teacher owns class
                if not Class.query.filter_by(id=class_id, teacher_id=current_user.id).first():
                    flash('Invalid class selected.', 'danger')
                    return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=current_user.id).all())
            except ValueError:
                flash('Invalid class ID.', 'danger')
                return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=current_user.id).all())

        try:
            announcement = Announcement(
                title=title.strip(),
                content=content.strip(),
                author_id=current_user.id,
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
            return render_template('announcements/create.html', classes=Class.query.filter_by(teacher_id=current_user.id).all())

    # GET
    classes = Class.query.filter_by(teacher_id=current_user.id).all()
    return render_template('announcements/create.html', classes=classes)
@main.route('/announcement/message', methods=['POST'])
@login_required
def send_message():
    if current_user.role != 'student':
        return jsonify(success=False, message="Only students can send messages"), 403

    class_id = request.form.get('class_id', type=int)
    content = request.form.get('content', '').strip()

    if not class_id or not content:
        return jsonify(success=False, message="Class and message required"), 400

    # Verify enrollment
    enrolled = db.session.execute(
        text("SELECT 1 FROM enrolled_classes WHERE user_id = :uid AND class_id = :cid"),
        {"uid": current_user.id, "cid": class_id}
    ).scalar()

    if not enrolled:
        return jsonify(success=False, message="Not enrolled in this class"), 403

    try:
        msg = Message(
            content=content,
            sender_id=current_user.id,
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
    announcement = Announcement.query.get_or_404(id)
    if announcement.author_id != current_user.id:
        flash('You can only delete your own announcements.', 'danger')
        return redirect(url_for('main.announcement_index'))

    db.session.delete(announcement)
    db.session.commit()
    flash('Announcement deleted!', 'success')
    return redirect(url_for('main.announcement_index'))


from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length

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
            teacher_id=current_user.id
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
    if current_user.role != 'teacher':
        flash('Access denied.', 'danger')
        return redirect(url_for('main.manage_classes'))

    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
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