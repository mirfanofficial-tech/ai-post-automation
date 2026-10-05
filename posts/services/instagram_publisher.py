"""
Instagram publishing adapter — Content Publishing API v19.0.

Publishes to Instagram Business/Creator accounts via the Graph API.

Instagram Content Publishing flow (two-step):
    1. Create a media container:   POST /{ig_account_id}/media
    2. Publish the container:      POST /{ig_account_id}/media_publish

Supported post types:
    • Single image   — IMAGE container
    • Single video   — REELS container (recommended for video)
    • Carousel       — up to 10 images/videos via CAROUSEL_ALBUM

Important limitations:
    • Text-only posts are NOT supported — media is required
    • Images must be publicly accessible URLs OR uploaded as bytes
      (we use the Graph API's image_url parameter with a CDN URL,
       or fall back to skipping media if no public URL is available)
    • Captions (text) are optional but supported

Note on media URLs:
    Instagram's API requires publicly accessible image/video URLs.
    For local dev, images stored on 127.0.0.1 won't work — they must be
    on a publicly reachable URL (e.g. via ngrok or a CDN).
    We detect this and fall back gracefully with a clear error message.

Graph API docs:
    https://developers.facebook.com/docs/instagram-api/reference/ig-user/media
    https://developers.facebook.com/docs/instagram-api/reference/ig-user/media_publish

SECURITY:
    • Never log access_token values.
    • Credentials accessed only here.
"""

import logging

import requests
from django.conf import settings

from posts.models import PostTarget

from .base_publisher import BasePublisher, PublisherError
from .platform_capabilities import get_caps, pick_featured_media, get_skipped_media
from .result import PublishResult

log = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v19.0"
_CAPS       = get_caps("instagram")


