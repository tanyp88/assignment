#app/tasks.py
from celery import shared_task
# CRITICAL FIX: Import the global celery instance from the package's __init__
#from celery import Celery
from app import db, mail #, celery as celery_app
#from flask import current_app as flask_app  # Use current_app for Flask app instance
#from .celery_app import celery_app as celery
# Import all necessary application components
#from app import app 
from app.models import User, Assignment, Submission, Class
from datetime import datetime, timedelta # New imports for recovery logic
import logging, time
from typing import Optional
# Email and utility imports
import smtplib, imaplib, email, re, os, json
from email.mime.text import MIMEText
#from flask import current_app
#from celery_app import celery_app

# Removed unused import of google.cloud.pubsub_v1 and create_app

#import google.generativeai as genai
#from google.generativeai import types # New import for configuring response schema
from google.genai import Client # Corrected import for the Client class
from google.genai import types
from google.genai.errors import ClientError
from google.genai import errors # Ensure you import the errors submodule

# Configure the logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from google.genai.errors import APIError # Adjust import path as needed

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize Gemini Client
# The client automatically uses the GENAI_API_KEY environment variable
#client = genai.Client()
# Initialize the Gemini Client
# Define the new timeout value in seconds (e.g., 120 seconds)
NEW_TIMEOUT_SECONDS = 180
if GEMINI_API_KEY:
    client = Client(api_key=GEMINI_API_KEY)
    # Set the connection timeout here
    timeout=NEW_TIMEOUT_SECONDS
else:
    logger.error("GEMINI_API_KEY environment variable not set. Grading will fail.")

# --- HELPER FUNCTION ---
# --- FIX 1: Make Regex flexible for single or double quotes around the delay ---
def _extract_retry_delay_seconds(exc_message: str) -> Optional[float]:
    """
    Extracts retry delay from Gemini error message.
    If quota is exceeded (250/day), forces 14400s (4h) delay.
    """
    # === 1. DETECT QUOTA EXHAUSTED ===
    if "quota exceeded" in exc_message.lower() and "250" in exc_message:
        return 14400.0  # 4 hours

    # === 2. FALLBACK: Extract from 'retryDelay' or 'Please retry in Xs' ===
    # Try JSON-style retryDelay first
    match_detail = re.search(r"'retryDelay':\s*['\"](?P<delay>\d+)\s*s['\"]", exc_message)
    if match_detail:
        try:
            return float(match_detail.group('delay'))
        except ValueError:
            pass

    # Try natural language: "Please retry in 21.7s"
    match_message = re.search(r'Please retry in\s*(?P<delay>\d+(\.\d+)?)\s*s', exc_message)
    if match_message:
        try:
            return float(match_message.group('delay'))
        except ValueError:
            pass

    return None

def is_daily_quota_exceeded(msg: str) -> bool:
    return (
        "quota exceeded" in msg.lower() and
        "250" in msg and
        "generate_content_free_tier_requests" in msg
    )

from app.utils.quota_lock import is_quota_exhausted, set_quota_exhausted, clear_quota_lock

@shared_task(name='app.tasks.quota.reset_gemini_quota_daily')
def reset_gemini_quota_daily():
    clear_quota_lock()
    logger.info("Gemini daily quota lock cleared — grading resumed")


