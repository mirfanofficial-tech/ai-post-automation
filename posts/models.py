"""
Social Media Publishing — Data Models (Phase 1)

Tables
──────
Platform                  – supported platforms (linkedin, x, discord, …)
SocialAccount             – a user's connected account on a platform
SocialAccountCredential   – encrypted OAuth tokens (internal, never exposed)
Post                      – platform-independent content
PostMedia                 – attached images/videos
PostTarget                – "publish this Post to this SocialAccount"
PostTargetContent         – platform-specific content override for a target
PublishingLog             – every individual publishing attempt

PHASE 5-7 ENHANCEMENTS:
  • Encrypted token storage
  • Automatic Post status recalculation via signals
  • Token refresh tracking
  • Idempotency request IDs
"""

import os

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from encrypted_model_fields.fields import EncryptedTextField

User = get_user_model()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _post_media_upload_path(instance, filename):
    """Store media under  media/posts/<post_id>/<filename>."""
    return os.path.join("media", "posts", str(instance.post_id), filename)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Platform
# ──────────────────────────────────────────────────────────────────────────────

class Platform(models.Model):
    """
    A supported publishing platform.

    `code` is the stable machine-readable identifier used in application logic.
    Never use `name` in code — the display name can change, the code must not.

    Example values:
        code='linkedin'  name='LinkedIn'
        code='x'         name='X'
        code='discord'   name='Discord'
    """

    class Status(models.TextChoices):
        ACTIVE   = "active",   "Active"
        INACTIVE = "inactive", "Inactive"

    name   = models.CharField(max_length=100, unique=True)
    code   = models.SlugField(max_length=50, unique=True,
                              help_text="Stable identifier used in code, e.g. 'linkedin'")
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.ACTIVE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering    = ["name"]
        verbose_name        = "Platform"
        verbose_name_plural = "Platforms"

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE


# ──────────────────────────────────────────────────────────────────────────────
# 2. SocialAccount
# ──────────────────────────────────────────────────────────────────────────────

class SocialAccount(models.Model):
    """
    A user's connected account/page/channel on a Platform.

    A single user can connect multiple accounts on the same platform:
        user → LinkedIn "My Personal Profile"
        user → LinkedIn "Company Page"
        user → X "My X Account"
    """

    class Status(models.TextChoices):
        ACTIVE   = "active",   "Active"
        INACTIVE = "inactive", "Inactive"

    user     = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="social_accounts",
    )
    platform = models.ForeignKey(
        Platform,
        on_delete=models.PROTECT,
        related_name="social_accounts",
    )
    account_label        = models.CharField(
        max_length=200,
        help_text="User-friendly label, e.g. 'My Main LinkedIn'",
    )
    external_account_id  = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Platform's own ID for this account/page/channel",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ["platform__name", "account_label"]
        verbose_name        = "Social Account"
        verbose_name_plural = "Social Accounts"
        # Prevent duplicate account entries for the same user+platform+external id
        # Note: uniqueness of (user, platform, external_account_id) is enforced
        # at the application/service layer rather than as a DB constraint,
        # because MySQL does not support conditional unique constraints and
        # external_account_id may be empty before OAuth completes.
        indexes = [
            models.Index(
                fields=["user", "platform"],
                name="idx_soc_acc_user_platform",
            ),
        ]

    def __str__(self):
        return f"{self.account_label} ({self.platform.name})"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE


# ──────────────────────────────────────────────────────────────────────────────
# 3. SocialAccountCredential
# ──────────────────────────────────────────────────────────────────────────────

