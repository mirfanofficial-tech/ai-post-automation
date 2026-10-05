"""
Facebook OAuth 2.0 service — Facebook Pages.

Handles the full OAuth flow for connecting Facebook Pages:
    1.  build_authorization_url()    →  redirect user to Facebook
    2.  exchange_code_for_token()    →  swap code for user access token
    3.  exchange_for_long_lived_token() → extend token life to ~60 days
    4.  fetch_facebook_pages()       →  list pages the user manages
    5.  fetch_facebook_profile()     →  get user's name/id for display

Facebook App setup checklist:
    1. Go to https://developers.facebook.com/apps/ and select your app
    2. Add "Facebook Login" product to the app
    3. Facebook Login → Settings → Valid OAuth Redirect URIs — add EXACTLY:
         Local dev:   http://127.0.0.1:8000/posts/social-accounts/facebook/callback/
         Production:  https://yourdomain.com/posts/social-accounts/facebook/callback/
    4. App Permissions → Request: pages_show_list, pages_manage_posts, public_profile

Scopes used:
    public_profile      — basic user identity
    pages_show_list     — list pages the user manages
    pages_manage_posts  — create/publish posts on managed pages

Token notes:
    • The callback gives a short-lived user access token (~1–2 hours)
    • We immediately exchange it for a long-lived user token (~60 days)
    • From the long-lived user token we fetch Page tokens (never expire
      while the user token is valid, and auto-refresh when re-connected)
    • We store the Page access token — not the user token — for publishing

SECURITY:
    • Never log access_token or app_secret values.
    • The state parameter prevents CSRF on the callback.
    • Tokens are stored via SocialAccountCredential — not in session or logs.
"""

import secrets
import urllib.parse

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone


# ── Facebook endpoint constants ───────────────────────────────────────────────

_AUTH_URL       = "https://www.facebook.com/v19.0/dialog/oauth"
_TOKEN_URL      = "https://graph.facebook.com/v19.0/oauth/access_token"
_ME_URL         = "https://graph.facebook.com/v19.0/me"
_PAGES_URL      = "https://graph.facebook.com/v19.0/me/accounts"


# ── Callback URL ──────────────────────────────────────────────────────────────

def get_callback_url(request) -> str:
    """
    Return the absolute OAuth callback URL for Facebook.

    Priority:
        1. FACEBOOK_REDIRECT_URI in settings/.env  → use as-is (production/proxy-safe)
        2. request.build_absolute_uri()             → auto-built (perfect for local dev)

    Local dev:   http://127.0.0.1:8000/posts/social-accounts/facebook/callback/
    Production:  https://yourdomain.com/posts/social-accounts/facebook/callback/
    """
    configured = getattr(settings, "FACEBOOK_REDIRECT_URI", "").strip()
    if configured:
        return configured
    path = reverse("posts:facebook_callback")
    return request.build_absolute_uri(path)


# ── State expiry ──────────────────────────────────────────────────────────────

def is_state_expired(oauth_state) -> bool:
    """Return True if an OAuthState record is older than OAUTH_STATE_EXPIRY_MINUTES."""
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    return oauth_state.created_at < cutoff


def cleanup_expired_states():
    """Delete all expired OAuthState records for Facebook."""
    from posts.models import OAuthState   # local import — avoids circular
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    deleted, _ = OAuthState.objects.filter(
        created_at__lt=cutoff,
        platform__code="facebook",
    ).delete()
    return deleted


# ── Public OAuth API ──────────────────────────────────────────────────────────

def build_authorization_url(request) -> tuple[str, str]:
    """
    Build the Facebook authorization URL and a cryptographically random state token.

    Returns:
        (authorization_url, state)

    The caller MUST store `state` in the DB (OAuthState) before redirecting.
    It is verified on callback to prevent CSRF.
    """
    state = secrets.token_urlsafe(32)

    scopes = getattr(
        settings,
        "FACEBOOK_SCOPES",
        "public_profile,pages_show_list,pages_manage_posts",
    )

    params = {
        "client_id":     settings.FACEBOOK_APP_ID,
        "redirect_uri":  get_callback_url(request),
        "state":         state,
        "scope":         scopes,
        "response_type": "code",
    }
    url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return url, state