#flask_app=create_app()     #circular imports resolved
#celery_app = make_celery(flask_app)
#@celery_app.task(bind=True, max_retries=5, default_retry_delay=60)
#@shared_task(bind=True, max_retries=None,rate_limit='1/m') # Set max_retries to None for long-term deferral
@shared_task(bind=True, max_retries=None,rate_limit='10/m') # Set max_retries to None for long-term deferral
def grade_submission(self, submission_id):
#    grade_submission_nodelay( submission_id)
#    pass
#def grade_submission_nodelay( submission_id):
    """Grades a submission using the Gemini API and updates the database."""
    
    if is_quota_exhausted():
        # Do NOT retry — just skip or mark as pending
        logging.warning(f"Quota exhausted. pending submission {submission_id} for grading later")
        return  
    
    #with flask_app.app_context():
    # Check if client is initialized
    if not GEMINI_API_KEY or not client:
      logger.error(f"Cannot grade submission {submission_id}: API client not initialized.")
      return 

    # Task status messages are critical for debugging
    logger.warning(f"Starting grade_submission task for submission {submission_id}")

    uploaded_file = None  # Initialize file object for cleanup

    # Define a safety buffer for retries to ensure we wait longer than requested
    SAFETY_BUFFER = 15.0 

    try:
      # 1. Fetch Submission Data
      #submission = Submission.query.get(submission_id)

      from app.models import Submission, ProjectSubmission  # ← 加这一行

      # 1. 先查 ProjectSubmission
      submission = ProjectSubmission.query.get(submission_id)
      content =''
      file_path =''
      assignment_title =''      
      if submission:
        content = submission.content_md or ""
        file_path = submission.file_path
        assignment_title = f"期末项目: {submission.project.title} - {submission.topic.title}"
      else:
        # 2. 回退到普通作业
        submission = Submission.query.get_or_404(submission_id)
        content = submission.content or ""
        file_path = submission.file_path
        assignment_title = submission.assignment.title


      if not submission:
          logger.error(f"Submission ID {submission_id} not found.")
          return
      else:
        logger.warning(f"{datetime.now()} Submission ID {submission_id} found.")

      if submission.file_path and submission.file_path.lower().endswith('.docx'):
        logger.warning(f"SKIPPING .docx file: {submission.file_path}")
        
        submission.grade = None
        submission.feedback = (
            "不支持 .docx 格式！\n"
            "请将 Word 文档另存为 PDF 后再提交。\n"
            "支持格式：PDF、TXT、JPG、PNG"
        )
        submission.graded_at = datetime.utcnow()
        
        try:
            db.session.commit()
            logger.info(f"Marked submission {submission_id} as unsupported format")
        except Exception as e:
            db.session.rollback()
            logger.error(f"DB commit failed for unsupported file: {e}")
        
        return  # ← EXIT EARLY — NO GEMINI CALL



      # --- NEW STEP: 2. Handle File Attachment ---
      contents = []
      uploaded_file = None
      if file_path and os.path.exists(file_path):
            # Construct the absolute path to the file on the local filesystem
            # IMPORTANT: Replace '/app/' with the actual root path if needed.
            local_file_path = file_path 
            
            logger.warning(f"Attempting to upload file: {local_file_path}")
            

            # --- START NEW TRY/EXCEPT BLOCK FOR FILE UPLOAD ---
            try:
              # Use the API to upload the file
              uploaded_file = client.files.upload(file=local_file_path)
              # Verify the uploaded_file object is valid (it should have a name/uri if successful)
              if not uploaded_file or not hasattr(uploaded_file, 'name'):
                    # Catch cases where the SDK returns an unexpected object instead of raising an error
                    raise APIError(f"Gemini file upload failed for {local_file_path}: API returned an unusable file object.")
              
              logger.info(f"File uploaded successfully. Name: {uploaded_file.name}")
              
              # Add the uploaded file object to the contents list
              contents.append(uploaded_file)
              '''
              import base64, magic
              mime_type = magic.from_file(submission.file_path, mime=True)
              with open(submission.file_path, 'rb') as f:
                  b64 = base64.b64encode(f.read()).decode()
              parts.append({
                  "inline_data": {
                      "mime_type": mime_type,
                      "data": b64
                }
              })
              ''' 
            except ClientError as file_upload_exc:
                # Catch specific API errors during upload (e.g., 400 Bad Request)
                logger.error(f"File upload failed for ID {submission_id} with API Error {file_upload_exc.status_code}: {file_upload_exc}")
                
                # For file upload issues, retry with a short delay (8 seconds)
                # It's highly unlikely this is a quota issue, so we use a standard retry.
                raise self.retry(exc=file_upload_exc, countdown=15)
            

      # 3. Define the Prompt and System Instruction
      system_instruction = (
          "You are an academic grader. Review the student's submission and assign "
          "a grade out of 100 and provide constructive feedback. "
          "The 'feedback' MUST BE in Chinese. " # <-- Added language instruction here
          "You MUST ONLY respond with a JSON object containing the fields: 'grade' (integer) "
          "and 'feedback' (string). DO NOT include any text outside the JSON block."
      )

      prompt_text = (
          f"Assignment: {assignment_title}\n"
          f"Full Grade Scale: 100 points maximum.\n"
          f"Submission Content:\n---\n{content}\n---"
          + ("\n\n**Note: The attached file should be considered the primary submission content if exists.**" 
               if uploaded_file else "")
      )

      # Add the text prompt content (must be after the file if it exists)
      contents.append(prompt_text)
       
      # 4. Define the Structured Output Schema
      response_schema = types.Schema(
          type=types.Type.OBJECT,
          properties={
              "grade": types.Schema(type=types.Type.INTEGER, description="The final numerical score out of 100."),
              "feedback": types.Schema(type=types.Type.STRING, description="Constructive text feedback for the student.")
          },
          required=["grade", "feedback"]
      )

      # 5. Define Generation Configuration as a dictionary (to fix the keyword argument error)
      generation_config_dict = {
          "system_instruction": system_instruction,
          "response_mime_type": "application/json",
          "response_schema": response_schema
      }

      logger.warning("Calling Gemini API with prompt:\n" + prompt_text[:200] + "...")
      
      # --- MODIFIED STEP: 6. Call the API ---
      # --- FIX APPLIED: Reverting keyword to 'config' and passing the dictionary ---
      # This addresses the 'unexpected keyword argument generation_config' error.
      response = client.models.generate_content(
                model='gemini-2.5-flash-preview-09-2025',
                contents=contents,
                config=generation_config_dict # <-- Using 'config' keyword with the dict representation
      )
      
      # 7. Process and Validate Response
      json_text = response.text.strip()
      import json
      try:
          grading_result = json.loads(json_text)
          grade = int(grading_result.get('grade'))
          feedback = grading_result.get('feedback')
          
          if grade is None or feedback is None:
              raise ValueError("JSON response missing 'grade' or 'feedback'.")  
      except (json.JSONDecodeError, ValueError) as e:
          # Handle non-compliant LLM output
          logger.error(f"LLM returned invalid JSON for submission ID {submission_id}. Response: {json_text}. Error: {e}")
          raise self.retry(exc=e) # Retry the task on invalid API response
      
      # 8. Update Database
      submission.grade = grade
      submission.feedback = feedback
      submission.graded_at = datetime.utcnow()

      # 🛑 THE FIX: Merge or Refresh the object before committing 🛑
      try:
        # This re-fetches the object from the database, dropping any old state
        # and checking if the record still exists.
        submission = db.session.merge(submission) 
      except Exception as e:
        # Handle case where record was deleted, which also prevents commit
        logger.error(f"Submission {submission_id} could not be merged: {e}")
        return


      db.session.commit()
      
      logger.info(f"Graded submission {submission_id}: Grade {grade}")

      # 9. Send notification (async task)
      '''
      send_async_email(submission.student.email,
                       SUBJECT=f"{submission.assignment.title} 作业号 {submission_id}: 分数 {grade}",
                       info=f"提交的内容：\n---\n{submission.content}\n---\n反馈：{feedback}"
                       )
      '''
      from app.utils.email import send_email
      send_email(
            to=submission.student.email,
            subject=f"{submission.assignment.title} 作业号 {submission_id}: 分数 {grade}",
            logger=logger,
            template="email/assignment_feedback.html",   # 你建一个这个模板就行
            submission=submission,
            submission_id=submission_id,
            grade=grade,
            feedback=feedback
      )
      # --- START MANUAL RATE LIMIT DELAY (15 SECONDS) ---
      #logger.info(f"Manual rate limit: Pausing for 15 seconds after successful grade for ID {submission_id}.")
      #time.sleep(15)
      # --- END MANUAL RATE LIMIT DELAY ---

    # --- REVISED EXCEPTION HANDLING BLOCK ---
    except (APIError, ClientError, ValueError, KeyError) as exc: 
        db.session.rollback() # Rollback session on error
        

        exc_message = str(exc)
        status_code = getattr(exc, 'status_code', None)
        api_suggested_delay = _extract_retry_delay_seconds(exc_message)

        if is_daily_quota_exceeded(exc_message):
            set_quota_exhausted()  # ← HERE, NOT IN _extract_retry_delay_seconds
            logging.warning("DAILY QUOTA (250) REACHED → LOCKING FOR 24H")
            raise self.retry(countdown=14400, exc=exc)
        
        # Check for specific fatal file errors (KeyError, ValueError, or 400 ClientError)
        is_fatal_file_error = (
            isinstance(exc, KeyError) and str(exc) == "'file'"
        ) or isinstance(exc, ValueError) or (
            isinstance(exc, ClientError) and status_code == 400
        )

        if is_fatal_file_error:
            # 1. Log the failure reason
            logger.error(f"FATAL FILE PROCESSING ERROR for ID {submission_id}: Skipping grading. Reason: {exc}")
            
            # 2. Update the submission status in the database (mark for manual review)
            submission = Submission.query.get(submission_id)
            if submission:
                # Update status and feedback
                submission.grade = None 
                submission.feedback = f"FATAL ERROR: File upload/processing failed (KeyError/400). Requires manual review. Error: {exc}"
                submission.graded_at = datetime.utcnow()
                
                # Commit the status change
                try:
                    db.session.commit()
                except Exception as db_exc:
                    logger.error(f"Failed to commit manual review status for {submission_id}: {db_exc}")
                    db.session.rollback()
                    
                logger.warning(f"Submission {submission_id} marked for manual review and SKIPPED.")
                return # Crucial: Exit the task successfully without raising self.retry()
            else:
                logger.error(f"Submission {submission_id} not found during error cleanup.")
                return

        # --- Remaining Logic for Retries (Transient Errors) ---
        # Determine the countdown based on priority:
        # 1. API Suggested Delay
        # 2. Hard Quota Limit fallback (14400s) if RESOURCE_EXHAUSTED and no short delay found
        # 3. Default short retry (15s)

        countdown_value = api_suggested_delay

        # 🛑 FIX: If no delay was extracted, check if it's a hard quota exhaustion.
        if countdown_value is None and 'RESOURCE_EXHAUSTED' in exc_message:
            countdown_value += SAFETY_BUFFER
            # Fallback for hard daily quota where API failed to specify a retry delay,
            # indicating we must wait for the next day.
            countdown_value = 14400 
            logger.error(f"Daily API quota exceeded (429 RESOURCE_EXHAUSTED) without explicit retry delay. Retrying task {self.request.id} in {countdown_value}s.")
        
        elif countdown_value is None:
            # Default short retry for transient errors (network, other client errors)
            countdown_value = 30
            logger.error(f"Transient Error (Code: {status_code}). Retrying task {self.request.id} with default delay ({countdown_value}s). Exc: {exc}")
        
        else:
            countdown_value += SAFETY_BUFFER # Increased from 15 to be safer
             # Using API suggested delay (this will catch the 53s delay in the log)
            logger.warning(f"Transient Error detected (Code: {status_code}). Using API-suggested delay of {countdown_value}s. Exc: {exc}")


        # Raise self.retry using the determined countdown value
        raise self.retry(exc=exc, countdown=int(round(countdown_value)))
    # --- The 'finally' block remains essential for file cleanup ---
    finally:
        if uploaded_file and hasattr(uploaded_file, 'name'):
            try:
                client.files.delete(name=uploaded_file.name)
                logger.info(f"Successfully deleted uploaded file: {uploaded_file.name}")
            except Exception as cleanup_exc:
                logger.error(f"Failed to delete uploaded file {uploaded_file.name}: {cleanup_exc}")

    logger.info(f"Graded submission {submission_id}: Done Successfully")