class SocialAccountCredential(models.Model):
    """
    OAuth credentials for a SocialAccount.

    SECURITY RULES:
    ─────────────
    • Never display tokens in the UI.
    • Never write tokens to application logs.
    • Tokens are encrypted at rest using FIELD_ENCRYPTION_KEY.
    • Access is restricted to the publishing/auth service only.
    • AI tools must never receive raw credentials — pass social_account_id instead.
    
    PHASE 5-7 ENHANCEMENTS:
    • access_token and refresh_token now use EncryptedTextField
    • Added last_refresh_at to track token refresh attempts
    """

    social_account = models.OneToOneField(
        SocialAccount,
        on_delete=models.CASCADE,
        related_name="credential",
    )
    token_type    = models.CharField(max_length=50, default="Bearer")
    access_token  = EncryptedTextField(
        help_text="OAuth access token — encrypted at rest, never expose in UI or logs"
    )
    refresh_token = EncryptedTextField(
        blank=True,
        default="",
        help_text="OAuth refresh token where applicable — encrypted at rest",
    )
    scope      = models.TextField(blank=True, default="",
                                  help_text="Granted OAuth scopes")
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Token expiry — null means no known expiry",
    )
    last_refresh_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last time token was successfully refreshed",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Social Account Credential"
        verbose_name_plural = "Social Account Credentials"

    def __str__(self):
        return f"Credential for {self.social_account}"

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        return timezone.now() >= self.expires_at
    
    def mark_refreshed(self, new_access_token: str, new_expires_at=None, 
                       new_refresh_token: str = None):
        """
        Update credential after successful token refresh.
        
        Call this after exchanging a refresh_token for a new access_token.
        """
        self.access_token = new_access_token
        if new_refresh_token:
            self.refresh_token = new_refresh_token
        if new_expires_at:
            self.expires_at = new_expires_at
        self.last_refresh_at = timezone.now()
        self.save(update_fields=["access_token", "refresh_token", "expires_at", 
                                 "last_refresh_at", "updated_at"])


# ──────────────────────────────────────────────────────────────────────────────
# 4. Post
# ──────────────────────────────────────────────────────────────────────────────

