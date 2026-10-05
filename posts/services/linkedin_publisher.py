"""
LinkedIn publishing adapter — Phase 4.

Implements BasePublisher for the LinkedIn platform.
Uses LinkedIn's UGC Posts API to create text + image posts.

All platform limits and capabilities are read from platform_capabilities.py.
Do NOT hardcode limits here — update platform_capabilities.py instead.

LinkedIn API docs:
    https://learn.microsoft.com/en-us/linkedin/marketing/integrations/community-management/shares/ugc-post-api
    https://learn.microsoft.com/en-us/linkedin/marketing/integrations/community-management/shares/images-api

SECURITY:
    • Never log access_token values.
    • Sanitize all API responses before storing in PublishingLog.
    • Credentials are accessed only here — never in views or templates.
"""

import logging
import urllib.parse

import requests

from posts.models import PostTarget

from .base_publisher import BasePublisher, PublisherError
from .platform_capabilities import get_caps, get_skipped_media, pick_featured_media
from .result import PublishResult

log = logging.getLogger(__name__)

# ── LinkedIn API endpoints ────────────────────────────────────────────────────
_UGC_POSTS_URL   = "https://api.linkedin.com/v2/ugcPosts"
_REGISTER_UPLOAD = "https://api.linkedin.com/v2/assets?action=registerUpload"

# Load capabilities once at import time
_CAPS = get_caps("linkedin")


