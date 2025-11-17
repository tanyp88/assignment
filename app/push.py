# app/push.py
from pywebpush import webpush, WebPushException
import json
import os

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_CLAIM_EMAIL = "mailto:tanyep@cctan.ca"

def send_push(user_id, title, body):
    from app.models import PushSubscription
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    payload = json.dumps({"title": title, "body": body})

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth}
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL}
            )
        except WebPushException as ex:
            print(f"Push failed: {ex}")
            if ex.response and ex.response.json():
                error = ex.response.json()
                if error.get("code") == 410:  # Gone
                    db.session.delete(sub)
                    db.session.commit()