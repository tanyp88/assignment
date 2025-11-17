# app/qq_login.py
import requests
import json
from urllib.parse import urlencode

class QQQRLoginService:
    def __init__(self, app_id, app_key):
        self.app_id = app_id
        self.app_key = app_key
        self.base_url = "https://graph.qq.com"

    def generate_qr_url(self, redirect_uri, state):
        params = {
            'response_type': 'code',
            'client_id': self.app_id,
            'redirect_uri': redirect_uri,
            'state': state,
            'scope': 'get_user_info',
            'display': 'qr'
        }
        return f"{self.base_url}/oauth2.0/authorize?{urlencode(params)}"

    def get_access_token(self, code, redirect_uri):
        url = f"{self.base_url}/oauth2.0/token"
        params = {
            'grant_type': 'authorization_code',
            'client_id': self.app_id,
            'client_secret': self.app_key,
            'code': code,
            'redirect_uri': redirect_uri
        }
        response = requests.get(url, params=params)
        if not response.ok:
            return None
        result = dict(item.split("=") for item in response.text.split("&"))
        return result.get('access_token')

    def get_openid(self, access_token):
        url = f"{self.base_url}/oauth2.0/me"
        response = requests.get(url, params={'access_token': access_token})
        if not response.ok:
            return None
        data = response.text.replace('callback(', '').replace(');', '')
        return json.loads(data).get('openid')

    def get_user_info(self, access_token, openid):
        url = f"{self.base_url}/user/get_user_info"
        params = {
            'access_token': access_token,
            'oauth_consumer_key': self.app_id,
            'openid': openid
        }
        response = requests.get(url, params=params)
        if not response.ok:
            return {}
        info = response.json()
        return {
            'openid': openid,
            'nickname': info.get('nickname'),
            'avatar': info.get('figureurl_qq_2'),
            'gender': info.get('gender'),
            'province': info.get('province'),
            'city': info.get('city')
        }