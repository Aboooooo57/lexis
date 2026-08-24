import os
import datetime
from typing import Optional, Dict, Any
import jwt
from google.oauth2 import id_token
from google.auth.transport import requests
from google_auth_oauthlib.flow import Flow
from fastapi import HTTPException, Security, Request, Cookie, Depends

from api import config, database

# In-memory store for valid OAuth states and their PKCE code_verifiers
VALID_STATES = {} # Dict[state, code_verifier]

class GoogleAuth:
    @staticmethod
    def get_login_url() -> tuple[str, str]:
        client_config = {
            "web": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
            redirect_uri=config.GOOGLE_REDIRECT_URI
        )
        
        auth_url, state = flow.authorization_url(prompt="consent")
        code_verifier = getattr(flow, 'code_verifier', None)
        VALID_STATES[state] = code_verifier
        return auth_url, state

    @staticmethod
    async def verify_code(code: str, code_verifier: Optional[str] = None) -> Dict[str, Any]:
        client_config = {
            "web": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
            redirect_uri=config.GOOGLE_REDIRECT_URI
        )
        
        try:
            if code_verifier:
                flow.code_verifier = code_verifier
            flow.fetch_token(code=code)
            credentials = flow.credentials
            
            id_info = id_token.verify_oauth2_token(
                credentials.id_token, requests.Request(), config.GOOGLE_CLIENT_ID
            )
            
            return {
                "id": id_info["sub"],
                "email": id_info["email"],
                "name": id_info.get("name"),
                "picture": id_info.get("picture"),
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to verify Google code: {str(e)}")

    @staticmethod
    def create_access_token(user_id: str) -> str:
        payload = {
            "sub": user_id,
            "iss": "lexume-lab-system",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=config.JWT_EXPIRATION_HOURS),
            "iat": datetime.datetime.utcnow(),
        }
        return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)

    @staticmethod
    def verify_access_token(token: str) -> str:
        try:
            payload = jwt.decode(
                token, 
                config.JWT_SECRET, 
                algorithms=[config.JWT_ALGORITHM],
                issuer="lexume-lab-system"
            )
            return payload["sub"]
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

class GoogleDriveAuth:
    @staticmethod
    def get_auth_url() -> tuple[str, str]:
        client_config = {
            "web": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
            redirect_uri=config.GOOGLE_DRIVE_REDIRECT_URI
        )
        
        auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
        code_verifier = getattr(flow, 'code_verifier', None)
        VALID_STATES[state] = code_verifier
        return auth_url, state

    @staticmethod
    async def get_credentials(code: str, code_verifier: Optional[str] = None):
        client_config = {
            "web": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
            redirect_uri=config.GOOGLE_DRIVE_REDIRECT_URI
        )
        
        if code_verifier:
            flow.code_verifier = code_verifier
            
        flow.fetch_token(code=code)
        return flow.credentials

async def get_current_user_id(request: Request, access_token: Optional[str] = Cookie(None)) -> str:
    # Try cookie first (OWASP recommended for browsers)
    token = access_token
    
    # Fallback to Authorization header (for API clients/testing)
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    try:
        user_id = GoogleAuth.verify_access_token(token)
        return user_id
    except HTTPException as e:
        raise e


async def require_active_subscription(user_id: str = Depends(get_current_user_id)) -> str:
    """
    Dependency for routes that should only be reachable with a real Lexume
    Plus subscription (e.g. the billing status/portal endpoints). Most
    extraction/narration routes do NOT use this directly — they accept an
    optional BYOK key and only fall into the subscription-gated path when
    none was supplied, via api/subscription.py's resolve_*_key() helpers,
    which is where this same active-subscription check happens for them.
    """
    if not await database.is_subscription_active(user_id):
        raise HTTPException(status_code=402, detail="An active Lexume Plus subscription is required.")
    return user_id
