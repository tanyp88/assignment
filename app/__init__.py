from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from celery import Celery
from celery.schedules import crontab  # ← ADD THIS LINE
import os, sys, redis
import logging
from dotenv import load_dotenv
from app.utils.quota_lock import clear_quota_lock  # Your lock file
load_dotenv()

#logging.basicConfig(level=logging.DEBUG)
#logger = logging.getLogger(__name__)
# Configure the root logger to capture DEBUG and INFO messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Optional: Keep third-party libraries like Werkzeug and Gunicorn from flooding the logs
logging.getLogger('gunicorn.error').setLevel(logging.INFO)
logging.getLogger('werkzeug').setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# --- GLOBALS ---
db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
mail = Mail()
csrf = CSRFProtect()

#celery_app = None
# 1. Define the Celery instance as a global singleton
celery = Celery(__name__) 
# --- END GLOBALS ---


def make_celery(app):
    # This function CONFIGURES the global 'celery' instance, it does not create a new one.
    
    # 1. Configure the global instance with app settings
    celery.conf.update(
        #app.import_name,
        backend=app.config.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0'), # Use CELERY_RESULT_BACKEND convention
        broker=app.config.get('CELERY_BROKER_URL', 'redis://redis:6379/0'), # Use CELERY_BROKER_URL convention
        broker_connection_retry_on_startup=True
    )

    # Important: Update config from object for general settings (like CELERY_BEAT_SCHEDULE)
    celery.config_from_object(app.config)

    # === ADD THIS BLOCK: QUOTA RESET TASK + SCHEDULE ===
    
    # Ensure beat schedule includes reset (safe to override)
    celery.conf.beat_schedule = celery.conf.get('beat_schedule', {})
    celery.conf.beat_schedule['reset-gemini-quota-daily'] = {
        'task': 'reset_gemini_quota_daily',
        'schedule': crontab(hour=0, minute=10),  # 00:10 UTC daily
        'args': (),
    }

    # Optional: Force update
    celery.conf.update(beat_schedule=celery.conf.beat_schedule,worker_concurrency=1,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
    )
    # ================================================

    # 2. Create a Celery Task class that wraps the run method in app_context
    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            # *** This is the CRITICAL FIX ***
            with app.app_context(): 
                return self.run(*args, **kwargs)
    # 2. Assign the new ContextTask class to the Celery app
    celery.Task = ContextTask

    logger.debug("Celery app initialized")
    return celery

from markupsafe import Markup
# 1. Define the custom filter function
def nl2br(value):
    """
    Replaces newlines with <br> tags and ensures the result is marked as safe HTML.
    """
    # Use Markup.escape to escape any HTML in the input (XSS prevention),
    # then replace newlines, and finally wrap the result in Markup
    # to tell Jinja2 that the output is safe to render.
    return Markup(Markup.escape(value).replace('\n', '<br>\n'))

# 2. Function to apply the filter to the Jinja environment
def register_jinja_filters(app):
    """
    Registers custom filters with the application's Jinja environment.
    """
    # This line registers the Python function 'nl2br' as a filter named 'nl2br' 
    # that can be used in your templates like {{ text | nl2br }}.
    app.jinja_env.filters['nl2br'] = nl2br
    
