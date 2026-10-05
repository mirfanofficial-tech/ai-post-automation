"""
Facebook publishing adapter — Graph API v19.0.

Implements BasePublisher for the Facebook platform.
Publishes to Facebook Pages using the Page access token stored during OAuth.

Supported post types (driven by platform_capabilities.py):
    • Text-only
    • Single photo  (featured image)
    • Single video  (featured video)
    • Photo carousel (up to 10 images via batch upload)

Facebook Graph API docs:
    https://developers.facebook.com/docs/graph-api/reference/page/feed/
    https://developers.facebook.com/docs/graph-api/reference/page/photos/
    https://developers.facebook.com/docs/graph-api/reference/video/

SECURITY:
    • Never log access_token values.
    • Sanitize all API responses before storing in PublishingLog.
    • Credentials are accessed only here — never in views or templates.
"""

import logging

import requests

from posts.models import PostTarget

from .base_publisher import BasePublisher, PublisherError
from .platform_capabilities import get_caps, get_skipped_media, pick_featured_media
from .result import PublishResult

log = logging.getLogger(__name__)

# ── Facebook Graph API endpoints ──────────────────────────────────────────────
_GRAPH_BASE = "https://graph.facebook.com/v19.0"

# Load capabilities once at import time
_CAPS = get_caps("facebook")


