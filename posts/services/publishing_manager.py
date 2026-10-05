"""
PublishingManager — the central tool registry.

This is the ONLY entry point for all publishing actions.
Both the human UI and future AI agents call these tools.
Nothing else should call platform adapters directly.

PHASE 5-7 ENHANCEMENTS:
  • Added request_id generation for idempotency
  • Improved error handling
  • Support for background task integration

Available tools
───────────────
create_post(user, content, title, hashtags, media_paths)
    → dict with post_id

get_post(post_id)
    → dict with post details

list_posts(user, status_filter, limit)
    → list of post dicts

update_post(post_id, content, title, hashtags)
    → dict with updated post

delete_post(post_id)
    → dict with confirmation

list_social_accounts(user)
    → list of safe account dicts (no credentials)

publish_post(post_id, social_account_id)
    → dict with target_id and result

schedule_post(post_id, social_account_id, scheduled_at)
    → dict with target_id

cancel_scheduled_post(target_id)
    → dict with confirmation

get_post_status(post_id)
    → dict with post + all target statuses

get_post_target_status(target_id)
    → dict with target status + latest log

get_publishing_logs(target_id)
    → list of log dicts

retry_post_target(target_id)
    → dict with new result

Architecture (spec §39):
    Human UI ──────┐
                   ├──→  PublishingManager  ──→  Platform Adapter  ──→  API
    AI Tools  ──────┘

Spec §25 — transactions:
    DB operations (create PostTarget, write PublishingLog) are committed
    BEFORE calling the external API. The API call is never inside a
    DB transaction.
"""

import logging
import secrets
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from posts.models import (
    Post,
    PostMedia,
    PostTarget,
    PostTargetContent,
    PublishingLog,
    SocialAccount,
)

from .base_publisher import BasePublisher
from .linkedin_publisher import LinkedInPublisher
from .linkedin_page_publisher import LinkedInPagePublisher
from .facebook_publisher import FacebookPublisher
from .instagram_publisher import InstagramPublisher
from .platform_capabilities import get_caps
from .result import PublishResult

log  = logging.getLogger(__name__)
User = get_user_model()


# ── Platform adapter registry ─────────────────────────────────────────────────
# Add new platforms here as they are built:
#   "x":       XPublisher(),
#   "discord": DiscordPublisher(),

_REGISTRY: dict[str, BasePublisher] = {
    "linkedin":      LinkedInPublisher(),
    "linkedin_page": LinkedInPagePublisher(),
    "facebook":      FacebookPublisher(),
    "instagram":     InstagramPublisher(),
}


def _get_adapter(platform_code: str) -> BasePublisher | None:
    return _REGISTRY.get(platform_code)


# ══════════════════════════════════════════════════════════════════════════════
#  POST TOOLS
# ══════════════════════════════════════════════════════════════════════════════

def create_post(
    user,
    content:  str,
    title:    str = "",
    hashtags: str = "",
) -> dict:
    """
    TOOL: create_post

    Create a new draft Post.

    Parameters:
        user      Django user object or user ID
        content   Main plain-text post content (required)
        title     Optional internal title
        hashtags  Optional hashtags string, e.g. "#AI #Django"

    Returns:
        {
            "ok":      True,
            "post_id": 42,
            "status":  "draft",
            "message": "Post created as draft."
        }

    Raises nothing — errors are returned in the dict as {"ok": False, "error": "..."}.
    """
    if isinstance(user, int):
        try:
            user = User.objects.get(pk=user)
        except User.DoesNotExist:
            return {"ok": False, "error": f"User #{user} not found."}

    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "Post content cannot be empty."}

    post = Post.objects.create(
        content_plain   = content,
        title           = (title or "").strip(),
        hashtags        = (hashtags or "").strip(),
        created_by      = user if not isinstance(user, int) else None,
        created_by_type = Post.CreatorType.USER,
        status          = Post.Status.DRAFT,
    )

    log.info("Tool create_post: created Post #%s for user %s", post.pk, user)
    return {
        "ok":      True,
        "post_id": post.pk,
        "status":  post.status,
        "message": "Post created as draft.",
    }


