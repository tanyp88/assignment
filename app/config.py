import os

class Config:
    SQLALCHEMY_DATABASE_URI = os.environ.get('SQLALCHEMY_DATABASE_URI') 
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    #CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL')
    #CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND')
    UPLOAD_FOLDER = '/app/uploads'




    # === Celery Config to suppress Deprecation Warning ===
    #CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True 

    # --- CELERY CONFIGURATION FIX ---
    # The old settings CELERY_BROKER_URL and CELERY_RESULT_BACKEND must be renamed
    # to the new, unprefixed format (broker_url and result_backend) to avoid the error.
    
    # OLD: CELERY_BROKER_URL is REMOVED
    # NEW: Use the unprefixed format
    broker_url = os.environ.get('CELERY_BROKER_URL')
    
    # OLD: CELERY_RESULT_BACKEND is REMOVED
    # NEW: Use the unprefixed format
    result_backend = os.environ.get('CELERY_RESULT_BACKEND')
    
    # --- Deprecation Warning Fix ---
    # CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP must also be renamed for consistency.
    # The old name caused the previous error, but the new name fixes the deprecation warning.
    
    # OLD: CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP is REMOVED
    # NEW: Use the unprefixed format
    broker_connection_retry_on_startup = True

    # CRITICAL FIX: Tell Celery to import your tasks module!
    CELERY_IMPORTS = ('app.tasks',)
