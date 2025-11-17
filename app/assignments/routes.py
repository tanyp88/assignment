from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, abort, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app import db, logger  # ← ADD THIS
from app.models import Assignment, Submission, User, Class, enrolled_classes
import logging
import logging
import os, logging, re, magic, tempfile
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.utils.quota_lock import is_quota_exhausted, get_quota_reset_time




#import logging
#logger = logging.getLogger('assignment_app')
'''
if not logger.handlers:                     # prevent duplicate handlers on reload
    handler = logging.StreamHandler()      # prints to stdout → docker logs
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s %(name)s – %(message)s',
        datefmt='%H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)          # change to INFO in production
'''
# Define the Blueprint for assignment-related routes
assignments = Blueprint('assignments', __name__, url_prefix='/zuoye')

# --- Helper Functions ---

def is_teacher():
    return current_user.is_authenticated and current_user.role == 'teacher'

def is_student():
    return current_user.is_authenticated and current_user.role == 'student'

UPLOADS_DIR='/app/uploads'
# Create a custom route that handles the full path after the endpoint
@assignments.route('/files/<path:filename>')
def serve_user_file(filename):
    # This function safely serves the file from UPLOADS_DIR
    return send_from_directory(UPLOADS_DIR, filename)

ALLOWED_EXT = {
    '.txt', '.md', '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.docx',
    '.py', '.c', '.cpp', '.ipynb'
}

MIME_MAP = {
    'text/plain': 'txt',
    'text/markdown': 'md',
    'application/pdf': 'pdf',
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    
    # 新增：代码文件
    'text/x-python': 'py',
    'text/x-c': 'c',
    'text/x-c++': 'cpp',
    'application/x-ipynb+json': 'ipynb'
}

import subprocess
import shlex

def convert_docx_to_pdf(docx_path):
    """Convert .docx to .pdf using LibreOffice headless."""
    pdf_path = docx_path.replace('.docx', '.pdf')
    cmd = [
        'libreoffice',
        '--headless',
        '--convert-to', 'pdf',
        '--outdir', os.path.dirname(docx_path),
        docx_path
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if os.path.exists(pdf_path):
            return pdf_path
        else:
            current_app.logger.error(f"PDF not created: {result.stdout}")
            return docx_path
    except subprocess.CalledProcessError as e:
        current_app.logger.error(f"LibreOffice failed: {e.stderr}")
        return docx_path
    except Exception as e:
        current_app.logger.error(f"Conversion error: {e}")
        return docx_path
    
def validate_and_process_file(file, assignment_id):
    """
    验证 + 保存 + 转换文件
    支持: .py .c .cpp .ipynb .docx(.pdf) .md .pdf .txt .png .jpg .webp
    """
    if not file or not file.filename:
        return None, "No file selected."

    # === 1. 安全文件名 + 扩展名 ===
    orig_filename = file.filename
    filename = secure_filename(orig_filename)
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXT:
        return None, f"File type not allowed: {ext}"

    # === 2. 读取前 1024 字节嗅探真实 MIME ===
    try:
        header = file.read(1024)
        detected_mime = magic.from_buffer(header, mime=True)
        file.seek(0)  # 重置指针
    except Exception as e:
        return None, f"MIME detection failed: {str(e)}"

    # === 3. MIME 兼容性修复 ===
    if ext == '.docx' and detected_mime in ['application/zip', 'application/octet-stream']:
        detected_mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    if ext == '.ipynb' and detected_mime in ['application/json', 'application/octet-stream']:
        detected_mime = 'application/x-ipynb+json'

    # === 4. MIME ↔ 扩展名 双向校验 ===
    allowed_exts = MIME_MAP.get(detected_mime, [])
    if isinstance(allowed_exts, str):
        allowed_exts = [allowed_exts]
    if ext[1:] not in allowed_exts:
        return None, f"File content mismatch: detected {detected_mime}, expected {ext}"

    # === 5. 安全路径生成 ===
    upload_dir = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_dir, exist_ok=True)

    timestamp = int(datetime.utcnow().timestamp())
    safe_name = f"{current_user.id}_{assignment_id}_{timestamp}_{filename}"
    final_path = os.path.join(upload_dir, safe_name)

    # === 6. 保存原始文件 ===
    file.save(final_path)

    # === 7. 特殊处理：.docx → .pdf ===
    if ext == '.docx':
        pdf_path = final_path.replace('.docx', '.pdf')
        try:
            from docx2pdf import convert  # pip install docx2pdf
            convert(final_path, pdf_path)
            # 可选：删除原 .docx
            # os.remove(final_path)
            return pdf_path, None
        except Exception as e:
            flash(f"DOCX→PDF failed, using original: {str(e)}", "warning")
            return final_path, None

    # === 8. 特殊处理：.ipynb → 提取代码（可选）===
    if ext == '.ipynb':
        try:
            with open(final_path, 'r', encoding='utf-8') as f:
                nb = json.load(f)
            code_cells = [cell['source'] for cell in nb.get('cells', []) if cell['cell_type'] == 'code']
            code_str = '\n\n'.join([''.join(cell) for cell in code_cells])
            code_path = final_path.replace('.ipynb', '_extracted.py')
            with open(code_path, 'w', encoding='utf-8') as f:
                f.write(f"# Extracted from {orig_filename}\n\n{code_str}")
            return final_path, code_path  # 返回 ipynb + 提取的 .py
        except Exception as e:
            flash(f"Failed to extract code from .ipynb: {str(e)}", "warning")

    # === 9. 其他文件直接返回 ===
    return final_path, None