def get_post(post_id: int) -> dict:
    """
    TOOL: get_post

    Retrieve a post's details.

    Returns:
        {
            "ok":       True,
            "post_id":  42,
            "title":    "...",
            "content":  "...",
            "hashtags": "...",
            "status":   "draft",
            "targets":  [ {"target_id": 1, "platform": "linkedin", "status": "pending"} ],
            "created_at": "2026-09-23T10:00:00Z"
        }
    """
    try:
        post = (
            Post.objects
            .prefetch_related("targets__social_account__platform")
            .get(pk=post_id)
        )
    except Post.DoesNotExist:
        return {"ok": False, "error": f"Post #{post_id} not found."}

    return {
        "ok":       True,
        "post_id":  post.pk,
        "title":    post.title,
        "content":  post.content_plain,
        "hashtags": post.hashtags,
        "status":   post.status,
        "targets": [
            {
                "target_id": t.pk,
                "platform":  t.social_account.platform.code,
                "account":   t.social_account.account_label,
                "status":    t.status,
            }
            for t in post.targets.all()
        ],
        "created_at": post.created_at.isoformat(),
        "updated_at": post.updated_at.isoformat(),
    }


def list_posts(user=None, status_filter: str = "", limit: int = 50) -> dict:
    """
    TOOL: list_posts

    List posts, optionally filtered by status.

    Parameters:
        user          Optional — filter by creator
        status_filter Optional status string, e.g. "draft", "published"
        limit         Max results (default 50)

    Returns:
        {"ok": True, "posts": [...], "total": N}
    """
    qs = Post.objects.select_related("created_by").order_by("-created_at")
    if user:
        qs = qs.filter(created_by=user)
    if status_filter:
        qs = qs.filter(status=status_filter)
    qs = qs[:limit]

    return {
        "ok":    True,
        "posts": [
            {
                "post_id":    p.pk,
                "title":      p.title,
                "snippet":    p.content_plain[:80],
                "status":     p.status,
                "created_at": p.created_at.isoformat(),
            }
            for p in qs
        ],
        "total": Post.objects.count(),
    }


def update_post(post_id: int, content: str = None,
                title: str = None, hashtags: str = None) -> dict:
    """
    TOOL: update_post

    Update a draft or scheduled post's content.
    Published posts cannot be edited (spec §31).

    Returns:
        {"ok": True, "post_id": 42, "message": "Post updated."}
    """
    try:
        post = Post.objects.get(pk=post_id)
    except Post.DoesNotExist:
        return {"ok": False, "error": f"Post #{post_id} not found."}

    if post.status == Post.Status.PUBLISHED:
        return {"ok": False, "error": "Published posts cannot be edited."}

    if content is not None:
        content = content.strip()
        if not content:
            return {"ok": False, "error": "Content cannot be empty."}
        post.content_plain = content
    if title is not None:
        post.title = title.strip()
    if hashtags is not None:
        post.hashtags = hashtags.strip()

    post.save()
    log.info("Tool update_post: updated Post #%s", post.pk)
    return {"ok": True, "post_id": post.pk, "message": "Post updated."}


