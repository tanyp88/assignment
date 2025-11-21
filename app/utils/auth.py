# utils/auth.py
from itsdangerous import URLSafeTimedSerializer
#Flask applications to securely and temporarily sign data like user IDs or email addresses.
from flask import current_app

# Initializes the serializer using the application's unique secret key.
# This key is CRUCIAL for signing (creation) and verifying (loading) the token.
ts = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
#URLSafeTimedSerializer: This class signs the data using a cryptographic hash (HMAC) and base64-encodes 
#the result in a URL-safe manner. It also embeds a timestamp into the token.
#current_app.config['SECRET_KEY']: The unique, private secret key must be used here. If the key changes, 
#or if an attacker tries to use a different key, verification will fail.

'''
ts.dumps(data, salt): This method takes your sensitive data (email) and converts it into a signed token (token).

email: The data being protected.

salt: A required, unique string ('password-reset-salt') that makes this token type distinct from any other token 
type used in your application (e.g., email confirmation tokens). This prevents cross-token misuse.

Output (token): The resulting string is typically sent to the user (e.g., in a password reset link). It looks something 
like eyJlbWFpbCI6ImdpbmV2cmFAZ21haWwuY29tIn0.Y-Lz7A.yLpB5....
'''
def generate_reset_token(email):
    return ts.dumps(email, salt='pass1word-reset-salt1')

'''
ts.loads(token, salt, max_age): This method performs three crucial security checks on the received token:

Integrity Check: It verifies the cryptographic signature using the SECRET_KEY and the salt. If the token's content was 
tampered with, this step fails, raising a BadSignature exception.

Uniqueness Check: It ensures the provided salt matches the one used during creation.

Timestamp Check: It verifies that the current time is not past the time limit specified by max_age (e.g., 3600 seconds 
for 1 hour). If expired, it raises a SignatureExpired exception.

Output (email): If all checks pass, the original, unsigned data (email) is safely retrieved.
'''    
def verify_reset_token(token, expiration=1800):  # 默认 30 分钟有效
    try:
        email = ts.loads(token, salt='pass1word-reset-salt1', max_age=expiration)
        return email
    except:
        return False

