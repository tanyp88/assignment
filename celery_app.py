#celery_app.py
from app import create_app, celery
import logging

# Set up logging for the worker/beat processes
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# 1. Initialize the Flask application
# This runs the create_app() function, which internally calls make_celery(app),
# which fully configures the GLOBAL 'celery' instance imported above.
logger.debug("Creating Flask app to configure Celery.")
flask_app = create_app()

# We expose the fully configured 'celery' instance as 'celery_app'.
# This is the object that Celery Beat and Worker must target.
celery_app = celery