class InstagramPublisher(BasePublisher):
    """
    Publishing adapter for Instagram Business/Creator accounts.

    The external_account_id on SocialAccount stores the Instagram Account ID
    (e.g. "17841400000000000"), NOT the Facebook Page ID.
    The credential access_token is the Page access token retrieved during OAuth.

    Media note: Instagram requires publicly accessible media URLs.
    For production, media should be served from a CDN or public storage.
    For local dev via ngrok, media URLs must use the ngrok domain.
    """

    platform_code = "instagram"

    MAX_ATTEMPTS = _CAPS["max_retries"]
    RETRY_DELAYS = _CAPS["retry_delays"]

    # ═════════════════════════════════════════════════════════════════════════
    # validate
    # ═════════════════════════════════════════════════════════════════════════

    def validate(self, target: PostTarget) -> PublishResult:
        if not target.social_account.is_active:
            return PublishResult.fail(
                "Social account is inactive. Please reconnect.",
                retryable=False, response_type="ACCOUNT_INACTIVE",
            )

        if not target.social_account.external_account_id:
            return PublishResult.fail(
                "No Instagram Account ID stored. Please reconnect.",
                retryable=False, response_type="NO_ACCOUNT_ID",
            )

        try:
            self.get_credential(target)
        except PublisherError as e:
            return PublishResult.fail(str(e), retryable=False,
                                      response_type="AUTH_ERROR")

        # Instagram requires media — text-only not supported
        media_list = list(target.post.media.order_by("sort_order"))
        if not media_list:
            return PublishResult.fail(
                "Instagram requires at least one image or video. "
                "Please attach media to this post.",
                retryable=False, response_type="MEDIA_REQUIRED",
            )

        full_text = self.build_full_text(target)
        max_len   = _CAPS["max_text_length"]
        if len(full_text) > max_len:
            return PublishResult.fail(
                f"Caption too long for Instagram ({len(full_text)}/{max_len} chars).",
                retryable=False, response_type="CONTENT_TOO_LONG",
            )

        return PublishResult.ok(message="Validation passed.")

    # ═════════════════════════════════════════════════════════════════════════
    # publish
    # ═════════════════════════════════════════════════════════════════════════

    def publish(self, target: PostTarget) -> PublishResult:
        """
        Publish to Instagram using the two-step container + publish flow.

        Media URL strategy:
            Instagram requires publicly accessible URLs for media.
            We build the URL using the request's MEDIA_URL + the file path,
            combined with a configured PUBLIC_BASE_URL setting (or ngrok URL).
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
        ig_id        = target.social_account.external_account_id
        caption      = self.build_full_text(target)
        media_list   = list(target.post.media.order_by("sort_order"))

        # Build public base URL for media files
        public_base = self._get_public_base_url()
        if not public_base:
            return PublishResult.fail(
                "INSTAGRAM_PUBLIC_BASE_URL is not configured. "
                "Instagram requires publicly accessible media URLs. "
                "Set INSTAGRAM_PUBLIC_BASE_URL in .env (e.g. your ngrok URL).",
                retryable=False, response_type="CONFIG_ERROR",
            )

        featured, kind = pick_featured_media("instagram", media_list)

        for skipped, reason in get_skipped_media("instagram", media_list, featured):
            log.info("PostTarget #%s: skipping %s — %s",
                     target.pk, skipped.file.name, reason)

        images = [m for m in media_list if m.media_type == "image"]
        videos = [m for m in media_list if m.media_type == "video"]

        # ── Carousel (multiple images) ─────────────────────────────────────
        max_carousel = 1 + _CAPS["max_extra_images"]  # 10 total
        if len(images) > 1:
            carousel_media = images[:max_carousel]
            return self._publish_carousel(ig_id, access_token, caption,
                                          carousel_media, public_base)

        # ── Single video (Reels) ───────────────────────────────────────────
        if featured and kind == "video":
            return self._publish_video(ig_id, access_token, caption,
                                       featured, public_base)

        # ── Single image ───────────────────────────────────────────────────
        if featured and kind == "image":
            return self._publish_image(ig_id, access_token, caption,
                                       featured, public_base)

        return PublishResult.fail(
            "No suitable media found for Instagram post.",
            retryable=False, response_type="NO_MEDIA",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # delete_from_platform — Instagram does not support delete via API
    # ═════════════════════════════════════════════════════════════════════════

    def delete_from_platform(self, target: PostTarget) -> PublishResult:
        return PublishResult.fail(
            "Instagram does not support deleting posts via API.",
            retryable=False, response_type="NOT_SUPPORTED",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # classify_error
    # ═════════════════════════════════════════════════════════════════════════

    def classify_error(self, status_code: int, body: str) -> bool:
        return status_code in {429, 500, 502, 503, 504}

    # ═════════════════════════════════════════════════════════════════════════
    # Private helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _get_public_base_url(self) -> str:
        """
        Return the public base URL used to construct media URLs for Instagram.
        Must be publicly accessible — not 127.0.0.1.

        Configure via INSTAGRAM_PUBLIC_BASE_URL in .env.
        Example: https://follow-macaroni-handiwork.ngrok-free.dev
        """
        url = getattr(settings, "INSTAGRAM_PUBLIC_BASE_URL", "").strip().rstrip("/")
        return url

    def _media_url(self, media, public_base: str) -> str:
        """Build the publicly accessible URL for a media file."""
        media_url = settings.MEDIA_URL.rstrip("/")
        # media.file.name is relative to MEDIA_ROOT, e.g. "media/posts/1/img.jpg"
        return f"{public_base}{media_url}/{media.file.name}"

    def _publish_image(self, ig_id: str, access_token: str, caption: str,
                       media, public_base: str) -> PublishResult:
        """Single image post via IMAGE container."""
        image_url = self._media_url(media, public_base)
        log.info("Instagram image URL: %s", image_url)

        container_id, error = self._create_container(ig_id, access_token, {
            "image_url": image_url,
            "caption":   caption,
        })
        if container_id is None:
            return PublishResult.fail(
                f"Instagram container error: {error}",
                retryable=True, response_type="CONTAINER_ERROR",
            )

        return self._publish_container(ig_id, access_token, container_id, "image")

    def _publish_video(self, ig_id: str, access_token: str, caption: str,
                       media, public_base: str) -> PublishResult:
        """Single video post as Reels."""
        video_url = self._media_url(media, public_base)

        container_id, error = self._create_container(ig_id, access_token, {
            "media_type": "REELS",
            "video_url":  video_url,
            "caption":    caption,
        })
        if container_id is None:
            return PublishResult.fail(
                f"Instagram video container error: {error}",
                retryable=True, response_type="CONTAINER_ERROR",
            )

        return self._publish_container(ig_id, access_token, container_id, "video")

    def _publish_carousel(self, ig_id: str, access_token: str, caption: str,
                           images: list, public_base: str) -> PublishResult:
        """
        Carousel post — up to 10 images.

        Flow:
            1. Create individual IMAGE containers for each image (no caption)
            2. Create CAROUSEL_ALBUM container referencing all image container IDs
            3. Publish the carousel container
        """
        child_ids = []

        for media in images:
            image_url = self._media_url(media, public_base)
            child_id, error = self._create_container(ig_id, access_token, {
                "image_url":        image_url,
                "is_carousel_item": "true",
            })
            if child_id:
                child_ids.append(child_id)
            else:
                log.warning("Instagram carousel: child container failed for %s — %s",
                            media.file.name, error)

        if not child_ids:
            return PublishResult.fail(
                "Instagram carousel: all child containers failed to create.",
                retryable=True, response_type="CONTAINER_ERROR",
            )

        carousel_id, error = self._create_container(ig_id, access_token, {
            "media_type": "CAROUSEL_ALBUM",
            "children":   ",".join(child_ids),
            "caption":    caption,
        })
        if carousel_id is None:
            return PublishResult.fail(
                f"Instagram carousel container error: {error}",
                retryable=True, response_type="CONTAINER_ERROR",
            )

        return self._publish_container(ig_id, access_token, carousel_id, "carousel")

    def _create_container(self, ig_id: str, access_token: str,
                           params: dict) -> tuple[str | None, str]:
        """
        POST /{ig_id}/media — create a media container.
        Returns (container_id, error_message).
        container_id is None on failure, error_message is empty on success.
        """
        try:
            data = {**params, "access_token": access_token}
            resp = requests.post(
                f"{_GRAPH_BASE}/{ig_id}/media",
                data=data,
                timeout=30,
            )
            if resp.status_code == 200:
                container_id = resp.json().get("id")
                if container_id:
                    return container_id, ""
                return None, "API returned 200 but no container ID"
            error_msg = self._sanitize_error(resp)
            log.warning(
                "Instagram create container failed HTTP %s ig_id=%s: %s",
                resp.status_code, ig_id, error_msg,
            )
            return None, error_msg
        except Exception as exc:
            log.warning("Instagram create container exception: %s", exc)
            return None, str(exc)

    def _publish_container(self, ig_id: str, access_token: str,
                            container_id: str, kind: str) -> PublishResult:
        """
        POST /{ig_id}/media_publish — publish a media container.
        Returns PublishResult.
        """
        try:
            resp = requests.post(
                f"{_GRAPH_BASE}/{ig_id}/media_publish",
                data={
                    "creation_id":  container_id,
                    "access_token": access_token,
                },
                timeout=30,
            )
        except requests.Timeout:
            return PublishResult.fail(
                "Request to Instagram timed out.",
                retryable=True, response_type="TIMEOUT",
            )
        except requests.RequestException as exc:
            return PublishResult.fail(
                f"Network error: {type(exc).__name__}",
                retryable=True, response_type="NETWORK_ERROR",
            )

        if resp.status_code == 200:
            post_id  = resp.json().get("id", "")
            post_url = f"https://www.instagram.com/p/{post_id}/" if post_id else ""
            return PublishResult.ok(
                external_post_id  = post_id,
                external_post_url = post_url,
                message           = f"Published {kind} successfully to Instagram.",
            )

        retryable = self.classify_error(resp.status_code, resp.text)
        return PublishResult.fail(
            message       = self._sanitize_error(resp),
            retryable     = retryable,
            response_type = f"HTTP_{resp.status_code}",
        )

    def _sanitize_error(self, resp: requests.Response) -> str:
        try:
            data    = resp.json()
            error   = data.get("error", {})
            msg     = error.get("message", f"HTTP {resp.status_code}")
            code    = error.get("code", "")
            subcode = error.get("error_subcode", "")
            if subcode:
                return f"{msg} (code {code}/{subcode})"
            if code:
                return f"{msg} (code {code})"
            return msg
        except Exception:
            return f"HTTP {resp.status_code}"