class FacebookPublisher(BasePublisher):
    """
    Publishing adapter for Facebook Pages.

    Uses the Page access token stored during OAuth to publish on behalf
    of the connected Page. The external_account_id on SocialAccount is
    the Facebook Page ID.

    Capabilities (from platform_capabilities.py):
        supports_featured_image  True  — single photo post
        supports_featured_video  True  — single video post
        max_extra_images         9     — up to 10 images in a carousel
        max_documents            0     — documents not supported
        supports_delete          True  — DELETE /<post_id>
    """

    platform_code = "facebook"

    MAX_ATTEMPTS = _CAPS["max_retries"]
    RETRY_DELAYS = _CAPS["retry_delays"]

    # ═════════════════════════════════════════════════════════════════════════
    # TOOL: validate_post_target
    # ═════════════════════════════════════════════════════════════════════════

    def validate(self, target: PostTarget) -> PublishResult:
        """Check content meets Facebook's requirements."""
        if not target.social_account.is_active:
            return PublishResult.fail(
                "Social account is inactive. Please reconnect.",
                retryable=False,
                response_type="ACCOUNT_INACTIVE",
            )

        if not target.social_account.external_account_id:
            return PublishResult.fail(
                "No Facebook Page ID stored for this account. Please reconnect.",
                retryable=False,
                response_type="NO_PAGE_ID",
            )

        try:
            self.get_credential(target)
        except PublisherError as e:
            return PublishResult.fail(str(e), retryable=False,
                                      response_type="AUTH_ERROR")

        full_text = self.build_full_text(target)
        max_len   = _CAPS["max_text_length"]
        if len(full_text) > max_len:
            return PublishResult.fail(
                f"Content too long for Facebook ({len(full_text)}/{max_len} characters).",
                retryable=False,
                response_type="CONTENT_TOO_LONG",
            )

        return PublishResult.ok(message="Validation passed.")

    # ═════════════════════════════════════════════════════════════════════════
    # TOOL: publish_post_target
    # ═════════════════════════════════════════════════════════════════════════

    def publish(self, target: PostTarget) -> PublishResult:
        """
        Publish the post to a Facebook Page.

        Media selection priority (from pick_featured_media):
            1. Featured video  → video post via /videos
            2. Featured image  → photo post via /photos (or carousel if multiple)
            3. Text-only       → link/text post via /feed
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
        page_id      = target.social_account.external_account_id
        full_text    = self.build_full_text(target)
        media_list   = list(target.post.media.order_by("sort_order"))

        # ── Choose media strategy ─────────────────────────────────────────────
        featured, kind = pick_featured_media("facebook", media_list)

        # Log skipped media
        for skipped_media, reason in get_skipped_media("facebook", media_list, featured):
            log.info(
                "PostTarget #%s: skipping %s '%s' — %s",
                target.pk, skipped_media.media_type,
                skipped_media.file.name, reason,
            )

        # ── Video post ────────────────────────────────────────────────────────
        if featured and kind == "video":
            return self._publish_video(page_id, access_token, full_text, featured)

        # ── Photo carousel (multiple images) ─────────────────────────────────
        images = [m for m in media_list if m.media_type == "image"]
        max_carousel = 1 + _CAPS["max_extra_images"]  # featured + extras = 10 total

        if len(images) > 1:
            carousel_images = images[:max_carousel]
            return self._publish_carousel(page_id, access_token, full_text, carousel_images)

        # ── Single photo post ─────────────────────────────────────────────────
        if featured and kind == "image":
            return self._publish_photo(page_id, access_token, full_text, featured)

        # ── Text-only post ────────────────────────────────────────────────────
        return self._publish_text(page_id, access_token, full_text)

    # ═════════════════════════════════════════════════════════════════════════
    # TOOL: delete_from_platform
    # ═════════════════════════════════════════════════════════════════════════

    def delete_from_platform(self, target: PostTarget) -> PublishResult:
        """Delete a published post from a Facebook Page."""
        if not target.external_post_id:
            return PublishResult.fail(
                "No external post ID — cannot delete from Facebook.",
                retryable=False,
                response_type="NO_EXTERNAL_ID",
            )

        try:
            cred = self.get_credential(target)
        except PublisherError as e:
            return PublishResult.fail(str(e), retryable=False,
                                      response_type="AUTH_ERROR")

        try:
            resp = requests.delete(
                f"{_GRAPH_BASE}/{target.external_post_id}",
                params={"access_token": cred.access_token},
                timeout=15,
            )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to Facebook timed out.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )

        if resp.status_code in {200, 204}:
            try:
                if resp.json().get("success"):
                    return PublishResult.ok(message="Post deleted from Facebook.")
            except Exception:
                pass
            return PublishResult.ok(message=f"Post deleted from Facebook (HTTP {resp.status_code}).")

        return PublishResult.fail(
            message       = self._sanitize_error(resp),
            retryable     = resp.status_code in {429, 500, 502, 503, 504},
            response_type = f"HTTP_{resp.status_code}",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # classify_error
    # ═════════════════════════════════════════════════════════════════════════

    def classify_error(self, status_code: int, body: str) -> bool:
        """
        Retryable: rate limits, server errors, temporary outages.
        Non-retryable: auth errors, bad content, permission denied.
        """
        return status_code in {429, 500, 502, 503, 504}

    # ═════════════════════════════════════════════════════════════════════════
    # Private publishing helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _publish_text(self, page_id: str, access_token: str,
                      message: str) -> PublishResult:
        """Publish a text-only post to the Page feed."""
        try:
            resp = requests.post(
                f"{_GRAPH_BASE}/{page_id}/feed",
                data={
                    "message":      message,
                    "access_token": access_token,
                },
                timeout=20,
            )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to Facebook timed out.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )

        return self._handle_feed_response(resp, page_id)

    def _publish_photo(self, page_id: str, access_token: str,
                       message: str, media) -> PublishResult:
        """Publish a single photo post to the Page."""
        try:
            with media.file.open("rb") as f:
                resp = requests.post(
                    f"{_GRAPH_BASE}/{page_id}/photos",
                    data={
                        "caption":      message,
                        "access_token": access_token,
                    },
                    files={"source": (media.file.name, f, media.mime_type or "image/jpeg")},
                    timeout=60,
                )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to Facebook timed out during photo upload.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )
        except Exception as exc:
            return PublishResult.fail(
                f"Failed to read media file: {exc}",
                retryable=False, response_type="FILE_ERROR",
            )

        if resp.status_code == 200:
            try:
                data    = resp.json()
                post_id = data.get("post_id") or data.get("id", "")
                post_url = f"https://www.facebook.com/{post_id}" if post_id else ""
                return PublishResult.ok(
                    external_post_id  = post_id,
                    external_post_url = post_url,
                    message           = "Published photo successfully to Facebook Page.",
                )
            except Exception:
                pass

        retryable = self.classify_error(resp.status_code, resp.text)
        return PublishResult.fail(
            message       = self._sanitize_error(resp),
            retryable     = retryable,
            response_type = f"HTTP_{resp.status_code}",
        )

    def _publish_carousel(self, page_id: str, access_token: str,
                          message: str, images: list) -> PublishResult:
        """
        Publish a multi-photo carousel to the Page.

        Facebook carousel flow:
            1. Upload each image unpublished → get photo IDs
            2. Create a feed post attaching all photo IDs
        """
        photo_ids = []

        for media in images:
            try:
                with media.file.open("rb") as f:
                    resp = requests.post(
                        f"{_GRAPH_BASE}/{page_id}/photos",
                        data={
                            "published":    "false",   # upload only, don't publish yet
                            "access_token": access_token,
                        },
                        files={"source": (media.file.name, f, media.mime_type or "image/jpeg")},
                        timeout=60,
                    )
                if resp.status_code == 200:
                    photo_id = resp.json().get("id")
                    if photo_id:
                        photo_ids.append(photo_id)
                    else:
                        log.warning("Facebook carousel: no id in photo upload response")
                else:
                    log.warning(
                        "Facebook carousel: photo upload failed HTTP %s — %s",
                        resp.status_code, self._sanitize_error(resp),
                    )
            except Exception as exc:
                log.warning("Facebook carousel: photo upload error — %s", exc)

        if not photo_ids:
            # Fall back to text-only if all uploads failed
            log.warning(
                "Facebook carousel: all photo uploads failed — publishing text-only."
            )
            return self._publish_text(page_id, access_token, message)

        # Build attached_media list for the feed post
        attached_media = [{"media_fbid": pid} for pid in photo_ids]

        try:
            resp = requests.post(
                f"{_GRAPH_BASE}/{page_id}/feed",
                json={
                    "message":        message,
                    "attached_media": attached_media,
                    "access_token":   access_token,
                },
                timeout=20,
            )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to Facebook timed out during carousel post.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )

        return self._handle_feed_response(resp, page_id)

    def _publish_video(self, page_id: str, access_token: str,
                       message: str, media) -> PublishResult:
        """Publish a video post to the Page."""
        try:
            with media.file.open("rb") as f:
                resp = requests.post(
                    f"{_GRAPH_BASE}/{page_id}/videos",
                    data={
                        "description":  message,
                        "access_token": access_token,
                    },
                    files={"source": (media.file.name, f, media.mime_type or "video/mp4")},
                    timeout=120,   # videos can be large
                )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to Facebook timed out during video upload.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )
        except Exception as exc:
            return PublishResult.fail(
                f"Failed to read video file: {exc}",
                retryable=False, response_type="FILE_ERROR",
            )

        if resp.status_code == 200:
            try:
                data    = resp.json()
                post_id = data.get("id", "")
                post_url = f"https://www.facebook.com/{post_id}" if post_id else ""
                return PublishResult.ok(
                    external_post_id  = post_id,
                    external_post_url = post_url,
                    message           = "Published video successfully to Facebook Page.",
                )
            except Exception:
                pass

        retryable = self.classify_error(resp.status_code, resp.text)
        return PublishResult.fail(
            message       = self._sanitize_error(resp),
            retryable     = retryable,
            response_type = f"HTTP_{resp.status_code}",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # Shared helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _handle_feed_response(self, resp: requests.Response,
                               page_id: str) -> PublishResult:
        """Parse a /feed POST response into a PublishResult."""
        if resp.status_code == 200:
            try:
                data    = resp.json()
                post_id = data.get("id", "")
                # Facebook returns "page_id_post_id" format — build URL
                post_url = f"https://www.facebook.com/{post_id}" if post_id else ""
                return PublishResult.ok(
                    external_post_id  = post_id,
                    external_post_url = post_url,
                    message           = "Published successfully to Facebook Page.",
                )
            except Exception:
                return PublishResult.ok(
                    message="Published successfully to Facebook Page."
                )

        retryable = self.classify_error(resp.status_code, resp.text)
        return PublishResult.fail(
            message       = self._sanitize_error(resp),
            retryable     = retryable,
            response_type = f"HTTP_{resp.status_code}",
        )

    def _sanitize_error(self, resp: requests.Response) -> str:
        """Extract a safe error message from a Facebook Graph API error response."""
        try:
            data  = resp.json()
            error = data.get("error", {})
            msg   = error.get("message", f"HTTP {resp.status_code}")
            code  = error.get("code", "")
            subcode = error.get("error_subcode", "")
            if subcode:
                return f"{msg} (code {code}/{subcode})"
            if code:
                return f"{msg} (code {code})"
            return msg
        except Exception:
            return f"HTTP {resp.status_code}"