def create_app():
    app = Flask(__name__)
    register_jinja_filters(app)

    # FIX: Set SECRET_KEY to enable session usage (required for flash messages)
    # This key is crucial for CSRF protection used by Flask-WTF
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or 'a-default-secret-key-for-session-signing'
    app.config['VAPID_PUBLIC_KEY'] = os.getenv('VAPID_PUBLIC_KEY','kOXClMeKiKUjTVEa3pSMWDaAxG_hcNeesxsg8Pc9Em0')
    app.config['VAPID_PRIVATE_KEY'] = os.getenv('VAPID_PRIVATE_KEY','BBAkHA3cOrxb6uVnekUN5SjXCBGg582sGBc34is5CFQbBR2j6bCbogaao4dLC-jfFZiTTI5o7uNIz_HxRSvC_0o')

    # Configuration
    app.config.from_object('app.config.Config')
    app.config['UPLOAD_FOLDER'] = '/app/uploads'
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)
    mail.init_app(app)
    
    # Configure Flask-Login
    login.login_view = 'main.login' 
    @login.user_loader
    def load_user(user_id):
        from app.models import User
        return db.session.get(User, int(user_id))
    
    # Initialize Celery
    # This call configures the GLOBAL 'celery' instance defined above.
    celery = make_celery(app)
    
    # Register blueprints
    # FIX: Remove template_folder/static_folder when the templates are in the default /app/templates directory.
    from app.routes import main
    from app.assignments.routes import assignments
    
    # Main routes (uses default /app/templates and /app/static)
    app.register_blueprint(main, url_prefix='/zuoye')
    
    # Assignments routes (uses default /app/templates and /app/static, but routes prefixed)
    app.register_blueprint(assignments, url_prefix='/zuoye')

    '''
    from app.wechat import wechat as wechat_blueprint
    app.register_blueprint(wechat_blueprint, url_prefix='/zuoye/wechat')


    from app.qq import qq as qq_blueprint
    app.register_blueprint(qq_blueprint, url_prefix='/zuoye/qq')
    '''
    
    from app.attendance import attendance as attendance_blueprint
    app.register_blueprint(attendance_blueprint, url_prefix='/zuoye/attendance')
    # Register errors blueprint if you have one
    # from app.errors import bp as errors_bp
    # app.register_blueprint(errors_bp)

    from app.blog import blog as blog_blueprint
    app.register_blueprint(blog_blueprint)
    
    from app.project import project_bp as final_project_blueprint
    app.register_blueprint(final_project_blueprint, url_prefix='/zuoye/final_project')

    app.config['WTF_CSRF_EXEMPT_ROUTES'] = [
        # Exemption for the student check-in API route
        '/zuoye/attendance/checkin', 
        '/zuoye/attendance/checkin/', # <-- Try this one too!
        # Add other public API routes here if needed
    ]
    csrf.init_app(app)
    
    # ADD THIS LINE
    

    return app

# Remove the 'if __name__ == '__main__': app.run()' block from __init__.py 
# since you are using run_flask.py and gunicorn.
'''
bind_sessions = {}

@app.before_request
def cleanup_old_sessions():
    now = datetime.utcnow()
    to_del = [k for k, v in bind_sessions.items() if (now - v['created']).seconds > 300]
    for k in to_del:
        del bind_sessions[k]
'''

# ----------------------------------------------------------------------
# Redis Configuration (Using Docker service name 'redis')
# ----------------------------------------------------------------------
REDIS_HOST = 'redis'
REDIS_PORT = 6379
# Initialize a module-level variable to hold the Redis client instance
_redis_client_instance = None

def get_redis_client():
    """
    Initializes and returns a connected real Redis client (singleton).
    Returns None if the connection fails.
    """
    global _redis_client_instance
    
    if _redis_client_instance is not None:
        return _redis_client_instance

    try:
        # Use StrictRedis to enforce standard commands and automatically handle connection pooling
        client = redis.StrictRedis(
            host=REDIS_HOST, 
            port=REDIS_PORT, 
            db=0, # Assuming database 0, matching your CELERY config
            decode_responses=False # Store and retrieve data as bytes, as expected by setex/get
        )
        # Verify connection immediately
        client.ping()
        _redis_client_instance = client
        logger.info(f"Successfully connected to real Redis at {REDIS_HOST}:{REDIS_PORT}.")
        return _redis_client_instance
        
    except redis.exceptions.ConnectionError as e:
        logger.error(f"FATAL: Failed to connect to Redis at {REDIS_HOST}:{REDIS_PORT}. Binding functionality disabled. Error: {e}")
        # In a production environment, you would use a proper fallback or circuit breaker.
        # For this context, we return None and let the calling functions handle the missing client.
        return None