# --- Teacher Routes ---

@assignments.route('/manage')
@login_required
def manage_assignments():
    if not is_teacher():
        flash('Only teachers can manage assignments.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    teacher_assignments = db.session.scalars(
        select(Assignment)
        .join(Class)
        .where(Class.teacher_id == current_user.id)
        .options(selectinload(Assignment.class_))
    ).all()
    return render_template('assignments/manage.html', title='Manage Assignments', assignments=teacher_assignments)

@assignments.route('/create/<int:class_id>', methods=['GET', 'POST'])
@login_required
def create_assignment(class_id):
    if not is_teacher():
        flash('Only teachers can create assignments.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    class_obj = db.session.get(Class, class_id)
    if not class_obj or class_obj.teacher_id != current_user.id:
        flash('Class not found or you are not authorized.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        due_date_str = request.form.get('due_date')
        weight = request.form.get('weight', '1.0')

        if not title or not due_date_str:
            flash('Title and due date are required.', 'danger')
            return render_template('assignments/create_assignment.html', class_obj=class_obj, title='Create Assignment')

        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%dT%H:%M')
            weight = float(weight)
            if weight <= 0:
                raise ValueError("Weight must be positive")
        except ValueError as e:
            flash(f'Invalid input: {e}', 'danger')
            return render_template('assignments/create_assignment.html', class_obj=class_obj, title='Create Assignment')

        assignment = Assignment(
            title=title,
            description=description,
            class_id=class_id,
            due_date=due_date,
            weight=weight
        )
        db.session.add(assignment)
        db.session.commit()
        flash('Assignment created successfully.', 'success')
        return redirect(url_for('assignments.class_detail', class_id=class_id))

    return render_template('assignments/create_assignment.html', class_obj=class_obj, title='Create Assignment')


@assignments.route('/edit/<int:assignment_id>', methods=['GET', 'POST'])
@login_required
def edit_assignment(assignment_id):
    if not is_teacher():
        flash('Only teachers can edit assignments.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    assignment = db.session.get(Assignment, assignment_id)
    if not assignment or assignment.class_.teacher_id != current_user.id:
        flash('Assignment not found or you are not authorized.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        due_date_str = request.form.get('due_date')
        weight = request.form.get('weight', '1.0')

        if not title or not due_date_str:
            flash('Title and due date are required.', 'danger')
            return render_template('assignments/edit_assignment.html', assignment=assignment, title='Edit Assignment')

        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%dT%H:%M')
            weight = float(weight)
            if weight <= 0:
                raise ValueError("Weight must be positive")
        except ValueError as e:
            flash(f'Invalid input: {e}', 'danger')
            return render_template('assignments/edit_assignment.html', assignment=assignment, title='Edit Assignment')

        assignment.title = title
        assignment.description = description
        assignment.due_date = due_date
        assignment.weight = weight
        db.session.commit()
        flash('Assignment updated successfully.', 'success')
        return redirect(url_for('assignments.manage_assignments'))

    return render_template('assignments/edit_assignment.html', assignment=assignment, title='Edit Assignment')


@assignments.route('/<int:assignment_id>/submit', methods=['GET', 'POST'])
@login_required
def submit_assignment1(assignment_id):
    if not is_student():
        flash('Only students can submit assignments.', 'danger')
        return redirect(url_for('main.index'))
    
    assignment = db.session.get(Assignment, assignment_id)
    if not assignment:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    # Check if student is enrolled in the class
    if assignment.class_ not in current_user.enrolled_classes:
        flash('You are not enrolled in this class.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    # Check for existing submission
    existing_submission = db.session.scalar(
        db.select(Submission)
        .where(Submission.assignment_id == assignment_id)
        .where(Submission.student_id == current_user.id)
    )
    if existing_submission and existing_submission.grade is not None:
        flash('You have already submitted this assignment.', 'info')
        return redirect(url_for('assignments.view_submission', 
                              assignment_id=assignment_id, 
                              student_id=current_user.id))
    
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        file = request.files.get('file')
        
        if not content and not file:
            flash('Submission content or file is required.', 'danger')
            return redirect(url_for('assignments.submit_assignment1', assignment_id=assignment_id))
        
        # Handle file upload
        file_path = None 
        error_msg = None
        if file:
            file_path, error_msg = validate_and_process_file(file,assignment_id)
            if error_msg:
                flash(error_msg, 'danger')
                return redirect(url_for('assignments.submit_assignment1', assignment_id=assignment_id))

        if existing_submission:
            # === UPDATE EXISTING SUBMISSION ===
            submission = existing_submission
            submission.content = content or ''
            submission.file_path = file_path
            # Optional: update a timestamp like submission.timestamp = datetime.now()
            action = "updated"
        else:
            # === CREATE NEW SUBMISSION ===
            submission = Submission(
                assignment_id=assignment_id,
                student_id=current_user.id,
                content=content or '',
                file_path=file_path
            )
            db.session.add(submission)
            action = "successful"
            
        db.session.commit()
        
        flash(f'Submission {action}! Grading will be processed shortly.', 'success')
        
        # ... (Celery task dispatch remains the same) ...
        submission_id = submission.id
        logger.info(f"Queuing grade_submission for {submission_id}")
        from app.tasks import grade_submission #,grade_submission_nodelay  # Import the Celery task
        #grade_submission_nodelay(submission_id)
        grade_submission.delay(submission_id)
        #
        
        return redirect(url_for('assignments.view_submission', 
                                assignment_id=assignment_id, 
                                student_id=current_user.id))
    
    # --- GET Handling ---
    # If the student has an ungraded submission, pass it to the template for editing
    initial_content = existing_submission.content if existing_submission else ''
    
    return render_template('assignments/submit1.html', 
                           title=f'Submit: {assignment.title}', 
                           assignment=assignment,
                           initial_content=initial_content) # Pass existing content


@assignments.route('/submissions', methods=['GET'])
@login_required
def view_submissions_history():
    if not is_student():
        flash('Access denied. This page is for students.', 'danger')
        return redirect(url_for('main.index'))
    
    submissions = db.session.scalars(
        db.select(Submission)
        .where(Submission.student_id == current_user.id)
        .order_by(Submission.submitted_at.desc())
    ).all()
    
    return render_template('assignments/submissions.html', title='My Submissions', submissions=submissions)

@assignments.route('/submission/<int:assignment_id>/<int:student_id>', methods=['GET'])
@login_required
def view_submission(assignment_id, student_id):
    submission = db.session.execute(
        select(Submission).where(
            Submission.assignment_id == assignment_id,
            Submission.student_id == student_id
        ).options(selectinload(Submission.student), selectinload(Submission.assignment))
    ).scalar_one_or_none()
    if not submission:
        flash('Submission not found.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    if is_student() and submission.student_id != current_user.id:
        flash('You are not authorized to view this submission.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    if is_teacher() and not db.session.execute(
        select(Class).join(Assignment).where(
            Assignment.id == assignment_id,
            Class.teacher_id == current_user.id
        )
    ).scalar_one_or_none():
        flash('You are not authorized to view this submission.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    return render_template('assignments/view_submission.html', 
                           title='View Submission', 
                           submission=submission, 
                           is_teacher=is_teacher)


@assignments.route('/submissions/<int:assignment_id>', methods=['GET'])
@login_required
def view_submissions(assignment_id):
    if not is_teacher():
        flash('Only teachers can view all submissions.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    # Fetch the assignment and verify teacher's authorization
    assignment = db.session.get(Assignment, assignment_id)
    if not assignment:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    if not db.session.execute(
        select(Class).where(
            Class.id == assignment.class_id,
            Class.teacher_id == current_user.id
        )
    ).scalar_one_or_none():
        flash('You are not authorized to view submissions for this assignment.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    # Fetch all submissions for the assignment with related student data
    submissions = db.session.execute(
        select(Submission).where(Submission.assignment_id == assignment_id)
        .options(selectinload(Submission.student))
    ).scalars().all()
    
    return render_template('assignments/view_submissions.html', 
                           title=f'Submissions for {assignment.title}', 
                           assignment=assignment, 
                           submissions=submissions, 
                           is_teacher=is_teacher)


@assignments.route('/submission/<int:assignment_id>/grade', methods=['POST'])
@login_required
def grade_submission_route(assignment_id):
    logger.info(f"grade_submission_route called by user={getattr(current_user, 'id', None)} assignment={assignment_id}")
    if not is_teacher():
        flash('Only teachers can grade submissions.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    # -------------------------------------------------
    # 1. Get form data
    # -------------------------------------------------
    student_id   = request.form.get('student_id', type=int)
    action       = request.form.get('action')          # "ungrade" or None
    grade_input  = request.form.get('grade')           # may be empty string
    feedback     = request.form.get('feedback', '')

    logger.debug(
        "grade_submission_route called – "
        f"teacher={current_user.id} assignment={assignment_id} "
        f"student={student_id} action={action!r} grade_input={grade_input!r}"
    )

    # -------------------------------------------------
    # 2. Validate student_id
    # -------------------------------------------------
    if not student_id:
        flash('Student ID is required.', 'danger')
        return redirect(url_for('assignments.view_submissions', assignment_id=assignment_id))

    # -------------------------------------------------
    # 3. Determine final grade (None = ungraded)
    # -------------------------------------------------
    final_grade = None

    if grade_input and grade_input.strip():
        try:
            g = int(grade_input)
            if 0 <= g <= 100:
                final_grade = g
            else:
                flash('Grade must be between 0 and 100.', 'danger')
                return redirect(url_for('assignments.view_submissions', assignment_id=assignment_id))
        except ValueError:
            flash('Grade must be a number.', 'danger')
            return redirect(url_for('assignments.view_submissions', assignment_id=assignment_id))

    # “Ungrade” button forces None even if a number is still in the field
    if action == 'ungrade':
        final_grade = None
        logger.debug(f"Ungrade button pressed → final_grade forced to None")

    # -------------------------------------------------
    # 4. Load submission
    # -------------------------------------------------
    submission = db.session.execute(
        select(Submission)
        .where(Submission.assignment_id == assignment_id,
               Submission.student_id   == student_id)
    ).scalar_one_or_none()

    if not submission:
        flash('Submission not found.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    # -------------------------------------------------
    # 5. Save
    # -------------------------------------------------
    submission.grade    = final_grade
    submission.feedback = feedback

    try:
        db.session.commit()
        if final_grade is None:
            flash(f'Submission for student {student_id} has been UNGRADED.', 'info')
            logger.info(f"Teacher {current_user.id} UNGRADED submission {submission.id}")
        else:
            flash(f'Submission graded: {final_grade}/100', 'success')
            logger.info(f"Teacher {current_user.id} graded submission {submission.id} → {final_grade}")
    except Exception as e:
        db.session.rollback()
        flash('Database error. Grade not saved.', 'danger')
        logger.error(f"Failed to save grade for submission {submission.id}: {e}")

    return redirect(url_for('assignments.view_submissions', assignment_id=assignment_id))

@assignments.route('/submission/<int:assignment_id>/<int:student_id>/grade', methods=['GET', 'POST'])
@login_required
def grade_assignment(assignment_id, student_id):
    if not is_teacher():
        flash('Access denied. Only teachers can grade assignments.', 'danger')
        return redirect(url_for('main.index'))
    
    submission = db.session.scalar(
        db.select(Submission)
        .where(Submission.assignment_id == assignment_id)
        .where(Submission.student_id == student_id)
    )
    
    if not submission:
        flash('Submission not found.', 'danger')
        return redirect(url_for('assignments.manage_assignments'))
    
    # Ensure teacher is enrolled in the class
    if submission.assignment.class_ not in current_user.enrolled_classes:
        flash('You are not authorized to grade this submission.', 'danger')
        return redirect(url_for('assignments.manage_assignments'))
    
    if request.method == 'POST':
        grade = request.form.get('grade')
        feedback = request.form.get('feedback')
        
        try:
            grade = int(grade)
            if not (0 <= grade <= 100):
                raise ValueError('Grade must be between 0 and 100.')
        except (ValueError, TypeError):
            flash('Invalid grade format.', 'danger')
            return redirect(url_for('assignments.grade_assignment', 
                                  assignment_id=assignment_id, 
                                  student_id=student_id))
        
        submission.grade = grade
        submission.feedback = feedback
        db.session.commit()
        flash('Grade updated successfully.', 'success')
        return redirect(url_for('assignments.view_submission', 
                              assignment_id=assignment_id, 
                              student_id=student_id))
    
    return render_template('assignments/grade.html', 
                         title='Grade Submission', 
                         submission=submission)

@assignments.route('/class/<int:class_id>')
@login_required
def class_detail(class_id):
    class_obj = db.session.get(Class, class_id)
    if not class_obj:
        flash('Class not found.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    if is_teacher() and class_obj.teacher_id != current_user.id:
        flash('You are not authorized to view this class.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    else:
        print(f"Current user ID: {current_user.id}, Role: {current_user.role}")  # Debug
        submissions = {}
        if current_user.is_authenticated and current_user.role == 'student':
            submissions = {
                s.assignment_id: s for s in Submission.query
                .filter_by(student_id=current_user.id)
                .join(Assignment)
                .filter(Assignment.class_id == class_id)
                .all()
            }
            print(f"Submissions found: {submissions}")  # Debug
    return render_template('assignments/class_detail.html', class_obj=class_obj,  submissions=submissions,title=f'Class: {class_obj.name}')


@assignments.route('/autograde/<int:assignment_id>', methods=['GET', 'POST'])
@login_required
def autograde_assignment(assignment_id):
    """Autogrades all ungraded submissions for a given assignment."""
    logger.info(f"autograde_assignment called by user={getattr(current_user, 'id', None)} assignment={assignment_id}")

    if not is_teacher():
        flash('Only teachers can autograde assignments.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    # 1. Validation & Authorization
    assignment = db.session.get(Assignment, assignment_id)
    if not assignment:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    
    # Check if the current user is the teacher for the class
    if not db.session.execute(
        select(Class).where(
            Class.id == assignment.class_id,
            Class.teacher_id == current_user.id
        )
    ).scalar_one_or_none():
        flash('You are not authorized to autograde this assignment.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    # 2. Fetch Ungraded Submissions
    ungraded_submissions = db.session.execute(
        select(Submission)
        .where(
            Submission.assignment_id == assignment_id,
            Submission.grade.is_(None)  # Select only submissions where grade is NULL/None
        )
        .options(selectinload(Submission.student))
    ).scalars().all()
    
    if not ungraded_submissions:
        flash('All submissions are already graded or no submissions available.', 'warning')
        return redirect(url_for('assignments.view_submissions', assignment_id=assignment_id))
    
    graded_count = 0
    
    # 3. Autograding Logic (Iterate and update)
    for submission in ungraded_submissions:
        try:
            '''
            # Placeholder AI autograding logic (as requested: simple content length)
            content_length = len(submission.content or '')
            
            # Simple grading rule: 10 chars = 1 point, capped at 100
            grade = min(100, max(0, int(content_length / 10))) 
            
            # Update submission object
            submission.grade = grade
            submission.feedback = f"Auto-graded based on content length ({content_length} characters). Grade: {grade}/100."
            
            graded_count += 1
            logger.debug(f"Autograded submission {submission.id} (student {submission.student_id}) to {grade}/100.")
            '''
            from app.tasks import grade_submission
            grade_submission.delay(submission.id)
            graded_count += 1
        except Exception as e:
            # Catch individual processing errors but continue with the batch
            logger.error(f"Error autograding submission {submission.id}: {e}")
            # Do not rollback the session yet, only commit the successful ones
            
    # 4. Commit all changes in one transaction
    '''
    try:
        db.session.commit()
        flash(f'Successfully autograded {graded_count} submissions.', 'success')
        logger.info(f"Teacher {current_user.id} successfully autograded {graded_count} submissions for assignment {assignment_id}.")
    except Exception as e:
        db.session.rollback()
        flash('A database error occurred during batch grading. No grades were saved.', 'danger')
        logger.error(f"Batch autograde failed for assignment {assignment_id}: {e}")
    '''
    return redirect(url_for('assignments.view_submissions', assignment_id=assignment_id))

@assignments.route('/grade_all_ungraded')
@login_required
def grade_all_ungraded():
    if current_user.role != 'teacher':
        flash('Only teachers can grade.', 'danger')
        return redirect(url_for('main.manage_classes'))

    # FIXED: Check submissions.grade IS NULL
    ungraded = db.session.execute(
        db.text("""
            SELECT s.id
            FROM submissions s
            JOIN assignments a ON s.assignment_id = a.id
            JOIN classes c ON a.class_id = c.id
            WHERE c.teacher_id = :teacher_id
              AND s.grade IS NULL
        """),
        {"teacher_id": current_user.id}
    ).fetchall()

    if not ungraded:
        flash('No ungraded submissions.', 'info')
        return redirect(url_for('main.manage_classes'))

    queued = 0
    for (sub_id,) in ungraded:
        try:
            from app.tasks import grade_submission
            grade_submission.delay(sub_id)
            queued += 1
        except Exception as e:
            current_app.logger.error(f"Task failed for {sub_id}: {e}")

    flash(f'Queued {queued} submissions for autograding.', 'success')
    return redirect(url_for('main.manage_classes'))

@assignments.route('/assignment/<int:assignment_id>')
@login_required
def assignment_detail(assignment_id):
    if not is_teacher():
        flash('Only teachers can view assignment submissions.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    assignment = db.session.get(Assignment, assignment_id)
    if not assignment or assignment.class_.teacher_id != current_user.id:
        flash('Assignment not found or you are not authorized.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    submissions = assignment.submissions
    return render_template('assignments/assignment_detail.html', assignment=assignment, submissions=submissions, title=f'Assignment: {assignment.title}')

@assignments.route('/edit_class/<int:class_id>', methods=['GET', 'POST'])
@login_required
def edit_class(class_id):
    """
    Handles the editing functionality for a specific class.
    Only accessible by users with the 'teacher' role.
    """
    
    # 1. Role Check: Only teachers are authorized to edit classes
    if current_user.role != 'teacher':
        flash('You are not authorized to manage classes.', 'danger')
        # Abort with a 403 Forbidden status
        abort(403) 

    # 2. Fetch the Class (using a helper function like get_or_404 if available, or direct query)
    # NOTE: Replace 'Class.query.get(class_id)' with your actual DB query logic.
    class_ = Class.query.get(class_id)
    if class_ is None:
        flash(f'Class with ID {class_id} not found.', 'danger')
        return redirect(url_for('assignments.manage_classes'))


    if request.method == 'POST':
        # --- Handle Form Submission (POST) ---
        new_name = request.form.get('name', '').strip()
        
        if not new_name:
            flash('The class name cannot be empty.', 'warning')
            return redirect(url_for('assignments.edit_class', class_id=class_id))
        
        # 3. Update the database
        try:
            class_.name = new_name
            db.session.commit()
            flash(f'Class "{new_name}" updated successfully!', 'success')
            
            # Redirect back to the class management view
            return redirect(url_for('assignments.manage_classes'))
        
        except Exception as e:
            # Handle potential database errors
            db.session.rollback()
            print(f"Database error during class update: {e}") 
            flash('An unexpected error occurred while saving changes.', 'danger')
            # Stay on the edit page so the user can try again
            return redirect(url_for('assignments.edit_class', class_id=class_id))
            

    # --- Display Form (GET) ---
    # The 'edit_class.html' template needs the 'class_' object passed to it.
    return render_template(
        'assignments/edit_class.html', 
        title=f'Edit {class_.name}', 
        class_=class_
    )

@assignments.route('/submit_assignment', methods=['GET', 'POST'])
@login_required
def submit_assignment():
    if not is_student():
        flash('Only students can submit assignments.', 'danger')
        return redirect(url_for('main.student_dashboard'))
    if request.method == 'POST':
        class_id = request.form.get('class_id', type=int)
        assignment_id = request.form.get('assignment_id', type=int)
        content = request.form.get('content')
        if not (class_id and assignment_id and content):
            flash('Class, assignment, and content are required.', 'danger')
            return redirect(url_for('assignments.submit_assignment'))
        assignment = db.session.get(Assignment, assignment_id)
        if not assignment or assignment.class_id != class_id:
            flash('Invalid assignment or class.', 'danger')
            return redirect(url_for('assignments.submit_assignment'))
        enrolled = db.session.execute(
            select(Class).join(enrolled_classes).where(
                enrolled_classes.c.user_id == current_user.id,
                enrolled_classes.c.class_id == class_id
            )
        ).scalar_one_or_none()
        if not enrolled:
            flash('You are not enrolled in this class.', 'danger')
            return redirect(url_for('assignments.submit_assignment'))
        existing_submission = db.session.execute(
            select(Submission).where(
                Submission.assignment_id == assignment_id,
                Submission.student_id == current_user.id
            )
        ).scalar_one_or_none()



        if existing_submission:
            flash('You have already submitted this assignment.', 'danger')
            return redirect(url_for('assignments.class_detail', class_id=class_id))
        submission = Submission(
            assignment_id=assignment_id,
            student_id=current_user.id,
            content=content,
            submitted_at=datetime.now()
        )
        db.session.add(submission)
        db.session.commit()
        flash('Submission successful.', 'success')
        return redirect(url_for('assignments.class_detail', class_id=class_id))
    user = db.session.get(User, current_user.id)
    classes = user.enrolled_classes
    return render_template('assignments/submit.html',  classes=classes, title='Submit Assignment')

@assignments.route('/api/assignments/<int:class_id>')
@login_required
def get_assignments(class_id):
    if not is_student():
        return jsonify({'error': 'Unauthorized'}), 403
    enrolled = db.session.execute(
        select(Class).join(enrolled_classes).where(
            enrolled_classes.c.user_id == current_user.id,
            enrolled_classes.c.class_id == class_id
        )
    ).scalar_one_or_none()
    logger.info(f"Enrolled check for user {current_user.id} in class {class_id}: {enrolled}")
    if not enrolled:
        return jsonify({'error': 'Not enrolled in this class'}), 403
    assignments = db.session.scalars(
        select(Assignment).where(Assignment.class_id == class_id)
    ).all()
    logger.info(f"Assignments fetched for class {class_id}: {[a.id for a in assignments]}")
    return jsonify([
        {'id': assignment.id, 'title': assignment.title}
        for assignment in assignments
    ])

@assignments.route('/api/assignment_details/<int:assignment_id>')
@login_required
def get_assignment_details(assignment_id):
    if not is_student():
        return jsonify({'error': 'Unauthorized'}), 403
    assignment = db.session.get(Assignment, assignment_id)
    if not assignment:
        return jsonify({'error': 'Assignment not found'}), 404
    enrolled = db.session.execute(
        select(Class).join(enrolled_classes).where(
            enrolled_classes.c.user_id == current_user.id,
            enrolled_classes.c.class_id == assignment.class_id
        )
    ).scalar_one_or_none()
    if not enrolled:
        return jsonify({'error': 'Not enrolled in this class'}), 403
    return jsonify({
        'description': assignment.description,
        'due_date': assignment.due_date.isoformat() if assignment.due_date else None
    })

@assignments.route('/status/<int:sid>')
def status(sid):
    sub = Submission.query.get(sid)
    return jsonify({
        "success": True,
        "grade": sub.grade or None,
        "feedback": sub.feedback or "",
    })

@assignments.route('/class/<int:class_id>/report')
@login_required
def assignment_report(class_id):
    if not is_teacher():
        flash('Only teachers can view reports.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    cls = db.session.get(Class, class_id)
    if not cls or cls.teacher_id != current_user.id:
        flash('Class not found or unauthorized.', 'danger')
        return redirect(url_for('main.teacher_dashboard'))

    # Get all assignments for this class
    assignments = Assignment.query.filter_by(class_id=class_id).order_by(Assignment.due_date).all()
    if not assignments:
        flash('No assignments in this class.', 'info')
        return render_template('assignments/report.html', cls=cls, assignments=[], students=[])

    # Get all students in class
    #students = db.session.execute(
    #    db.text("""
    #        SELECT u.id, u.student_id, u.username
    #        FROM user u
    #        JOIN enrolled_classes ec ON u.id = ec.user_id
    #        WHERE ec.class_id = :cid 
    #            AND u.role != 'teacher'   -- EXCLUDE TEACHERS
    #        ORDER BY u.username
    #    """),
    #    {'cid': class_id}
    #).fetchall()
    students = db.session.query(User).join(User.enrolled_classes).filter(
        Class.id == class_id,
        User.role != 'teacher'
    ).order_by(User.student_id).all()

    # Build report: {student_id: {assignment_id: grade, 'average': float}}
    report = {}
    for student in students:
        sid = student.id
        report[sid] = {
            'student_id': student.student_id,
            'username': student.username,
            'grades': {},
            'average': None
        }

        total_weighted = 0.0
        total_weight = 0.0

        for assignment in assignments:
            sub = Submission.query.filter_by(
                student_id=sid,
                assignment_id=assignment.id
            ).first()

            grade = sub.grade if sub and sub.grade is not None else None
            report[sid]['grades'][assignment.id] = grade

            if grade is not None:
                total_weighted += grade * assignment.weight
                total_weight += assignment.weight

        if total_weight > 0:
            report[sid]['average'] = round(total_weighted / total_weight, 2)

    return render_template(
        'assignments/report.html',
        cls=cls,
        assignments=assignments,
        report=report
    )

# app/assignments.py
import csv
from io import StringIO
import urllib.parse
from flask import Response

@assignments.route('/class/<int:class_id>/report/csv')
@login_required
def assignment_report_csv(class_id):
    if not is_teacher():
        flash('Only teachers can download reports.', 'danger')
        return redirect(url_for('main.student_dashboard'))

    cls = db.session.get(Class, class_id)
    if not cls or cls.teacher_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('main.teacher_dashboard'))

    assignments = Assignment.query.filter_by(class_id=class_id).order_by(Assignment.due_date).all()
    if not assignments:
        flash('No assignments to export.', 'info')
        return redirect(url_for('assignments.assignment_report', class_id=class_id))

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

    output = StringIO()
    writer = csv.writer(output)

    headers = ['Student ID', 'Name']
    headers.extend([a.title for a in assignments])
    headers.append('Average')
    writer.writerow(headers)

    for student in students:
        sid = student.id
        row = [student.student_id, student.username]

        total_weighted = 0.0
        total_weight = 0.0

        for assignment in assignments:
            sub = Submission.query.filter_by(student_id=sid, assignment_id=assignment.id).first()
            grade = sub.grade if sub and sub.grade is not None else ''
            row.append(grade)
            if grade != '':
                total_weighted += float(grade) * assignment.weight
                total_weight += assignment.weight

        average = round(total_weighted / total_weight, 2) if total_weight > 0 else ''
        row.append(average)
        writer.writerow(row)

    output.seek(0)

    # SAFE FILENAME
    raw_name = f"{cls.name}_assignment_report.csv"
    safe_name = urllib.parse.quote(raw_name, safe=' _-')

    return Response(
        output,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{safe_name}"',
            'Cache-Control': 'no-cache'
        }
    )

# ------------------------------------------------------------
#  Teacher → View all submissions of ONE student (any class)
# ------------------------------------------------------------
@assignments.route('/student_submissions/<int:class_id>/<string:student_id>')
@login_required
def student_submissions(class_id, student_id):
    """Show all submissions of a student in a specific class."""
    if current_user.role != 'teacher':
        flash('Only teachers can view student submissions.', 'danger')
        return redirect(url_for('main.teacher_dashboard'))

    # 1. Verify class exists and teacher owns it
    cls = Class.query.get_or_404(class_id)
    if cls.teacher_id != current_user.id:
        flash('You do not teach this class.', 'danger')
        return redirect(url_for('main.manage_classes'))

    # 2. Verify student exists and is enrolled
    student = User.query.filter_by(student_id=student_id, role='student').first_or_404()
    enrolled = db.session.execute(
        db.text("SELECT 1 FROM enrolled_classes WHERE user_id = :uid AND class_id = :cid"),
        {"uid": student.id, "cid": class_id}
    ).scalar()
    if not enrolled:
        flash('This student is not enrolled in the class.', 'danger')
        return redirect(url_for('main.class_students', class_id=class_id))

    # 3. Get submissions in this class only
    submissions = db.session.execute(
        db.text("""
            SELECT 
                s.id,
                s.assignment_id,
                a.title AS assignment_title,
                c.name AS class_name,
                s.submitted_at,
                s.grade,
                s.feedback,
                s.file_path
            FROM submissions s
            JOIN assignments a ON s.assignment_id = a.id
            JOIN classes c ON a.class_id = c.id
            WHERE s.student_id = :student_id
              AND c.id = :class_id
            ORDER BY s.submitted_at DESC
        """),
        {"student_id": student.id, "class_id": class_id}
    ).fetchall()

    return render_template(
        'assignments/student_submissions.html',
        student=student,
        cls=cls,
        submissions=submissions
    )

from flask import send_file, abort
import os

@assignments.route('/submission/<int:submission_id>/download')
@login_required
def download_file(submission_id):
    """Download a submission file."""
    submission = Submission.query.get_or_404(submission_id)

    # Security: Teacher or student owner
    if current_user.role == 'teacher':
        if submission.assignment.class_.teacher_id != current_user.id:
            abort(403)
    elif current_user.role == 'student':
        if submission.student_id != current_user.id:
            abort(403)
    else:
        abort(403)

    if not submission.file_path or not os.path.exists(submission.file_path):
        flash('File not found or deleted.', 'danger')
        return redirect(request.referrer or url_for('main.dashboard'))

    return send_file(
        submission.file_path,
        as_attachment=True,
        download_name=os.path.basename(submission.file_path)
    )

@assignments.route('/api/quota-status')
@login_required
def quota_status():
    if not is_quota_exhausted():
        return jsonify({"status": "ok", "message": "Grading active"})
    
    reset_time = get_quota_reset_time()
    countdown = int((reset_time - datetime.utcnow()).total_seconds())
    
    hours = countdown // 3600
    minutes = (countdown % 3600) // 60
    seconds = countdown % 60
    
    return jsonify({
        "status": "locked",
        "message": "Daily quota reached",
        "countdown": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
        "resets_at": reset_time.strftime("%Y-%m-%d %H:%M UTC")
    })