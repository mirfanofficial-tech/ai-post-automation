"""
LinkedIn Page OAuth 2.0 service.

Handles the full OAuth flow for connecting LinkedIn Organization/Company Pages:
    1.  build_authorization_url()    →  redirect user to LinkedIn
    2.  exchange_code_for_token()    →  swap code for access token
    3.  fetch_linkedin_profile()     →  get the member's own identity (for labeling)
    4.  fetch_linkedin_pages()       →  list organization pages the member admins

This uses a SEPARATE LinkedIn Developer App from the personal profile app.
Scopes required (add these Products in your LinkedIn Developer App):
    r_organization_social   — read organization posts (Marketing Developer Platform)
    w_organization_social   — create posts on organization pages
    r_basicprofile          — basic identity (to label the connection)

LinkedIn App setup checklist:
    1. Go to https://www.linkedin.com/developers/apps and create a NEW app
    2. Products tab → Request "Marketing Developer Platform"
       (this grants r_organization_social + w_organization_social)
    3. Auth tab → Authorized redirect URLs — add EXACTLY:
         Local dev:  http://127.0.0.1:8000/posts/social-accounts/linkedin-page/callback/
         Production: https://yourdomain.com/posts/social-accounts/linkedin-page/callback/

Organization API flow:
    After getting the access token, call GET /v2/organizationAcls?q=roleAssignee
    to find all org pages where the member has ADMINISTRATOR role.
    Each org has a stable `organization` URN like urn:li:organization:12345.

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

_AUTH_URL          = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL         = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL      = "https://api.linkedin.com/v2/userinfo"
_ORG_ACLS_URL      = "https://api.linkedin.com/v2/organizationAcls"
_ORG_URL           = "https://api.linkedin.com/v2/organizations"


# ── Callback URL ──────────────────────────────────────────────────────────────

def get_callback_url(request) -> str:
    """
    Return the absolute OAuth callback URL for LinkedIn Page.

    Priority:
        1. LINKEDIN_PAGE_REDIRECT_URI in settings/.env  → use as-is
        2. request.build_absolute_uri()                 → auto-built for local dev

    Local dev:   http://127.0.0.1:8000/posts/social-accounts/linkedin-page/callback/
    Production:  https://yourdomain.com/posts/social-accounts/linkedin-page/callback/
    """
    configured = getattr(settings, "LINKEDIN_PAGE_REDIRECT_URI", "").strip()
    if configured:
        return configured
    path = reverse("posts:linkedin_page_callback")
    return request.build_absolute_uri(path)


# ── State expiry ──────────────────────────────────────────────────────────────

def is_state_expired(oauth_state) -> bool:
    """Return True if an OAuthState record is older than OAUTH_STATE_EXPIRY_MINUTES."""
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    return oauth_state.created_at < cutoff


def cleanup_expired_states():
    """Delete all expired OAuthState records for linkedin_page."""
    from posts.models import OAuthState
    expiry_minutes = getattr(settings, "OAUTH_STATE_EXPIRY_MINUTES", 10)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)
    deleted, _ = OAuthState.objects.filter(
        created_at__lt=cutoff,
        platform__code="linkedin_page",
    ).delete()
    return deleted


# ── Public OAuth API ──────────────────────────────────────────────────────────

def build_authorization_url(request) -> tuple[str, str]:
    """
    Build the LinkedIn authorization URL for Page OAuth.

    Returns:
        (authorization_url, state)

    The caller MUST store `state` in OAuthState before redirecting.
    """
    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id":     settings.LINKEDIN_PAGE_CLIENT_ID,
        "redirect_uri":  get_callback_url(request),
        "state":         state,
        "scope":         settings.LINKEDIN_PAGE_SCOPES,
    }
    url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return url, state


def exchange_code_for_token(request, code: str) -> dict:
    """
    Exchange the authorization code for an access token.

    Returns dict:
        {
            "access_token": "...",
            "expires_in":   5183944,
            "token_type":   "Bearer",
            "scope":        "r_organization_social w_organization_social ...",
        }

    Raises:
        LinkedInPageOAuthError — on any failure.
    """
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  get_callback_url(request),
            "client_id":     settings.LINKEDIN_PAGE_CLIENT_ID,
            "client_secret": settings.LINKEDIN_PAGE_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )

    if not resp.ok:
        raise LinkedInPageOAuthError(
            f"Token exchange failed: HTTP {resp.status_code}"
        )

    data = resp.json()
    if "access_token" not in data:
        raise LinkedInPageOAuthError("Token response missing access_token.")

    return data


def fetch_member_profile(access_token: str) -> dict:
    """
    Fetch the authenticated member's basic profile via OIDC userinfo.
    Used only to get the member's name for labeling purposes.

    Returns dict with at least: sub, name (or given_name).

    Raises:
        LinkedInPageOAuthError — if the request fails.
    """
    resp = requests.get(
        _USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )

    if not resp.ok:
        # Non-fatal — we'll just use a generic label if this fails
        return {}

    data = resp.json()
    return data


def fetch_linkedin_pages(access_token: str) -> list[dict]:
    """
    Fetch all LinkedIn Organization Pages the authenticated member administers.

    Uses GET /v2/organizationAcls?q=roleAssignee&role=ADMINISTRATOR
    to find org URNs, then GET /v2/organizations/{id} for each to get the name.

    Returns a list of dicts:
        [
            {
                "org_id":   "12345678",        # numeric org ID
                "org_urn":  "urn:li:organization:12345678",
                "name":     "My Company Page",
            },
            ...
        ]

    Returns empty list if the member admins no pages or permissions are missing.

    Raises:
        LinkedInPageOAuthError — if the API call itself fails.
    """
    # Step 1: get organization ACLs for this member (ADMINISTRATOR role only)
    resp = requests.get(
        _ORG_ACLS_URL,
        params={
            "q":             "roleAssignee",
            "role":          "ADMINISTRATOR",
            "state":         "APPROVED",
            "projection":    "(elements*(organization~(id,localizedName),role,state))",
        },
        headers={
            "Authorization":             f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        timeout=15,
    )

    if not resp.ok:
        raise LinkedInPageOAuthError(
            f"Organization ACLs fetch failed: HTTP {resp.status_code} — {resp.text[:200]}"
        )

    data     = resp.json()
    elements = data.get("elements", [])

    pages = []
    for element in elements:
        org_data = element.get("organization~", {})
        org_urn  = element.get("organization", "")

        # Extract numeric ID from URN like "urn:li:organization:12345678"
        org_id = org_urn.split(":")[-1] if org_urn else ""
        name   = org_data.get("localizedName", "") or f"LinkedIn Page ({org_id})"

        if org_id:
            pages.append({
                "org_id":  org_id,
                "org_urn": org_urn,
                "name":    name,
            })

    return pages


# ── Exception ─────────────────────────────────────────────────────────────────

class LinkedInPageOAuthError(Exception):
    """Raised when any step of the LinkedIn Page OAuth flow fails."""
    pass
