# app/loaders.py
from app import logger, db, login_manager # Import the initialized manager and db


@login_manager.user_loader
def load_user(user_id):
    logger.info(f"load_user called")
    from app.models import User
    try:
        user = db.session.get(User, int(user_id))
        if user and not user.is_active:
            return None
        return user
    except Exception as e:
        logger.error(f"load_user 出错: {e}")
        return None

    