class Post(models.Model):
    """
    Platform-independent content.

    A Post is the common body of text/media that can be published to one or
    more platforms.  It does NOT represent a single-platform publication —
    that is the job of PostTarget.

    Creator design:
    ───────────────
    • Human creator: created_by is set, created_by_type = 'user'
    • AI creator:    created_by is NULL,  created_by_type = 'ai_system'
    Never use a fake user ID (e.g. 0) to represent an AI.
    """

    class CreatorType(models.TextChoices):
        USER      = "user",      "User"
        AI_SYSTEM = "ai_system", "AI System"

    class Status(models.TextChoices):
        DRAFT               = "draft",               "Draft"
        SCHEDULED           = "scheduled",           "Scheduled"
        PUBLISHING          = "publishing",          "Publishing"
        PUBLISHED           = "published",           "Published"
        PARTIALLY_PUBLISHED = "partially_published", "Partially Published"
        FAILED              = "failed",              "Failed"

    title         = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Optional internal title — not necessarily published to the platform",
    )
    content_plain = models.TextField(help_text="Main plain-text content")
    content_html  = models.TextField(
        blank=True,
        default="",
        help_text="Optional HTML representation where the platform supports it",
    )
    hashtags = models.TextField(
        blank=True,
        default="",
        help_text="Common hashtags, e.g. '#AI #Python #Django'",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
        help_text="Human author; NULL for AI-created posts",
    )
    created_by_type = models.CharField(
        max_length=20,
        choices=CreatorType.choices,
        default=CreatorType.USER,
    )

    status = models.CharField(
        max_length=25,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ["-created_at"]
        verbose_name        = "Post"
        verbose_name_plural = "Posts"

    def __str__(self):
        if self.title:
            return self.title
        # Truncate content for display
        snippet = self.content_plain[:60]
        return f"{snippet}…" if len(self.content_plain) > 60 else snippet

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def target_count(self):
        return self.targets.count()

    def recalculate_status(self):
        """
        Derive the Post's overall status from its PostTargets.
        Call this after any PostTarget status change.

        Rules (spec §48):
            No targets               → DRAFT
            All scheduled            → SCHEDULED
            Any publishing/retrying  → PUBLISHING
            All success              → PUBLISHED
            Mix success + failed     → PARTIALLY_PUBLISHED
            All failed               → FAILED
        """
        targets = list(self.targets.all())
        if not targets:
            self.status = self.Status.DRAFT
            self.save(update_fields=["status", "updated_at"])
            return

        statuses = {t.status for t in targets}

        PostTarget = self.targets.model  # avoid circular import at module level

        if statuses <= {PostTarget.Status.SCHEDULED}:
            self.status = self.Status.SCHEDULED
        elif statuses & {PostTarget.Status.PUBLISHING, PostTarget.Status.RETRYING}:
            self.status = self.Status.PUBLISHING
        elif statuses <= {PostTarget.Status.SUCCESS}:
            self.status = self.Status.PUBLISHED
        elif PostTarget.Status.SUCCESS in statuses and statuses & {
            PostTarget.Status.FAILED, PostTarget.Status.CANCELLED
        }:
            self.status = self.Status.PARTIALLY_PUBLISHED
        elif statuses <= {PostTarget.Status.FAILED, PostTarget.Status.CANCELLED}:
            self.status = self.Status.FAILED
        else:
            # Mixed pending/scheduled/success states — stay as publishing
            self.status = self.Status.PUBLISHING

        self.save(update_fields=["status", "updated_at"])


# ──────────────────────────────────────────────────────────────────────────────
# 5. PostMedia
# ──────────────────────────────────────────────────────────────────────────────

class PostMedia(models.Model):
    """
    Media file (image/video/document) attached to a Post.

    Supports images, videos, and documents (PDF, DOCX, etc.).
    Media is platform-independent — the platform adapter decides how to use it.
    """

    class MediaType(models.TextChoices):
        IMAGE    = "image",    "Image"
        VIDEO    = "video",    "Video"
        DOCUMENT = "document", "Document"

    post       = models.ForeignKey(Post, on_delete=models.CASCADE,
                                   related_name="media")
    media_type = models.CharField(max_length=20, choices=MediaType.choices,
                                  default=MediaType.IMAGE)
    mime_type  = models.CharField(max_length=100, blank=True, default="",
                                  help_text="e.g. image/jpeg, application/pdf")
    file       = models.FileField(upload_to=_post_media_upload_path)
    file_size  = models.PositiveIntegerField(default=0,
                                             help_text="File size in bytes")
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Controls display/publishing order; lower = first",
    )
    is_main    = models.BooleanField(
        default=False,
        help_text="Main/thumbnail image for this post",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ["sort_order", "id"]
        verbose_name        = "Post Media"
        verbose_name_plural = "Post Media"

    def __str__(self):
        return f"{self.media_type} for Post #{self.post_id} (sort {self.sort_order})"
    
    @property
    def file_extension(self):
        """Return the file extension (e.g., 'pdf', 'jpg')."""
        if self.file:
            return os.path.splitext(self.file.name)[1][1:].lower()
        return ""
    
    @property
    def is_image(self):
        return self.media_type == self.MediaType.IMAGE
    
    @property
    def is_video(self):
        return self.media_type == self.MediaType.VIDEO
    
    @property
    def is_document(self):
        return self.media_type == self.MediaType.DOCUMENT


# ──────────────────────────────────────────────────────────────────────────────
# 6. PostTarget
# ──────────────────────────────────────────────────────────────────────────────

class PostTarget(models.Model):
    """
    "Publish this Post to this SocialAccount."

    Each PostTarget has its own independent lifecycle:
        - scheduled_at   (can differ from every other target)
        - status
        - published_at
        - external_post_id / external_post_url
        - retry state
        - publishing logs

    One platform failing must NOT affect another platform's result.

    Platform is NOT stored directly here — derive it via:
        post_target.social_account.platform
    This prevents the inconsistency of platform_code != social_account.platform.
    """

    class Status(models.TextChoices):
        PENDING    = "pending",    "Pending"
        SCHEDULED  = "scheduled",  "Scheduled"
        PUBLISHING = "publishing", "Publishing"
        RETRYING   = "retrying",   "Retrying"
        SUCCESS    = "success",    "Success"
        FAILED     = "failed",     "Failed"
        CANCELLED  = "cancelled",  "Cancelled"

    post           = models.ForeignKey(Post, on_delete=models.CASCADE,
                                       related_name="targets")
    social_account = models.ForeignKey(SocialAccount, on_delete=models.PROTECT,
                                       related_name="post_targets")

    status        = models.CharField(max_length=20, choices=Status.choices,
                                     default=Status.PENDING, db_index=True)
    scheduled_at  = models.DateTimeField(
        null=True, blank=True,
        help_text="Target-specific schedule; NULL = publish immediately when triggered",
    )
    published_at  = models.DateTimeField(null=True, blank=True)

    # External platform identifiers — stored for links, audit, future updates
    external_post_id  = models.CharField(max_length=500, blank=True, default="")
    external_post_url = models.URLField(max_length=1000, blank=True, default="")

    # Retry state
    next_retry_at  = models.DateTimeField(null=True, blank=True)
    attempt_count  = models.PositiveSmallIntegerField(default=0)
    last_error     = models.TextField(
        blank=True,
        default="",
        help_text="Most recent sanitized failure reason",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ["created_at"]
        verbose_name        = "Post Target"
        verbose_name_plural = "Post Targets"
        constraints = [
            # Prevent publishing the same Post twice to the same SocialAccount
            models.UniqueConstraint(
                fields=["post", "social_account"],
                name="unique_post_social_account",
            )
        ]

    def __str__(self):
        return (
            f"Post #{self.post_id} → "
            f"{self.social_account.account_label} "
            f"[{self.status}]"
        )

    @property
    def platform(self):
        """Convenience shortcut: PostTarget → SocialAccount → Platform."""
        return self.social_account.platform

    @property
    def is_terminal(self):
        """True if this target has reached a final state (no further action)."""
        return self.status in {self.Status.SUCCESS, self.Status.FAILED,
                               self.Status.CANCELLED}


# ──────────────────────────────────────────────────────────────────────────────
# 7. PostTargetContent
# ──────────────────────────────────────────────────────────────────────────────

class PostTargetContent(models.Model):
    """
    Platform-specific content for a PostTarget.

    Allows overriding the common Post content per platform.
    For example LinkedIn may get a different text or hashtags than Discord.

    The PostTarget already identifies the platform (via social_account.platform),
    so no platform field is needed here.
    """

    post_target   = models.OneToOneField(
        PostTarget,
        on_delete=models.CASCADE,
        related_name="content_override",
    )
    title         = models.CharField(max_length=500, blank=True, default="")
    content_plain = models.TextField(blank=True, default="")
    content_html  = models.TextField(blank=True, default="")
    hashtags      = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Post Target Content"
        verbose_name_plural = "Post Target Contents"

    def __str__(self):
        return f"Content override for {self.post_target}"


# ──────────────────────────────────────────────────────────────────────────────
# 8. PublishingLog
# ──────────────────────────────────────────────────────────────────────────────

class PublishingLog(models.Model):
    """
    A single publishing attempt record.

    Every attempt — successful or failed — is recorded here.
    This provides a complete history for debugging, auditing, and AI reasoning.

    SECURITY: Never store access tokens, refresh tokens, or auth headers here.
    Sanitize platform responses before storage.
    
    PHASE 5-7 ENHANCEMENTS:
    • Added request_id for idempotency tracking
    """

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED  = "failed",  "Failed"

    post_target    = models.ForeignKey(
        PostTarget,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    attempt_number  = models.PositiveSmallIntegerField()
    request_id      = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Unique request identifier for idempotency tracking",
    )
    started_at      = models.DateTimeField()
    ended_at        = models.DateTimeField(null=True, blank=True)
    response_type   = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="e.g. HTTP_200, HTTP_429, TIMEOUT, API_ERROR",
    )
    response_message = models.TextField(
        blank=True,
        default="",
        help_text="Sanitized platform response / error message — never include tokens",
    )
    status = models.CharField(max_length=20, choices=Status.choices)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ["post_target", "attempt_number"]
        verbose_name        = "Publishing Log"
        verbose_name_plural = "Publishing Logs"
        indexes = [
            models.Index(fields=["request_id"], name="idx_pub_log_request_id"),
        ]

    def __str__(self):
        return (
            f"Attempt #{self.attempt_number} for "
            f"PostTarget #{self.post_target_id} — {self.status}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 9. OAuthState  (temporary — one row per in-flight OAuth attempt)
# ──────────────────────────────────────────────────────────────────────────────

class OAuthState(models.Model):
    """
    Temporary record used to validate the `state` parameter on OAuth callbacks.

    One row is created when the user is redirected to LinkedIn.
    It is deleted after the callback is processed (success or failure).

    This prevents CSRF attacks on the OAuth callback endpoint.
    """

    user       = models.ForeignKey(User, on_delete=models.CASCADE,
                                   related_name="oauth_states")
    state      = models.CharField(max_length=128, unique=True, db_index=True)
    platform   = models.ForeignKey(Platform, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "OAuth State"
        verbose_name_plural = "OAuth States"

    def __str__(self):
        return f"OAuthState {self.state[:12]}… ({self.platform.code})"


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNALS — Auto Status Recalculation
# ══════════════════════════════════════════════════════════════════════════════
# Phase 5-7 Enhancement: Automatically recalculate Post status when PostTargets
# are created, updated, or deleted. Prevents stale status issues.

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver(post_save, sender=PostTarget)
def recalculate_post_status_on_target_save(sender, instance, **kwargs):
    """
    Auto-recalculate Post status whenever a PostTarget is saved.
    Uses Post.objects.filter().update() to avoid triggering this signal again.
    """
    try:
        post    = instance.post
        targets = list(post.targets.all())

        if not targets:
            new_status = Post.Status.DRAFT
        else:
            statuses = {t.status for t in targets}
            if statuses <= {PostTarget.Status.SCHEDULED}:
                new_status = Post.Status.SCHEDULED
            elif statuses & {PostTarget.Status.PUBLISHING, PostTarget.Status.RETRYING}:
                new_status = Post.Status.PUBLISHING
            elif statuses <= {PostTarget.Status.SUCCESS}:
                new_status = Post.Status.PUBLISHED
            elif PostTarget.Status.SUCCESS in statuses and statuses & {
                PostTarget.Status.FAILED, PostTarget.Status.CANCELLED
            }:
                new_status = Post.Status.PARTIALLY_PUBLISHED
            elif statuses <= {PostTarget.Status.FAILED, PostTarget.Status.CANCELLED}:
                new_status = Post.Status.FAILED
            else:
                new_status = Post.Status.PUBLISHING

        # update() bypasses post_save signal — no recursion
        Post.objects.filter(pk=post.pk).update(
            status     = new_status,
            updated_at = timezone.now(),
        )

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "Failed to recalculate status for Post #%s: %s", instance.post_id, e
        )


@receiver(post_delete, sender=PostTarget)
def recalculate_post_status_on_target_delete(sender, instance, **kwargs):
    """
    Auto-recalculate Post status when a PostTarget is deleted.
    """
    try:
        if instance.post_id and Post.objects.filter(pk=instance.post_id).exists():
            post    = Post.objects.get(pk=instance.post_id)
            targets = list(post.targets.all())

            if not targets:
                new_status = Post.Status.DRAFT
            else:
                statuses = {t.status for t in targets}
                if statuses <= {PostTarget.Status.SCHEDULED}:
                    new_status = Post.Status.SCHEDULED
                elif statuses & {PostTarget.Status.PUBLISHING, PostTarget.Status.RETRYING}:
                    new_status = Post.Status.PUBLISHING
                elif statuses <= {PostTarget.Status.SUCCESS}:
                    new_status = Post.Status.PUBLISHED
                elif PostTarget.Status.SUCCESS in statuses and statuses & {
                    PostTarget.Status.FAILED, PostTarget.Status.CANCELLED
                }:
                    new_status = Post.Status.PARTIALLY_PUBLISHED
                elif statuses <= {PostTarget.Status.FAILED, PostTarget.Status.CANCELLED}:
                    new_status = Post.Status.FAILED
                else:
                    new_status = Post.Status.PUBLISHING

            Post.objects.filter(pk=post.pk).update(
                status     = new_status,
                updated_at = timezone.now(),
            )

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "Failed to recalculate status after target delete: %s", e
        )