@shared_task
def generate_report():
    """
    Scheduled task (runs every 30s) that generates a report and acts as a
    RECOVERY MECHANISM for failed/stuck submissions.
    """
    logger.info("Starting generate_report (and recovery check) task")
    # This task requires the app context, which is handled by ContextTask wrapper
    
    # 5 minutes is a reasonable timeout for a grading process.
    timeout_dt = datetime.utcnow() - timedelta(minutes=5)
    
    # Find submissions that have not been graded and are older than 5 minutes
    # Note: We assume 'submitted_at' exists on the Submission model
    stuck_submissions = Submission.query.filter(
        Submission.grade.is_(None),
        Submission.submitted_at < timeout_dt # The ORM will raise if 'submitted_at' doesn't exist
    ).all()
    
    report_lines = []
    
    if stuck_submissions:
        logger.warning(f"Found {len(stuck_submissions)} pending submissions for recovery. Re-queuing...")
        for s in stuck_submissions:
            # Explicitly call grade_submission.delay() to re-queue the task
            grade_submission.delay(s.id)
            logger.warning(f"RE-QUEUING GRADING FOR SUBMISSION ID: {s.id}")
            report_lines.append(f"Student: {s.student.username}, Assignment: {s.assignment_id}, Grade: Pending (RE-QUEUED)")
    
    # Generate simple report summary (for logging)
    all_submissions = Submission.query.all()
    
    if not stuck_submissions:
        for s in all_submissions:
            grade_status = s.grade if s.grade is not None else "Pending"
            report_lines.append(f"Student: {s.student.username}, Assignment: {s.assignment_id}, Grade: {grade_status}")

    logger.warning("Submission Report\n" + "\n".join(report_lines))