class LinkedInPublisher(BasePublisher):
    """
    Publishing adapter for LinkedIn.

    Capabilities (from platform_capabilities.py):
        max_images=1          single image only via UGC Posts API
        supports_video=False  requires separate chunked-upload API
        supports_documents=False  requires separate Document Share API
        supports_delete=True  DELETE /v2/ugcPosts/<urn>
    """

    platform_code = "linkedin"

    # Read retry config from capabilities — no hardcoding here
    MAX_ATTEMPTS = _CAPS["max_retries"]
    RETRY_DELAYS = _CAPS["retry_delays"]

    # ═════════════════════════════════════════════════════════════════════════
    # TOOL: validate_post_target
    # ═════════════════════════════════════════════════════════════════════════

    def validate(self, target: PostTarget) -> PublishResult:
        """
        Check that content meets LinkedIn's requirements.
        Limits come from platform_capabilities["linkedin"].
        """
        if not target.social_account.is_active:
            return PublishResult.fail(
                "Social account is inactive. Please reconnect.",
                retryable=False,
                response_type="ACCOUNT_INACTIVE",
            )

        try:
            self.get_credential(target)
        except PublisherError as e:
            return PublishResult.fail(str(e), retryable=False,
                                      response_type="AUTH_ERROR")

        full_text = self.build_full_text(target)
        if not full_text:
            return PublishResult.fail(
                "Post content is empty.",
                retryable=False,
                response_type="EMPTY_CONTENT",
            )

        max_len = _CAPS["max_text_length"]
        if len(full_text) > max_len:
            return PublishResult.fail(
                f"Content too long for LinkedIn ({len(full_text)}/{max_len} characters).",
                retryable=False,
                response_type="CONTENT_TOO_LONG",
            )

        return PublishResult.ok(message="Validation passed.")

    # ═════════════════════════════════════════════════════════════════════════
    # TOOL: publish_post_target
    # ═════════════════════════════════════════════════════════════════════════

    def publish(self, target: PostTarget) -> PublishResult:
        """
        Publish the post to LinkedIn.

        Media selection uses pick_featured_media() from platform_capabilities:
            Priority: featured video > featured image > text-only
            LinkedIn caps: supports_featured_video=False → always image or text-only
        """
        validation = self.validate(target)
        if not validation.success:
            return validation

        try:
            cred = self.get_credential(target)
        except PublisherError as e:
            return PublishResult.fail(str(e), retryable=False,
                                      response_type="AUTH_ERROR")

        access_token = cred.access_token
        member_urn   = f"urn:li:person:{target.social_account.external_account_id}"
        full_text    = self.build_full_text(target)

        # ── Media: priority video > image, driven by capabilities ────────────
        # LinkedIn: supports_featured_video=False, supports_featured_image=True
        # So pick_featured_media will resolve to (image, "image") or (None, None).
        # When LinkedIn video support is added later, update capabilities only.
        media_list      = list(target.post.media.order_by("sort_order"))
        image_asset_urn = None

        if media_list:
            featured, kind = pick_featured_media("linkedin", media_list)

            if featured and kind == "image":
                image_asset_urn = self._upload_image(access_token, member_urn, featured)
                if image_asset_urn is None:
                    log.warning(
                        "Image upload failed for PostTarget #%s — publishing as text-only.",
                        target.pk,
                    )
            elif featured and kind == "video":
                # Future: implement video upload when LinkedIn video API is added
                log.warning(
                    "PostTarget #%s: video upload not yet implemented — text-only.",
                    target.pk,
                )

            for skipped_media, reason in get_skipped_media("linkedin", media_list, featured):
                log.info(
                    "PostTarget #%s: skipping %s '%s' — %s",
                    target.pk, skipped_media.media_type,
                    skipped_media.file.name, reason,
                )

        # ── Build and send UGC post ───────────────────────────────────────────
        payload = self._build_ugc_payload(member_urn, full_text, image_asset_urn)

        try:
            resp = requests.post(
                _UGC_POSTS_URL,
                json    = payload,
                headers = {
                    "Authorization":             f"Bearer {access_token}",
                    "Content-Type":              "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                timeout = 20,
            )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to LinkedIn timed out.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )

        if resp.status_code == 201:
            post_urn = resp.headers.get("x-restli-id", "")
            post_url = f"https://www.linkedin.com/feed/update/{post_urn}/" if post_urn else ""
            return PublishResult.ok(
                external_post_id  = post_urn,
                external_post_url = post_url,
                message           = "Published successfully to LinkedIn.",
            )

        retryable = self.classify_error(resp.status_code, resp.text)
        return PublishResult.fail(
            message       = self._sanitize_response(resp),
            retryable     = retryable,
            response_type = f"HTTP_{resp.status_code}",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # TOOL: delete_from_platform
    # ═════════════════════════════════════════════════════════════════════════

    def delete_from_platform(self, target: PostTarget) -> PublishResult:
        """
        Delete a published post from LinkedIn.
        Only called when capabilities supports_delete=True.
        """
        if not target.external_post_id:
            return PublishResult.fail(
                "No external post ID — cannot delete from LinkedIn.",
                retryable=False, response_type="NO_EXTERNAL_ID",
            )

        try:
            cred = self.get_credential(target)
        except PublisherError as e:
            return PublishResult.fail(str(e), retryable=False,
                                      response_type="AUTH_ERROR")

        encoded_urn = urllib.parse.quote(target.external_post_id, safe="")
        delete_url  = f"https://api.linkedin.com/v2/ugcPosts/{encoded_urn}"

        try:
            resp = requests.delete(
                delete_url,
                headers={
                    "Authorization":             f"Bearer {cred.access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                timeout=15,
            )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to LinkedIn timed out.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )

        if resp.status_code in {200, 204, 404}:
            return PublishResult.ok(
                message=f"Post deleted from LinkedIn (HTTP {resp.status_code})."
            )

        return PublishResult.fail(
            message       = self._sanitize_response(resp),
            retryable     = resp.status_code in {429, 500, 502, 503, 504},
            response_type = f"HTTP_{resp.status_code}",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # classify_error
    # ═════════════════════════════════════════════════════════════════════════

    def classify_error(self, status_code: int, body: str) -> bool:
        return status_code in {429, 500, 502, 503, 504}

    # ═════════════════════════════════════════════════════════════════════════
    # Private helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _upload_image(self, access_token: str, member_urn: str, media) -> str | None:
        try:
            register_payload = {
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                    "owner":   member_urn,
                    "serviceRelationships": [{
                        "relationshipType": "OWNER",
                        "identifier":       "urn:li:userGeneratedContent",
                    }],
                }
            }
            reg_resp = requests.post(
                _REGISTER_UPLOAD,
                json    = register_payload,
                headers = {
                    "Authorization":             f"Bearer {access_token}",
                    "Content-Type":              "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                timeout = 15,
            )
            if not reg_resp.ok:
                log.warning("LinkedIn image register failed: HTTP %s", reg_resp.status_code)
                return None

            reg_data   = reg_resp.json()
            upload_url = reg_data["value"]["uploadMechanism"][
                "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
            ]["uploadUrl"]
            asset_urn  = reg_data["value"]["asset"]

            with media.file.open("rb") as f:
                up_resp = requests.put(
                    upload_url,
                    data    = f,
                    headers = {
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type":  media.mime_type or "image/jpeg",
                    },
                    timeout = 30,
                )
            if up_resp.status_code not in {200, 201}:
                log.warning("LinkedIn image PUT failed: HTTP %s", up_resp.status_code)
                return None

            return asset_urn

        except Exception as exc:
            log.warning("LinkedIn image upload error: %s", exc)
            return None

    def _build_ugc_payload(self, member_urn: str, text: str,
                           image_asset_urn: str | None) -> dict:
        if image_asset_urn:
            share_content = {
                "shareCommentary":    {"text": text},
                "shareMediaCategory": "IMAGE",
                "media": [{
                    "status":      "READY",
                    "description": {"text": ""},
                    "media":       image_asset_urn,
                    "title":       {"text": ""},
                }],
            }
        else:
            share_content = {
                "shareCommentary":    {"text": text},
                "shareMediaCategory": "NONE",
            }

        return {
            "author":          member_urn,
            "lifecycleState":  "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
            "visibility":      {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

    def _sanitize_response(self, resp: requests.Response) -> str:
        try:
            data    = resp.json()
            message = data.get("message") or data.get("error_description") or str(resp.status_code)
            code    = data.get("errorCode") or data.get("error") or ""
            return f"{message} ({code})" if code else message
        except Exception:
            return f"HTTP {resp.status_code}"
