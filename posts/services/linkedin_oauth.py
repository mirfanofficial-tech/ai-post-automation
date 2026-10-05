"""
LinkedIn OAuth 2.0 service — Phase 3.

Handles the full OAuth flow:
    1.  build_authorization_url()  →  redirect user to LinkedIn
    2.  exchange_code_for_token()  →  swap code for access token
    3.  fetch_linkedin_profile()   →  get member's LinkedIn ID + name

LinkedIn uses the OpenID Connect (OIDC) flow on top of OAuth 2.0.

Scopes used:
    openid          — OIDC identity
    profile         — first/last name, profile picture
    email           — email address
    w_member_social — create posts on the member's feed

LinkedIn App setup checklist:
    1. Go to https://www.linkedin.com/developers/apps
    2. Create / select your app
    3. Products tab → Add "Share on LinkedIn" (grants w_member_social)
    4. Products tab → Add "Sign In with LinkedIn using OpenID Connect"
       (grants openid, profile, email)
    5. Auth tab → Authorized redirect URLs — add EXACTLY:
         Local dev:   http://127.0.0.1:8000/posts/social-accounts/linkedin/callback/
         Production:  https://yourdomain.com/posts/social-accounts/linkedin/callback/

SECURITY:
    • Never log access_token or client_secret values.
    • The state parameter prevents CSRF on the callback.
    • Tokens are stored via SocialAccountCredential — not in session or logs.
"""

import secrets
import urllib.parse

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone


# ── LinkedIn endpoint constants ───────────────────────────────────────────────

_AUTH_URL     = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL    = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


# ── Callback URL ──────────────────────────────────────────────────────────────

def get_callback_url(request) -> str:
    """
    Return the absolute OAuth callback URL.

    Priority:
        1. LINKEDIN_REDIRECT_URI in settings/.env  → use as-is (production/proxy-safe)
        2. request.build_absolute_uri()             → auto-built (perfect for local dev)

    Local dev:   http://127.0.0.1:8000/posts/social-accounts/linkedin/callback/
    Production:  https://yourdomain.com/posts/social-accounts/linkedin/callback/
    """
    configured = getattr(settings, "LINKEDIN_REDIRECT_URI", "").strip()
    if configured:
        return configured
    path = reverse("posts:linkedin_callback")
    return request.build_absolute_uri(path)


# ── State expiry ──────────────────────────────────────────────────────────────

def is_state_expired(oauth_state) -> bool:
    """
    Return True if an OAuthState record is older than OAUTH_STATE_EXPIRY_MINUTES.
    Expired states should be rejected and deleted.
    """
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    return oauth_state.created_at < cutoff


def cleanup_expired_states():
    """
    Delete all expired OAuthState records.
    Called automatically on each callback to keep the table tidy.
    """
    from posts.models import OAuthState   # local import — avoids circular
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    deleted, _ = OAuthState.objects.filter(created_at__lt=cutoff).delete()
    return deleted


# ── Public OAuth API ──────────────────────────────────────────────────────────

def build_authorization_url(request) -> tuple[str, str]:
    """
    Build the LinkedIn authorization URL and a cryptographically random state token.

    Returns:
        (authorization_url, state)

    The caller MUST store `state` in the DB (OAuthState) before redirecting.
    It is verified on callback to prevent CSRF.
    """
    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id":     settings.LINKEDIN_CLIENT_ID,
        "redirect_uri":  get_callback_url(request),
        "state":         state,
        "scope":         settings.LINKEDIN_SCOPES,
    }
    url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return url, state


def exchange_code_for_token(request, code: str) -> dict:
    """
    Exchange the authorization code for an access token.

    Returns the token response dict:
        {
            "access_token": "...",
            "expires_in":   5183944,       # seconds (~60 days)
            "token_type":   "Bearer",
            "scope":        "email openid profile w_member_social",
        }

    Raises:
        LinkedInOAuthError — on any failure (bad code, network error, etc.)

    SECURITY: Never log client_secret. Never log access_token.
    """
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  get_callback_url(request),
            "client_id":     settings.LINKEDIN_CLIENT_ID,
            "client_secret": settings.LINKEDIN_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )

    if not resp.ok:
        raise LinkedInOAuthError(
            f"Token exchange failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "access_token" not in data:
        raise LinkedInOAuthError("Token response missing access_token.")

    return data


def fetch_linkedin_profile(access_token: str) -> dict:
    """
    Fetch the authenticated member's profile via the OIDC userinfo endpoint.

    Returns dict with:
        sub           — stable LinkedIn member ID  (store as external_account_id)
        name          — full display name
        given_name    — first name
        family_name   — last name
        email         — email address
        picture       — profile picture URL (optional)

    Raises:
        LinkedInOAuthError — if the request fails or profile is missing 'sub'.

    SECURITY: Never log access_token.
    """
    resp = requests.get(
        _USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )

    if not resp.ok:
        raise LinkedInOAuthError(
            f"Profile fetch failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "sub" not in data:
        raise LinkedInOAuthError("Profile response missing 'sub' field.")

    return data


# ── Exception ─────────────────────────────────────────────────────────────────

class LinkedInOAuthError(Exception):
    """Raised when any step of the LinkedIn OAuth flow fails."""
    pass
