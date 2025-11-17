# utils/quota_lock.py
import redis
import time
#from django.conf import settings
from flask import current_app
import logging

logger = logging.getLogger(__name__)
#r = redis.Redis.from_url(settings.CELERY_BROKER_URL)

QUOTA_LOCK_KEY = "gemini:quota:exhausted"
QUOTA_RESET_TIME = 86400  # 24 hours in seconds

def get_redis():
    """Get Redis client from Flask config"""
    CELERY_BROKER_URL = current_app.config.get('CELERY_BROKER_URL', 'redis://redis:6379/0')
    return redis.Redis.from_url(CELERY_BROKER_URL)

def set_quota_exhausted():
    """Mark quota as exhausted for ~24h"""
    try:
        r = get_redis()
        r.setex(QUOTA_LOCK_KEY, QUOTA_RESET_TIME, int(time.time()))
        logger.warning("Gemini quota exhausted — locked for 24h")
    except Exception as e:
        logger.error(f"Failed to set quota lock: {e}")

def is_quota_exhausted():
    try:
        r = get_redis()
        return r.get(QUOTA_LOCK_KEY) is not None
    except Exception as e:
        logger.error(f"Failed to check quota lock: {e}")
        return False

def clear_quota_lock():  # For testing
    """Clear quota lock (called daily)"""
    try:
        r = get_redis()
        r.delete(QUOTA_LOCK_KEY)
        logger.info("Gemini quota lock cleared — grading resumed")
    except Exception as e:
        logger.error(f"Failed to clear quota lock: {e}")

from datetime import datetime, timedelta

def get_quota_reset_time():
    now = datetime.utcnow()
    reset = now.replace(hour=0, minute=10, second=0, microsecond=0)
    if now >= reset:
        reset += timedelta(days=1)
    return reset