"""
Instagram OAuth service — via Facebook Login for Business.

Instagram professional accounts must be connected to a Facebook Page.
The OAuth flow uses the same Facebook app (same APP_ID/SECRET) but with
Instagram-specific permissions.

Flow:
    1.  build_authorization_url()   →  redirect user to Facebook Login dialog
    2.  exchange_code_for_token()   →  swap code for short-lived user token
    3.  exchange_for_long_lived_token() → extend to ~60 days
    4.  fetch_instagram_accounts()  →  get Instagram Business/Creator accounts
                                       linked to the user's Facebook Pages

Required permissions (add in Meta App → Permissions and Features):
    instagram_basic
    instagram_content_publishing
    pages_read_engagement
    business_management
    pages_show_list

Important notes:
    • This uses the SAME Facebook app (FACEBOOK_APP_ID / FACEBOOK_APP_SECRET)
    • The Instagram account must be a Professional account (Business or Creator)
    • The Instagram account must be linked to a Facebook Page
    • We store the Instagram Account ID (not the Facebook Page ID) as external_account_id
    • Publishing uses POST /{ig_account_id}/media + /{ig_account_id}/media_publish

SECURITY:
    • Never log access_token or app_secret values.
    • State parameter prevents CSRF.
    • Tokens stored via SocialAccountCredential — not in session or logs.
"""

import secrets
import urllib.parse

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone


# ── Facebook/Instagram Graph API endpoints ────────────────────────────────────

_AUTH_URL   = "https://www.facebook.com/v19.0/dialog/oauth"
_TOKEN_URL  = "https://graph.facebook.com/v19.0/oauth/access_token"
_PAGES_URL  = "https://graph.facebook.com/v19.0/me/accounts"


# ── Callback URL ──────────────────────────────────────────────────────────────

def get_callback_url(request) -> str:
    """
    Return the absolute OAuth callback URL for Instagram.

    Priority:
        1. INSTAGRAM_REDIRECT_URI in settings/.env  → use as-is
        2. request.build_absolute_uri()             → auto-built for local dev

    Local dev:   http://127.0.0.1:8000/posts/social-accounts/instagram/callback/
    Production:  https://yourdomain.com/posts/social-accounts/instagram/callback/
    """
    configured = getattr(settings, "INSTAGRAM_REDIRECT_URI", "").strip()
    if configured:
        return configured
    path = reverse("posts:instagram_callback")
    return request.build_absolute_uri(path)


# ── State helpers ─────────────────────────────────────────────────────────────

def is_state_expired(oauth_state) -> bool:
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    return oauth_state.created_at < cutoff


def cleanup_expired_states():
    from posts.models import OAuthState
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    deleted, _ = OAuthState.objects.filter(
        created_at__lt=cutoff,
        platform__code="instagram",
    ).delete()
    return deleted


# ── Public OAuth API ──────────────────────────────────────────────────────────

def build_authorization_url(request) -> tuple[str, str]:
    """
    Build the Facebook Login authorization URL for Instagram permissions.

    Uses the same FACEBOOK_APP_ID but requests Instagram-specific scopes.
    Returns (authorization_url, state).
    """
    state = secrets.token_urlsafe(32)

    scopes = getattr(
        settings,
        "INSTAGRAM_SCOPES",
        "instagram_basic,instagram_content_publish,pages_read_engagement,business_management,pages_show_list",
    )

    params = {
        "client_id":     settings.INSTAGRAM_APP_ID,
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

    Returns dict with at least: access_token, token_type.
    Raises InstagramOAuthError on failure.
    """
    resp = requests.get(
        _TOKEN_URL,
        params={
            "client_id":     settings.INSTAGRAM_APP_ID,
            "client_secret": settings.INSTAGRAM_APP_SECRET,
            "redirect_uri":  get_callback_url(request),
            "code":          code,
        },
        timeout=15,
    )

    if not resp.ok:
        raise InstagramOAuthError(
            f"Token exchange failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "access_token" not in data:
        error_msg = data.get("error", {}).get("message", "Missing access_token")
        raise InstagramOAuthError(f"Token response error: {error_msg}")

    return data


def exchange_for_long_lived_token(short_lived_token: str) -> dict:
    """
    Exchange a short-lived token for a long-lived one (~60 days).
    Raises InstagramOAuthError on failure.
    """
    resp = requests.get(
        _TOKEN_URL,
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         settings.INSTAGRAM_APP_ID,
            "client_secret":     settings.INSTAGRAM_APP_SECRET,
            "fb_exchange_token": short_lived_token,
        },
        timeout=15,
    )

    if not resp.ok:
        raise InstagramOAuthError(
            f"Long-lived token exchange failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "access_token" not in data:
        error_msg = data.get("error", {}).get("message", "Missing access_token")
        raise InstagramOAuthError(f"Long-lived token error: {error_msg}")

    return data


def fetch_instagram_accounts(long_lived_user_token: str) -> list[dict]:
    """
    Fetch Instagram Business/Creator accounts linked to the user's Facebook Pages.

    Flow:
        1. GET /me/accounts → list of Facebook Pages + page tokens
        2. For each page, GET /{page_id}?fields=instagram_business_account
           → get the linked Instagram account ID + username

    Returns list of dicts:
        [
            {
                "ig_account_id": "17841400000000000",
                "username":      "mybusiness",
                "name":          "My Business",
                "page_id":       "123456789",
                "page_name":     "My Facebook Page",
                "page_token":    "...",   # Page token — used for publishing
            },
            ...
        ]

    Returns empty list if no Instagram accounts are linked.
    Raises InstagramOAuthError if the pages fetch itself fails.

    SECURITY: page_token values are present — never log this response.
    """
    # Step 1: get Facebook Pages + page tokens
    pages_resp = requests.get(
        _PAGES_URL,
        params={
            "fields":       "id,name,access_token",
            "access_token": long_lived_user_token,
        },
        timeout=15,
    )

    if not pages_resp.ok:
        raise InstagramOAuthError(
            f"Pages fetch failed: HTTP {pages_resp.status_code}"
        )

    pages_data = pages_resp.json()
    if "error" in pages_data:
        raise InstagramOAuthError(
            f"Pages API error: {pages_data['error'].get('message', 'Unknown')}"
        )

    pages = pages_data.get("data", [])
    ig_accounts = []

    # Step 2: for each page, check for a linked Instagram business account
    for page in pages:
        page_id    = page.get("id", "")
        page_name  = page.get("name", "")
        page_token = page.get("access_token", "")

        if not page_id or not page_token:
            continue

        ig_resp = requests.get(
            f"https://graph.facebook.com/v19.0/{page_id}",
            params={
                "fields":       "instagram_business_account{id,name,username}",
                "access_token": page_token,
            },
            timeout=15,
        )

        if not ig_resp.ok:
            continue

        ig_data  = ig_resp.json()
        ig_acct  = ig_data.get("instagram_business_account")

        if not ig_acct:
            continue

        ig_account_id = ig_acct.get("id", "")
        username      = ig_acct.get("username", "")
        name          = ig_acct.get("name", "") or username or f"Instagram ({ig_account_id})"

        if ig_account_id:
            ig_accounts.append({
                "ig_account_id": ig_account_id,
                "username":      username,
                "name":          name,
                "page_id":       page_id,
                "page_name":     page_name,
                "page_token":    page_token,
            })

    return ig_accounts


# ── Exception ─────────────────────────────────────────────────────────────────

class InstagramOAuthError(Exception):
    """Raised when any step of the Instagram OAuth flow fails."""
    pass