#------------------------------------------------------------------------------------------------------#


#------------------------------------------------------------------------------------------------------#

@shared_task(bind=True, max_retries=3)
def process_email_submission(self):
    try:
        with flask_app.app_context():
            print("[DEBUG] Starting process_email_submission task")
            # Outlook IMAP settings
            #IMAP_SERVER = 'outlook.office365.com'
            IMAP_SERVER = 'mail.cctan.ca'
            IMAP_PORT = 993
            #EMAIL_USER = 'myceassignments@outlook.com'
            EMAIL_USER = 'xxxxxx@cctan.ca'
            #EMAIL_PASS = os.environ.get('EMAIL_PASSWORD', 'your_app_password')  # Use app-specific password
            EMAIL_PASS = os.environ.get('CCTANMAIL_PASSWORD')  #
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            print("[DEBUG] Connecting to Outlook IMAP")
            mail.login(EMAIL_USER, EMAIL_PASS)
            print("[DEBUG] Logged in to Outlook IMAP")
            mail.select('INBOX')
            _, message_numbers = mail.search(None, 'UNSEEN')
            print(f"[DEBUG] Found {len(message_numbers[0].split())} unseen emails")
            for num in message_numbers[0].split():
                _, msg_data = mail.fetch(num, '(RFC822)')
                email_body = msg_data[0][1]
                msg = email.message_from_bytes(email_body)
                subject = email.header.decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                sender = msg['From']
                print(f"[DEBUG] Processing email from {sender} with subject: {subject}")

                # Parse subject with regex
                match = re.match(r'Assignment ID: (\d+), Class ID: (\d+) - (.+) (\w+)', subject.strip())
                if not match:
                    print(f"[ERROR] Invalid subject format: {subject}. Expected: 'Assignment ID: <id>, Class ID: <id> - <name> <id>'")
                    # Send auto-reply for invalid format
                    send_reply(sender, "Invalid Submission Format", "Please use the format: Assignment ID: <id>, Class ID: <id> - <name> <id>")
                    mail.store(num, '+FLAGS', '\\Seen')  # Mark as seen
                    continue

                assignment_id, class_id, student_name, student_id = match.groups()
                assignment_id = int(assignment_id)
                class_id = int(class_id)
                print(f"[DEBUG] Parsed: Assignment {assignment_id}, Class {class_id}, Student {student_name} {student_id}")

                # Find user by email (sender)
                user = User.query.filter_by(email=sender).first()
                if not user or user.student_id != student_id:
                    print(f"[ERROR] No matching user found for email: {sender} or student ID mismatch")
                    send_reply(sender, "Invalid Student ID", "Your student ID does not match your registered email. Please check and resubmit.")
                    mail.store(num, '+FLAGS', '\\Seen')
                    continue

                # Validate assignment and enrollment
                assignment = Assignment.query.get(assignment_id)
                if not assignment or assignment.class_id != class_id:
                    print(f"[ERROR] Invalid assignment {assignment_id} or class {class_id}")
                    send_reply(sender, "Invalid Assignment", "The assignment or class ID is invalid. Please check and resubmit.")
                    mail.store(num, '+FLAGS', '\\Seen')
                    continue

                # Check enrollment (assuming User has a relationship to classes)
                if user not in assignment.course.students:
                    print(f"[ERROR] User {user.student_id} not enrolled in class {class_id}")
                    send_reply(sender, "Not Enrolled", "You are not enrolled in this class. Contact the instructor.")
                    mail.store(num, '+FLAGS', '\\Seen')
                    continue

                # Extract content from body (plain text priority)
                content = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            break
                else:
                    content = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

                if not content.strip():
                    print(f"[ERROR] No content in email body")
                    send_reply(sender, "No Content", "Your email body is empty. Please include your submission text.")
                    mail.store(num, '+FLAGS', '\\Seen')
                    continue

                # Create submission
                submission = Submission(
                    student_id=user.id,
                    assignment_id=assignment_id,
                    content=content.strip()
                )
                db.session.add(submission)
                db.session.commit()
                print(f"[INFO] Submission created: {submission.id} for user {user.student_id}")

                # Trigger grading
                grade_submission.delay(submission.id)

                # Send confirmation reply
                send_reply(sender, "Submission Received", f"Your submission for Assignment {assignment.title} (ID: {assignment_id}) has been received and queued for grading. Submission ID: {submission.id}")

                # Mark as seen
                mail.store(num, '+FLAGS', '\\Seen')

            mail.logout()
            print("[INFO] Completed process_email_submission task")
    except Exception as e:
        print(f"[ERROR] Error in process_email_submission: {str(e)}")
        self.retry(countdown=60, exc=e)

# Helper function to send reply (using SMTP)
def send_reply(to_email, subject, body):
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = 'myceassignments@outlook.com'
        msg['To'] = to_email
        with smtplib.SMTP('outlook.office365.com', 587) as server:
            server.starttls()
            server.login('myceassignments@outlook.com', os.environ.get('EMAIL_PASSWORD', 'your_app_password'))
            server.sendmail('myceassignments@outlook.com', to_email, msg.as_string())
        print(f"[INFO] Reply sent to {to_email}")
    except Exception as e:
        print(f"[ERROR] Failed to send reply to {to_email}: {str(e)}")