def delete_post(post_id: int) -> dict:
    """
    TOOL: delete_post

    Delete a local Post record.
    NOTE (spec §32): does NOT delete any externally-published content.

    Returns:
        {"ok": True, "message": "Post deleted. External posts are not affected."}
    """
    try:
        post = Post.objects.get(pk=post_id)
    except Post.DoesNotExist:
        return {"ok": False, "error": f"Post #{post_id} not found."}

    post.delete()
    log.info("Tool delete_post: deleted Post #%s", post_id)
    return {
        "ok":     True,
        "message": "Post deleted. Any externally-published content is not affected.",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SOCIAL ACCOUNT TOOLS
# ══════════════════════════════════════════════════════════════════════════════

def list_social_accounts(user) -> dict:
    """
    TOOL: list_social_accounts

    Return safe social account info for a user.
    Credentials (tokens) are NEVER included — only IDs and labels.

    Returns:
        {"ok": True, "accounts": [...]}
    """
    accounts = (
        SocialAccount.objects
        .filter(user=user, status=SocialAccount.Status.ACTIVE)
        .select_related("platform")
    )
    return {
        "ok": True,
        "accounts": [
            {
                "social_account_id": a.pk,
                "platform":          a.platform.code,
                "platform_name":     a.platform.name,
                "account_label":     a.account_label,
                "status":            a.status,
            }
            for a in accounts
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLISHING TOOLS
# ══════════════════════════════════════════════════════════════════════════════

def publish_post(post_id: int, social_account_id: int) -> dict:
    """
    TOOL: publish_post

    Publish a post immediately to the given social account.

    This is the main publishing tool. It:
        1. Creates a PostTarget (PENDING → PUBLISHING)
        2. Commits the DB record
        3. Calls the platform adapter outside any transaction
        4. Updates PostTarget with result
        5. Writes a PublishingLog entry
        6. Recalculates Post status

    Parameters:
        post_id           ID of the Post to publish
        social_account_id ID of the SocialAccount to publish to

    Returns:
        {
            "ok":        True,
            "target_id": 7,
            "status":    "success",
            "external_post_id":  "urn:li:share:123",
            "external_post_url": "https://...",
            "message":   "Published successfully to LinkedIn."
        }
    """
    # ── Load and validate ─────────────────────────────────────────────────────
    try:
        post = Post.objects.prefetch_related("media").get(pk=post_id)
    except Post.DoesNotExist:
        return {"ok": False, "error": f"Post #{post_id} not found."}

    try:
        account = SocialAccount.objects.select_related("platform").get(
            pk=social_account_id
        )
    except SocialAccount.DoesNotExist:
        return {"ok": False, "error": f"Social account #{social_account_id} not found."}

    if not account.is_active:
        return {"ok": False, "error": "Social account is inactive. Please reconnect."}

    # ── Get adapter ───────────────────────────────────────────────────────────
    adapter = _get_adapter(account.platform.code)
    if adapter is None:
        # Create a FAILED PostTarget so the post status reflects the failure
        # rather than silently staying as DRAFT (spec §48)
        with transaction.atomic():
            target, _ = PostTarget.objects.get_or_create(
                post           = post,
                social_account = account,
                defaults={"status": PostTarget.Status.FAILED},
            )
            target.status     = PostTarget.Status.FAILED
            target.last_error = f"No publisher available for platform '{account.platform.code}'."
            target.save(update_fields=["status", "last_error", "updated_at"])
        return {
            "ok":   False,
            "error": f"No publisher available for platform '{account.platform.code}'.",
        }

    # ── Create PostTarget (transaction 1 — spec §25) ──────────────────────────
    with transaction.atomic():
        # Prevent duplicate targets for same post + account
        target, created = PostTarget.objects.get_or_create(
            post           = post,
            social_account = account,
            defaults={"status": PostTarget.Status.PENDING},
        )
        if not created and target.status == PostTarget.Status.SUCCESS:
            return {
                "ok":   False,
                "error": "This post has already been successfully published to this account.",
            }
        target.status = PostTarget.Status.PUBLISHING
        target.save(update_fields=["status", "updated_at"])

    # ── Call platform API (outside transaction — spec §25) ────────────────────
    started_at = timezone.now()
    request_id = f"req_{secrets.token_urlsafe(16)}"  # Idempotency tracking
    
    result: PublishResult = adapter.publish(target)
    ended_at = timezone.now()

    # ── Write result to DB (transaction 2) ────────────────────────────────────
    with transaction.atomic():
        attempt = target.attempt_count + 1

        if result.success:
            target.status             = PostTarget.Status.SUCCESS
            target.published_at       = ended_at
            target.external_post_id   = result.external_post_id
            target.external_post_url  = result.external_post_url
            target.attempt_count      = attempt
            target.last_error         = ""
            target.next_retry_at      = None
        else:
            if result.retryable and attempt < adapter.MAX_ATTEMPTS:
                delay = adapter.retry_delay_for(attempt)
                target.status        = PostTarget.Status.RETRYING
                target.next_retry_at = timezone.now() + timezone.timedelta(seconds=delay)
            else:
                target.status        = PostTarget.Status.FAILED
                target.next_retry_at = None
            target.attempt_count = attempt
            target.last_error    = result.message

        target.save()

        PublishingLog.objects.create(
            post_target      = target,
            attempt_number   = attempt,
            request_id       = request_id,  # For idempotency tracking
            started_at       = started_at,
            ended_at         = ended_at,
            response_type    = result.response_type,
            response_message = result.message,
            status = (
                PublishingLog.Status.SUCCESS if result.success
                else PublishingLog.Status.FAILED
            ),
        )

    # Note: Post status recalculation now happens automatically via signal
    # post.recalculate_status()  # No longer needed — handled by signal

    log.info(
        "Tool publish_post: Post #%s → account #%s → %s",
        post_id, social_account_id, target.status,
    )

    return {
        "ok":               result.success,
        "target_id":        target.pk,
        "status":           target.status,
        "external_post_id": result.external_post_id,
        "external_post_url":result.external_post_url,
        "message":          result.message,
    }


def schedule_post(post_id: int, social_account_id: int,
                  scheduled_at: datetime) -> dict:
    """
    TOOL: schedule_post

    Schedule a post for future publishing to a specific social account.
    Each target has its own independent schedule (spec §3).

    Parameters:
        post_id           ID of the Post
        social_account_id ID of the SocialAccount
        scheduled_at      Timezone-aware datetime for publishing

    Returns:
        {"ok": True, "target_id": 7, "scheduled_at": "2026-09-25T10:00:00Z"}
    """
    try:
        post = Post.objects.get(pk=post_id)
    except Post.DoesNotExist:
        return {"ok": False, "error": f"Post #{post_id} not found."}

    try:
        account = SocialAccount.objects.select_related("platform").get(
            pk=social_account_id
        )
    except SocialAccount.DoesNotExist:
        return {"ok": False, "error": f"Social account #{social_account_id} not found."}

    if not account.is_active:
        return {"ok": False, "error": "Social account is inactive."}

    # scheduled_at must be in the future
    if timezone.is_naive(scheduled_at):
        return {"ok": False, "error": "scheduled_at must be timezone-aware."}
    if scheduled_at <= timezone.now():
        return {"ok": False, "error": "scheduled_at must be in the future."}

    with transaction.atomic():
        target, _ = PostTarget.objects.update_or_create(
            post           = post,
            social_account = account,
            defaults={
                "status":       PostTarget.Status.SCHEDULED,
                "scheduled_at": scheduled_at,
            },
        )

    post.recalculate_status()

    log.info("Tool schedule_post: Post #%s scheduled for %s → account #%s",
             post_id, scheduled_at.isoformat(), social_account_id)

    return {
        "ok":          True,
        "target_id":   target.pk,
        "scheduled_at": scheduled_at.isoformat(),
        "message":     f"Scheduled for {scheduled_at.strftime('%b %d, %Y %H:%M UTC')}.",
    }


def cancel_scheduled_post(target_id: int) -> dict:
    """
    TOOL: cancel_scheduled_post

    Cancel a SCHEDULED PostTarget.
    Only targets with status=scheduled can be cancelled.

    Returns:
        {"ok": True, "message": "Scheduled post cancelled."}
    """
    try:
        target = PostTarget.objects.select_related("post").get(pk=target_id)
    except PostTarget.DoesNotExist:
        return {"ok": False, "error": f"PostTarget #{target_id} not found."}

    if target.status != PostTarget.Status.SCHEDULED:
        return {
            "ok":   False,
            "error": f"Cannot cancel — target is '{target.status}', not 'scheduled'.",
        }

    target.status       = PostTarget.Status.CANCELLED
    target.scheduled_at = None
    target.save(update_fields=["status", "scheduled_at", "updated_at"])
    # Note: Post status recalculation now automatic via signal

    log.info("Tool cancel_scheduled_post: PostTarget #%s cancelled", target_id)
    return {"ok": True, "message": "Scheduled post cancelled."}


# ══════════════════════════════════════════════════════════════════════════════
#  STATUS & LOG TOOLS
# ══════════════════════════════════════════════════════════════════════════════

def get_post_status(post_id: int) -> dict:
    """
    TOOL: get_post_status

    Get the publishing status of a post across all its targets.

    Returns:
        {
            "ok":         True,
            "post_id":    42,
            "post_status":"partially_published",
            "targets": [
                {
                    "target_id":        1,
                    "platform":         "linkedin",
                    "account":          "My LinkedIn",
                    "status":           "success",
                    "published_at":     "...",
                    "external_post_url":"https://..."
                }
            ]
        }
    """
    try:
        post = (
            Post.objects
            .prefetch_related("targets__social_account__platform")
            .get(pk=post_id)
        )
    except Post.DoesNotExist:
        return {"ok": False, "error": f"Post #{post_id} not found."}

    targets = []
    for t in post.targets.all():
        targets.append({
            "target_id":         t.pk,
            "platform":          t.social_account.platform.code,
            "account":           t.social_account.account_label,
            "status":            t.status,
            "scheduled_at":      t.scheduled_at.isoformat() if t.scheduled_at else None,
            "published_at":      t.published_at.isoformat() if t.published_at else None,
            "external_post_id":  t.external_post_id,
            "external_post_url": t.external_post_url,
            "attempt_count":     t.attempt_count,
            "last_error":        t.last_error,
            "next_retry_at":     t.next_retry_at.isoformat() if t.next_retry_at else None,
        })

    return {
        "ok":         True,
        "post_id":    post.pk,
        "post_status": post.status,
        "targets":    targets,
    }


def get_post_target_status(target_id: int) -> dict:
    """
    TOOL: get_post_target_status

    Get status and latest log entry for a specific PostTarget.

    Returns:
        {"ok": True, "target": {...}, "latest_log": {...}}
    """
    try:
        target = (
            PostTarget.objects
            .select_related("post", "social_account__platform")
            .get(pk=target_id)
        )
    except PostTarget.DoesNotExist:
        return {"ok": False, "error": f"PostTarget #{target_id} not found."}

    latest_log = target.logs.order_by("-attempt_number").first()

    return {
        "ok": True,
        "target": {
            "target_id":         target.pk,
            "post_id":           target.post_id,
            "platform":          target.social_account.platform.code,
            "account":           target.social_account.account_label,
            "status":            target.status,
            "attempt_count":     target.attempt_count,
            "last_error":        target.last_error,
            "scheduled_at":      target.scheduled_at.isoformat() if target.scheduled_at else None,
            "published_at":      target.published_at.isoformat() if target.published_at else None,
            "external_post_id":  target.external_post_id,
            "external_post_url": target.external_post_url,
            "next_retry_at":     target.next_retry_at.isoformat() if target.next_retry_at else None,
        },
        "latest_log": {
            "attempt_number":  latest_log.attempt_number,
            "status":          latest_log.status,
            "response_type":   latest_log.response_type,
            "message":         latest_log.response_message,
            "started_at":      latest_log.started_at.isoformat(),
        } if latest_log else None,
    }


def get_publishing_logs(target_id: int) -> dict:
    """
    TOOL: get_publishing_logs

    Get the full publishing attempt history for a PostTarget.

    Returns:
        {"ok": True, "target_id": 7, "logs": [...]}
    """
    try:
        target = PostTarget.objects.get(pk=target_id)
    except PostTarget.DoesNotExist:
        return {"ok": False, "error": f"PostTarget #{target_id} not found."}

    logs = target.logs.order_by("attempt_number")
    return {
        "ok":       True,
        "target_id": target_id,
        "logs": [
            {
                "attempt_number": l.attempt_number,
                "status":         l.status,
                "response_type":  l.response_type,
                "message":        l.response_message,
                "started_at":     l.started_at.isoformat(),
                "ended_at":       l.ended_at.isoformat() if l.ended_at else None,
            }
            for l in logs
        ],
    }


def unpublish_post_targets(post_id: int, target_ids: list[int],
                           delete_local: bool, user) -> dict:
    """
    TOOL: unpublish_post_targets

    Delete a post from the selected platform targets.
    Only deletes the local Post record when:
      - delete_local is True  AND
      - after this operation, zero PostTargets remain in SUCCESS status

    Rules:
      - Per selected target: call adapter.delete_from_platform()
          success → mark target CANCELLED, clear external_post_url, write SUCCESS log
          failure → write FAILED log, store error in last_error, keep target intact
      - If any platform deletion fails → never delete locally, return error
      - If delete_local=False → never delete locally regardless
      - If delete_local=True but remaining success targets exist → never delete locally
      - Only when delete_local=True AND all success targets are gone → hard-delete Post

    No migrations needed — uses existing PublishingLog, PostTarget, Post tables.
    """
    try:
        post = Post.objects.prefetch_related(
            "targets__social_account__platform"
        ).get(pk=post_id)
    except Post.DoesNotExist:
        return {"ok": False, "error": f"Post #{post_id} not found."}

    deleted_from  = []
    failed_labels = []

    selected_targets = list(
        post.targets
        .filter(pk__in=target_ids, status=PostTarget.Status.SUCCESS)
        .select_related("social_account__platform")
    )

    for target in selected_targets:
        adapter = _get_adapter(target.social_account.platform.code)
        platform_label = (
            f"{target.social_account.platform.name} — "
            f"{target.social_account.account_label}"
        )

        if adapter is None or not hasattr(adapter, "delete_from_platform"):
            failed_labels.append(f"{platform_label} (deletion not supported)")
            continue

        started_at = timezone.now()
        result: PublishResult = adapter.delete_from_platform(target)
        ended_at   = timezone.now()

        with transaction.atomic():
            attempt = target.attempt_count + 1

            PublishingLog.objects.create(
                post_target      = target,
                attempt_number   = attempt,
                request_id       = f"del_{secrets.token_urlsafe(12)}",
                started_at       = started_at,
                ended_at         = ended_at,
                response_type    = result.response_type or "DELETE",
                response_message = result.message,
                status=(
                    PublishingLog.Status.SUCCESS if result.success
                    else PublishingLog.Status.FAILED
                ),
            )
            target.attempt_count = attempt

            if result.success:
                target.status            = PostTarget.Status.CANCELLED
                target.external_post_url = ""
                target.last_error        = ""
                target.save(update_fields=[
                    "status", "external_post_url", "last_error",
                    "attempt_count", "updated_at",
                ])
                deleted_from.append(platform_label)
                log.info(
                    "unpublish_post_targets: removed Post #%s from %s",
                    post_id, platform_label,
                )
            else:
                target.last_error = f"[Delete failed] {result.message}"
                target.save(update_fields=["last_error", "attempt_count", "updated_at"])
                failed_labels.append(f"{platform_label}: {result.message}")
                log.warning(
                    "unpublish_post_targets: failed to remove Post #%s from %s — %s",
                    post_id, platform_label, result.message,
                )

    # ── Decide whether to delete the local post ───────────────────────────────
    if failed_labels:
        # Platform deletion(s) failed — never delete locally
        detail = "; ".join(failed_labels)
        return {
            "ok":           False,
            "deleted_from": deleted_from,
            "failed":       failed_labels,
            "error": (
                f"Could not remove from {len(failed_labels)} platform(s): {detail}. "
                f"Post kept locally. Fix the issue and try again."
            ),
        }

    # Check how many SUCCESS targets remain after this operation
    remaining_success = post.targets.filter(
        status=PostTarget.Status.SUCCESS
    ).count()

    if not delete_local:
        # User didn't request local deletion
        msg = f"Removed from {len(deleted_from)} platform(s)." if deleted_from else "No platforms removed."
        return {
            "ok":           True,
            "deleted_from": deleted_from,
            "failed":       [],
            "local_deleted": False,
            "remaining_success": remaining_success,
            "message":      msg,
        }

    if remaining_success > 0:
        # Still live on at least one platform — refuse local deletion
        return {
            "ok":           True,
            "deleted_from": deleted_from,
            "failed":       [],
            "local_deleted": False,
            "remaining_success": remaining_success,
            "message": (
                f"Removed from {len(deleted_from)} platform(s), but the post is still "
                f"live on {remaining_success} other platform(s). "
                f"Local record kept."
            ),
        }

    # All platforms cleared AND user explicitly requested local deletion → delete
    post_label = str(post)
    post.delete()
    log.info(
        "unpublish_post_targets: hard-deleted local Post #%s ('%s')",
        post_id, post_label,
    )
    return {
        "ok":           True,
        "deleted_from": deleted_from,
        "failed":       [],
        "local_deleted": True,
        "remaining_success": 0,
        "message": (
            f"Removed from {len(deleted_from)} platform(s) and deleted locally."
            if deleted_from else "Post deleted locally."
        ),
    }


def retry_post_target(target_id: int) -> dict:
    """
    TOOL: retry_post_target

    Manually retry a FAILED PostTarget immediately.

    Returns:
        {"ok": True/False, "target_id": 7, "status": "success", "message": "..."}
    """
    try:
        target = (
            PostTarget.objects
            .select_related("post", "social_account__platform")
            .get(pk=target_id)
        )
    except PostTarget.DoesNotExist:
        return {"ok": False, "error": f"PostTarget #{target_id} not found."}

    if target.status not in {PostTarget.Status.FAILED, PostTarget.Status.RETRYING}:
        return {
            "ok":   False,
            "error": f"Cannot retry — target status is '{target.status}'.",
        }

    # Get the adapter so we use its own MAX_ATTEMPTS, not a hardcoded value
    adapter = _get_adapter(target.social_account.platform.code)
    max_attempts = adapter.MAX_ATTEMPTS if adapter else 4

    if target.attempt_count >= max_attempts:
        return {
            "ok":   False,
            "error": f"Maximum retry attempts ({max_attempts}) reached.",
        }

    # Delegate back to publish_post (it creates the log + updates status)
    return publish_post(
        post_id           = target.post_id,
        social_account_id = target.social_account_id,
    )
