# run_flask.py

#import logging
from app import create_app

'''
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.debug("Creating Flask app")
'''

app = create_app()