def exchange_code_for_token(request, code: str) -> dict:
    """
    Exchange the authorization code for a short-lived user access token.

    Returns dict:
        {
            "access_token": "...",
            "token_type":   "bearer",
        }

    Raises:
        FacebookOAuthError — on any failure.

    SECURITY: Never log app_secret. Never log access_token.
    """
    resp = requests.get(
        _TOKEN_URL,
        params={
            "client_id":     settings.FACEBOOK_APP_ID,
            "client_secret": settings.FACEBOOK_APP_SECRET,
            "redirect_uri":  get_callback_url(request),
            "code":          code,
        },
        timeout=15,
    )

    if not resp.ok:
        raise FacebookOAuthError(
            f"Token exchange failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "access_token" not in data:
        error_msg = data.get("error", {}).get("message", "Missing access_token")
        raise FacebookOAuthError(f"Token response error: {error_msg}")

    return data


def exchange_for_long_lived_token(short_lived_token: str) -> dict:
    """
    Exchange a short-lived user access token for a long-lived one (~60 days).

    Facebook Graph API: GET /oauth/access_token
        grant_type=fb_exchange_token
        client_id=...
        client_secret=...
        fb_exchange_token=<short-lived-token>

    Returns dict:
        {
            "access_token": "...",
            "token_type":   "bearer",
            "expires_in":   5183944,   # seconds (~60 days)
        }

    Raises:
        FacebookOAuthError — on any failure.

    SECURITY: Never log app_secret or access_token.
    """
    resp = requests.get(
        _TOKEN_URL,
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         settings.FACEBOOK_APP_ID,
            "client_secret":     settings.FACEBOOK_APP_SECRET,
            "fb_exchange_token": short_lived_token,
        },
        timeout=15,
    )

    if not resp.ok:
        raise FacebookOAuthError(
            f"Long-lived token exchange failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "access_token" not in data:
        error_msg = data.get("error", {}).get("message", "Missing access_token")
        raise FacebookOAuthError(f"Long-lived token error: {error_msg}")

    return data


def fetch_facebook_profile(user_access_token: str) -> dict:
    """
    Fetch the authenticated user's basic profile.

    Returns dict:
        {
            "id":   "123456789",   # Facebook user ID
            "name": "John Doe",
        }

    Raises:
        FacebookOAuthError — if the request fails.

    SECURITY: Never log access_token.
    """
    resp = requests.get(
        _ME_URL,
        params={
            "fields":       "id,name",
            "access_token": user_access_token,
        },
        timeout=15,
    )

    if not resp.ok:
        raise FacebookOAuthError(
            f"Profile fetch failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "id" not in data:
        error_msg = data.get("error", {}).get("message", "Missing id field")
        raise FacebookOAuthError(f"Profile response error: {error_msg}")

    return data


def fetch_facebook_pages(long_lived_user_token: str) -> list[dict]:
    """
    Fetch the list of Facebook Pages that the authenticated user manages.

    Each page entry contains:
        {
            "id":           "123456789",       # Page ID
            "name":         "My Business Page",
            "access_token": "...",             # Page access token (use for publishing)
            "category":     "Software",
            "tasks":        ["CREATE_CONTENT", "MANAGE", ...],
        }

    Returns an empty list if the user has no managed pages.

    Raises:
        FacebookOAuthError — if the API call fails.

    SECURITY: Page access tokens are present in the response — never log this.
    """
    resp = requests.get(
        _PAGES_URL,
        params={
            "fields":       "id,name,access_token,category,tasks",
            "access_token": long_lived_user_token,
        },
        timeout=15,
    )

    if not resp.ok:
        raise FacebookOAuthError(
            f"Pages fetch failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error")
        raise FacebookOAuthError(f"Pages API error: {error_msg}")

    return data.get("data", [])


# ── Exception ─────────────────────────────────────────────────────────────────

class FacebookOAuthError(Exception):
    """Raised when any step of the Facebook OAuth flow fails."""
    pass
