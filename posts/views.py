"""
Posts module views — Phase 5/6/7/8 (Publish Now, Schedule, Retry, Status).

PHASE 5-7 ENHANCEMENTS:
  • Publishing now uses async tasks (prevents HTTP timeout)
  • Scheduled posts processed by background worker
  • Improved error handling and status updates

URL map
───────
GET  /posts/                                    post_list
GET/POST /posts/create/                         post_create
GET  /posts/<pk>/                               post_detail
GET/POST /posts/<pk>/edit/                      post_edit
POST /posts/<pk>/delete/                        post_delete
POST /posts/<pk>/publish-now/                   post_publish_now    [ASYNC]
POST /posts/<pk>/schedule/                      post_schedule       [ASYNC]
POST /posts/media/<pk>/delete/                  media_delete
POST /posts/targets/<pk>/retry/                 target_retry        [ASYNC]
POST /posts/targets/<pk>/cancel/                target_cancel

GET  /posts/social-accounts/                    social_account_list
GET  /posts/social-accounts/linkedin/connect/   linkedin_connect
GET  /posts/social-accounts/linkedin/callback/  linkedin_callback
GET  /posts/social-accounts/facebook/connect/   facebook_connect
GET  /posts/social-accounts/facebook/callback/  facebook_callback
POST /posts/social-accounts/<pk>/disconnect/    social_account_delete
"""

import logging
import mimetypes
import os
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_q.tasks import async_task

from rbac.decorators import rbac_permission_required

from .forms import PostForm, PostMediaForm
from .models import (
    OAuthState, Platform, Post, PostMedia,
    PostTarget, SocialAccount, SocialAccountCredential,
)
from .services import (
    cancel_scheduled_post,
    publish_post,
    retry_post_target,
    schedule_post as svc_schedule_post,
    unpublish_post_targets,
)
from .services.linkedin_oauth import (
    LinkedInOAuthError,
    build_authorization_url,
    cleanup_expired_states,
    exchange_code_for_token,
    fetch_linkedin_profile,
    is_state_expired,
)
from .services.facebook_oauth import (
    FacebookOAuthError,
    build_authorization_url       as fb_build_authorization_url,
    cleanup_expired_states        as fb_cleanup_expired_states,
    exchange_code_for_token       as fb_exchange_code_for_token,
    exchange_for_long_lived_token as fb_exchange_for_long_lived_token,
    fetch_facebook_pages,
    fetch_facebook_profile,
    is_state_expired              as fb_is_state_expired,
)
from .services.linkedin_page_oauth import (
    LinkedInPageOAuthError,
    build_authorization_url  as lip_build_authorization_url,
    cleanup_expired_states   as lip_cleanup_expired_states,
    exchange_code_for_token  as lip_exchange_code_for_token,
    fetch_member_profile     as lip_fetch_member_profile,
    fetch_linkedin_pages     as lip_fetch_linkedin_pages,
    is_state_expired         as lip_is_state_expired,
)
from .services.instagram_oauth import (
    InstagramOAuthError,
    build_authorization_url    as ig_build_authorization_url,
    cleanup_expired_states     as ig_cleanup_expired_states,
    exchange_code_for_token    as ig_exchange_code_for_token,
    exchange_for_long_lived_token as ig_exchange_for_long_lived_token,
    fetch_instagram_accounts,
    is_state_expired           as ig_is_state_expired,
)

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  POSTS — CRUD
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_view_posts")
def post_list(request):
    """All posts, newest first, with optional status filter."""
    status_filter = request.GET.get("status", "")
    qs = (
        Post.objects
        .select_related("created_by")
        .prefetch_related("targets__social_account__platform", "media")
        .order_by("-created_at")
    )
    if status_filter:
        qs = qs.filter(status=status_filter)

    return render(request, "posts/list.html", {
        "posts":          qs,
        "status_filter":  status_filter,
        "status_choices": Post.Status.choices,
        "total_count":    Post.objects.count(),
    })


@rbac_permission_required("can_create_posts")
def post_create(request):
    """
    Create a new Post.

    The form supports three actions (via `action` button value):
        draft   → save as draft only, no publishing
        publish → save, then publish/schedule to each selected account

    For each selected account the form sends:
        publish_mode_<id>       = 'now' | 'schedule'
        scheduled_date_<id>     = 'YYYY-MM-DD'  (only when mode=schedule)
        scheduled_time_<id>     = 'HH:MM'       (only when mode=schedule)
    """
    social_accounts = (
        SocialAccount.objects
        .filter(user=request.user, status=SocialAccount.Status.ACTIVE)
        .select_related("platform")
        .order_by("platform__name", "account_label")
    )

    if request.method == "POST":
        form = PostForm(request.POST)
        if form.is_valid():
            # ── Save post ─────────────────────────────────────────────────────
            post = form.save(commit=False)
            post.created_by      = request.user
            post.created_by_type = Post.CreatorType.USER
            post.status          = Post.Status.DRAFT
            post.save()

            # ── Optional featured image (marked as main) ─────────────────────
            featured_image = request.FILES.get("featured_image")
            if featured_image:
                mime, _ = mimetypes.guess_type(featured_image.name)
                PostMedia.objects.create(
                    post       = post,
                    file       = featured_image,
                    mime_type  = mime or "",
                    file_size  = featured_image.size,
                    media_type = PostMedia.MediaType.IMAGE,
                    is_main    = True,
                    sort_order = 0,
                )

            # ── Optional multiple additional file uploads ─────────────────────
            media_files = request.FILES.getlist("files")
            if media_files:
                # Start sort order from 1 if featured image exists, else 0
                start_order = 1 if featured_image else 0
                for idx, media_file in enumerate(media_files):
                    mime, _ = mimetypes.guess_type(media_file.name)
                    
                    # Determine media type based on MIME type
                    if mime and mime.startswith("image/"):
                        media_type = PostMedia.MediaType.IMAGE
                    elif mime and mime.startswith("video/"):
                        media_type = PostMedia.MediaType.VIDEO
                    else:
                        media_type = PostMedia.MediaType.DOCUMENT
                    
                    PostMedia.objects.create(
                        post       = post,
                        file       = media_file,
                        mime_type  = mime or "",
                        file_size  = media_file.size,
                        media_type = media_type,
                        is_main    = False,  # Additional files are never main
                        sort_order = start_order + idx,
                    )

            action          = request.POST.get("action", "draft")
            selected_ids    = request.POST.getlist("selected_accounts")

            # ── If publish action and accounts selected — process each ─────────
            if action == "publish" and selected_ids:
                now_count      = 0
                sched_count    = 0
                fail_count     = 0

                for acct_id in selected_ids:
                    mode = request.POST.get(f"publish_mode_{acct_id}", "now")

                    if mode == "schedule":
                        date_str = request.POST.get(f"scheduled_date_{acct_id}", "").strip()
                        time_str = request.POST.get(f"scheduled_time_{acct_id}", "").strip()
                        if not date_str or not time_str:
                            messages.warning(
                                request,
                                f"No date/time set for one account — skipped scheduling."
                            )
                            fail_count += 1
                            continue
                        try:
                            naive_dt     = timezone.datetime.strptime(
                                f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
                            )
                            scheduled_at = timezone.make_aware(naive_dt, dt_timezone.utc)
                        except ValueError:
                            fail_count += 1
                            continue

                        if scheduled_at <= timezone.now():
                            messages.warning(
                                request,
                                "Scheduled time must be in the future — one account skipped."
                            )
                            fail_count += 1
                            continue

                        result = svc_schedule_post(
                            post_id           = post.pk,
                            social_account_id = int(acct_id),
                            scheduled_at      = scheduled_at,
                        )
                        if result.get("ok"):
                            sched_count += 1
                        else:
                            fail_count += 1
                    else:
                        # Publish Now
                        result = publish_post(
                            post_id           = post.pk,
                            social_account_id = int(acct_id),
                        )
                        if result.get("ok"):
                            now_count += 1
                        else:
                            fail_count += 1

                # ── Summary message ───────────────────────────────────────────
                parts = []
                if now_count:
                    parts.append(f"Published to {now_count} account{'s' if now_count != 1 else ''}")
                if sched_count:
                    parts.append(f"scheduled on {sched_count}")
                if parts:
                    msg = ", ".join(parts) + "."
                    if fail_count:
                        msg += f" {fail_count} failed — see status below."
                        messages.warning(request, msg)
                    else:
                        messages.success(request, msg)
                elif fail_count:
                    messages.error(request, f"All {fail_count} publishing attempt(s) failed. Check the status below.")
                else:
                    messages.info(request, "Post saved as draft.")
            else:
                messages.success(request, "Post saved as draft.")

            return redirect("posts:post_detail", pk=post.pk)
    else:
        form = PostForm()

    return render(request, "posts/create.html", {
        "form":            form,
        "social_accounts": social_accounts,
    })


@rbac_permission_required("can_view_posts")
def post_detail(request, pk):
    """View a single post with all targets and publishing history."""
    post = get_object_or_404(
        Post.objects
        .select_related("created_by")
        .prefetch_related(
            "media",
            "targets__social_account__platform",
            "targets__logs",
        ),
        pk=pk,
    )
    # Social accounts for the publish panel
    social_accounts = (
        SocialAccount.objects
        .filter(user=request.user, status=SocialAccount.Status.ACTIVE)
        .select_related("platform")
        .order_by("platform__name", "account_label")
    )
    return render(request, "posts/detail.html", {
        "post":            post,
        "social_accounts": social_accounts,
    })


@rbac_permission_required("can_edit_posts")
def post_edit(request, pk):
    """Edit a draft or scheduled post. Published posts are read-only (spec §31)."""
    post = get_object_or_404(Post, pk=pk)

    if post.status == Post.Status.PUBLISHED:
        messages.error(request, "Published posts cannot be edited.")
        return redirect("posts:post_detail", pk=post.pk)

    if request.method == "POST":
        # Handle featured image upload
        featured_image = request.FILES.get("featured_image")
        if featured_image:
            mime, _ = mimetypes.guess_type(featured_image.name)
            # Check if there's already a main image
            existing_main = post.media.filter(is_main=True).first()
            if existing_main:
                # Replace the existing main image
                existing_main.file = featured_image
                existing_main.mime_type = mime or ""
                existing_main.file_size = featured_image.size
                existing_main.media_type = PostMedia.MediaType.IMAGE
                existing_main.save()
            else:
                # Create new main image
                PostMedia.objects.create(
                    post       = post,
                    file       = featured_image,
                    mime_type  = mime or "",
                    file_size  = featured_image.size,
                    media_type = PostMedia.MediaType.IMAGE,
                    is_main    = True,
                    sort_order = 0,
                )
        
        # Handle additional files upload
        media_files = request.FILES.getlist("files")
        if media_files:
            existing_count = post.media.count()
            for idx, media_file in enumerate(media_files):
                mime, _ = mimetypes.guess_type(media_file.name)
                
                # Determine media type based on MIME type
                if mime and mime.startswith("image/"):
                    media_type = PostMedia.MediaType.IMAGE
                elif mime and mime.startswith("video/"):
                    media_type = PostMedia.MediaType.VIDEO
                else:
                    media_type = PostMedia.MediaType.DOCUMENT
                
                PostMedia.objects.create(
                    post       = post,
                    file       = media_file,
                    mime_type  = mime or "",
                    file_size  = media_file.size,
                    media_type = media_type,
                    is_main    = False,
                    sort_order = existing_count + idx,
                )

        form = PostForm(request.POST, instance=post)
        if form.is_valid():
            form.save()
            messages.success(request, "Post updated.")
            return redirect("posts:post_detail", pk=post.pk)
    else:
        form = PostForm(instance=post)

    return render(request, "posts/edit.html", {"form": form, "post": post})


@rbac_permission_required("can_delete_posts")
@require_http_methods(["POST"])
def post_delete(request, pk):
    """Delete local Post record. Does NOT delete externally-published content (spec §32)."""
    post = get_object_or_404(Post, pk=pk)
    label = str(post)
    post.delete()
    messages.success(request, f'Post "{label}" deleted.')
    return redirect("posts:post_list")


@rbac_permission_required("can_delete_posts")
@require_http_methods(["POST"])
def post_unpublish(request, pk):
    """
    Remove a post from selected platforms and optionally delete it locally.

    POST params:
        target_ids[]   — PostTarget PKs to remove from their platforms
        delete_local   — "1" if user wants to delete the local record too

    Local deletion only happens when:
      - delete_local == "1"  AND
      - zero PostTargets remain in SUCCESS status after platform removals
    """
    post = get_object_or_404(Post, pk=pk)

    target_ids_raw = request.POST.getlist("target_ids")
    try:
        target_ids = [int(t) for t in target_ids_raw if t]
    except (ValueError, TypeError):
        target_ids = []

    delete_local = request.POST.get("delete_local") == "1"

    result = unpublish_post_targets(
        post_id      = post.pk,
        target_ids   = target_ids,
        delete_local = delete_local,
        user         = request.user,
    )

    if result.get("ok"):
        if result.get("local_deleted"):
            messages.success(request, result["message"])
            return redirect("posts:post_list")
        else:
            remaining = result.get("remaining_success", 0)
            msg = result["message"]
            if remaining > 0:
                msg += f" {remaining} platform(s) still active — remove them all to delete locally."
            messages.info(request, msg)
            return redirect("posts:post_detail", pk=post.pk)
    else:
        messages.error(request, result.get("error", "Delete failed."))
        return redirect("posts:post_detail", pk=post.pk)


@rbac_permission_required("can_delete_posts")
@require_http_methods(["POST"])
def media_delete(request, pk):
    """Remove a single media file from a post."""
    media   = get_object_or_404(PostMedia, pk=pk)
    post_pk = media.post_id
    try:
        if media.file and os.path.isfile(media.file.path):
            os.remove(media.file.path)
    except Exception:
        pass
    media.delete()
    messages.success(request, "Image removed.")
    return redirect("posts:post_edit", pk=post_pk)


# ══════════════════════════════════════════════════════════════════════════════
#  PHASE 5 — PUBLISH NOW
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_publish_posts")
@require_http_methods(["POST"])
def post_publish_now(request, pk):
    """
    Publish a post immediately to one or more selected social accounts.
    
    PHASE 5-7: Now uses async task queue to prevent HTTP blocking.
    Publishing happens in background; user sees "Publishing..." status immediately.

    Reads selected_accounts[] from POST body.
    Queues async publish_post_task() for each — independently, one failure
    never stops the others (spec §26).
    """
    post = get_object_or_404(Post, pk=pk)
    account_ids = request.POST.getlist("selected_accounts")

    if not account_ids:
        messages.error(request, "Select at least one account to publish to.")
        return redirect("posts:post_detail", pk=pk)

    queued_count = 0

    for account_id in account_ids:
        try:
            account_id_int = int(account_id)
            
            # If DEBUG mode, run synchronously for easier testing
            if settings.DEBUG and getattr(settings, "Q_CLUSTER", {}).get("sync", False):
                result = publish_post(
                    post_id=post.pk,
                    social_account_id=account_id_int,
                )
                if result.get("ok"):
                    queued_count += 1
                else:
                    log.warning(
                        "publish_post failed Post #%s → account #%s: %s",
                        post.pk, account_id, result.get("error", "unknown"),
                    )
            else:
                # Production: queue async task
                from .tasks import publish_post_task
                async_task(
                    publish_post_task,
                    post.pk,
                    account_id_int,
                    task_name=f"publish_post_{post.pk}_{account_id_int}",
                )
                queued_count += 1
                
        except (ValueError, TypeError):
            log.error(f"Invalid account ID: {account_id}")
            continue

    if queued_count == len(account_ids):
        messages.success(
            request,
            f"Publishing to {queued_count} account{'s' if queued_count != 1 else ''} "
            f"has been queued. Check status below in a few moments."
        )
    elif queued_count > 0:
        messages.warning(
            request,
            f"Queued {queued_count} of {len(account_ids)} accounts. "
            f"Check status below."
        )
    else:
        messages.error(request, "Failed to queue publishing tasks.")

    return redirect("posts:post_detail", pk=pk)


# ══════════════════════════════════════════════════════════════════════════════
#  PHASE 6 — SCHEDULE
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_schedule_posts")
@require_http_methods(["POST"])
def post_schedule(request, pk):
    """
    Schedule a post for future publishing.

    Reads from POST:
        selected_accounts[]  — one or more account IDs
        scheduled_date       — YYYY-MM-DD
        scheduled_time       — HH:MM

    Each account gets its own independent PostTarget with the same
    scheduled_at time (they can be adjusted individually later).
    """
    post = get_object_or_404(Post, pk=pk)
    account_ids    = request.POST.getlist("selected_accounts")
    scheduled_date = request.POST.get("scheduled_date", "").strip()
    scheduled_time = request.POST.get("scheduled_time", "").strip()

    if not account_ids:
        messages.error(request, "Select at least one account to schedule.")
        return redirect("posts:post_detail", pk=pk)

    if not scheduled_date or not scheduled_time:
        messages.error(request, "Please provide both date and time for scheduling.")
        return redirect("posts:post_detail", pk=pk)

    # Parse datetime — stored in UTC (spec §43)
    try:
        naive_dt = timezone.datetime.strptime(
            f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M"
        )
        scheduled_at = timezone.make_aware(naive_dt, dt_timezone.utc)
    except ValueError:
        messages.error(request, "Invalid date or time format.")
        return redirect("posts:post_detail", pk=pk)

    if scheduled_at <= timezone.now():
        messages.error(request, "Scheduled time must be in the future.")
        return redirect("posts:post_detail", pk=pk)

    success_count = 0
    fail_count    = 0

    for account_id in account_ids:
        try:
            account_id_int = int(account_id)
            
            # Use task-based scheduling
            from .tasks import schedule_post_task
            result = schedule_post_task(
                post_id=post.pk,
                social_account_id=account_id_int,
                scheduled_at=scheduled_at,
            )
            
            if result.get("ok"):
                success_count += 1
            else:
                fail_count += 1
                log.error(f"Failed to schedule: {result.get('error')}")
                
        except (ValueError, TypeError):
            fail_count += 1

    if success_count and not fail_count:
        dt_str = scheduled_at.strftime("%b %d, %Y at %H:%M UTC")
        messages.success(request, f"Scheduled for {dt_str} on {success_count} account{'' if success_count == 1 else 's'}.")
    elif success_count and fail_count:
        messages.warning(request, f"Scheduled on {success_count} account{'' if success_count == 1 else 's'}, {fail_count} failed.")
    else:
        messages.error(request, "Scheduling failed. Check your accounts and try again.")

    return redirect("posts:post_detail", pk=pk)


# ══════════════════════════════════════════════════════════════════════════════
#  PHASE 7 — RETRY
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_publish_posts")
@require_http_methods(["POST"])
def target_retry(request, pk):
    """
    Manually retry a failed PostTarget immediately.
    
    PHASE 5-7: Uses async task to prevent blocking.
    """
    target = get_object_or_404(PostTarget, pk=pk)
    
    # Queue async retry task
    from .tasks import publish_post_task
    if settings.DEBUG and getattr(settings, "Q_CLUSTER", {}).get("sync", False):
        # Sync mode for testing
        result = retry_post_target(target_id=pk)
        if result.get("ok"):
            messages.success(request, f"Retry succeeded — published to {target.social_account.account_label}.")
        else:
            messages.error(request, f"Retry failed: {result.get('error') or result.get('message', 'Unknown error')}")
    else:
        # Async mode
        async_task(
            publish_post_task,
            target.post_id,
            target.social_account_id,
            task_name=f"retry_{target.pk}",
        )
        messages.success(
            request,
            f"Retry queued for {target.social_account.account_label}. Check status in a moment."
        )

    return redirect("posts:post_detail", pk=target.post_id)


@rbac_permission_required("can_publish_posts")
@require_http_methods(["POST"])
def target_cancel(request, pk):
    """Cancel a SCHEDULED PostTarget."""
    target = get_object_or_404(PostTarget, pk=pk)
    result = cancel_scheduled_post(target_id=pk)

    if result.get("ok"):
        messages.success(request, "Scheduled post cancelled.")
    else:
        messages.error(request, f"Could not cancel: {result.get('error', '')}")

    return redirect("posts:post_detail", pk=target.post_id)


# ══════════════════════════════════════════════════════════════════════════════
#  SOCIAL ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_view_social_accounts")
def social_account_list(request):
    """List all social accounts belonging to the current user."""
    accounts = (
        SocialAccount.objects
        .filter(user=request.user)
        .select_related("platform")
        .order_by("platform__name", "account_label")
    )
    platforms = Platform.objects.filter(status=Platform.Status.ACTIVE)
    return render(request, "posts/social_accounts/list.html", {
        "accounts":                  accounts,
        "platforms":                 platforms,
        "has_linkedin_accounts":     accounts.filter(platform__code="linkedin").exists(),
        "has_facebook_accounts":     accounts.filter(platform__code="facebook").exists(),
        "has_linkedin_page_accounts":accounts.filter(platform__code="linkedin_page").exists(),
        "has_instagram_accounts":    accounts.filter(platform__code="instagram").exists(),
    })


@rbac_permission_required("can_disconnect_social_accounts")
@require_http_methods(["POST"])
def social_account_delete(request, pk):
    """
    Disconnect (soft-deactivate) a social account.
    Spec §52: set inactive, never hard-delete — preserves publishing history.
    """
    account = get_object_or_404(SocialAccount, pk=pk, user=request.user)
    account.status = SocialAccount.Status.INACTIVE
    account.save(update_fields=["status", "updated_at"])
    messages.success(request, f'"{account.account_label}" disconnected.')
    return redirect("posts:social_account_list")


@rbac_permission_required("can_edit_social_accounts")
@require_http_methods(["POST"])
def social_account_edit_label(request, pk):
    """
    Edit the friendly label of a social account.
    """
    account = get_object_or_404(SocialAccount, pk=pk, user=request.user)
    new_label = request.POST.get("account_label", "").strip()
    
    if not new_label:
        messages.error(request, "Account label cannot be empty.")
    else:
        account.account_label = new_label
        account.save(update_fields=["account_label", "updated_at"])
        messages.success(request, f'Account label updated to "{new_label}".')
    
    return redirect("posts:social_account_list")


# ══════════════════════════════════════════════════════════════════════════════
#  LINKEDIN OAUTH — Phase 3
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_connect_social_accounts")
def linkedin_connect(request):
    """
    Step 1 — Redirect the user to LinkedIn's authorization page.

    Generates a random `state` token, saves it to DB, then sends
    the user to LinkedIn. LinkedIn will call linkedin_callback() next.
    """
    try:
        linkedin_platform = Platform.objects.get(code="linkedin")
    except Platform.DoesNotExist:
        messages.error(request, "LinkedIn platform is not configured. Run the seeder first.")
        return redirect("posts:social_account_list")

    auth_url, state = build_authorization_url(request)

    # Persist state so we can verify it on callback (CSRF protection)
    OAuthState.objects.create(
        user     = request.user,
        state    = state,
        platform = linkedin_platform,
    )

    return redirect(auth_url)


@rbac_permission_required("can_connect_social_accounts")
def linkedin_callback(request):
    """
    Step 2 — LinkedIn redirects back here with ?code=...&state=...

    Flow:
        1.  Clean up expired OAuthState records (housekeeping)
        2.  Verify state matches a valid, non-expired OAuthState (CSRF check)
        3.  Exchange code for access token
        4.  Fetch LinkedIn profile (sub = stable member ID, name)
        5.  Create or reactivate SocialAccount
        6.  Store / update SocialAccountCredential (access token + expiry)
        7.  Delete the used OAuthState record
        8.  Redirect to social accounts list with success message

    Error cases all redirect safely to social_account_list with a message.
    The `code` is single-use — never retry with the same code.
    """
    # ── Housekeeping: delete stale state records ──────────────────────────────
    cleanup_expired_states()

    code  = request.GET.get("code",  "").strip()
    state = request.GET.get("state", "").strip()
    error = request.GET.get("error", "").strip()

    # ── LinkedIn returned an error or user clicked Cancel ─────────────────────
    if error:
        error_desc = request.GET.get("error_description", error)
        messages.warning(request, f"LinkedIn connection cancelled: {error_desc}")
        return redirect("posts:social_account_list")

    if not code or not state:
        messages.error(request, "Invalid callback — missing code or state parameter.")
        return redirect("posts:social_account_list")

    # ── CSRF state verification ───────────────────────────────────────────────
    try:
        oauth_state = OAuthState.objects.select_related("platform").get(
            state = state,
            user  = request.user,
        )
    except OAuthState.DoesNotExist:
        messages.error(
            request,
            "Invalid or expired authorization token. Please try connecting again."
        )
        return redirect("posts:social_account_list")

    # Reject states older than OAUTH_STATE_EXPIRY_MINUTES
    if is_state_expired(oauth_state):
        oauth_state.delete()
        messages.error(
            request,
            "Authorization timed out (> 10 minutes). Please try connecting again."
        )
        return redirect("posts:social_account_list")

    platform = oauth_state.platform
    oauth_state.delete()   # one-time use — delete immediately

    # ── Exchange code for access token ────────────────────────────────────────
    try:
        token_data = exchange_code_for_token(request, code)
    except LinkedInOAuthError as exc:
        log.warning("LinkedIn token exchange failed user=%s: %s", request.user.pk, exc)
        messages.error(request, f"Could not connect LinkedIn: {exc}")
        return redirect("posts:social_account_list")

    access_token = token_data["access_token"]
    expires_in   = token_data.get("expires_in")   # seconds, typically ~60 days
    scope        = token_data.get("scope", "")

    # ── Fetch LinkedIn member profile ─────────────────────────────────────────
    try:
        profile = fetch_linkedin_profile(access_token)
    except LinkedInOAuthError as exc:
        log.warning("LinkedIn profile fetch failed user=%s: %s", request.user.pk, exc)
        messages.error(request, f"Connected but could not read LinkedIn profile: {exc}")
        return redirect("posts:social_account_list")

    member_id = profile["sub"]                          # stable OIDC subject ID
    
    # Build account label with proper fallbacks
    full_name = (
        profile.get("name") or 
        profile.get("given_name") or 
        profile.get("localizedFirstName") or
        f"LinkedIn Account"
    )
    
    # Ensure we never store empty or "undefined" string
    if not full_name or full_name.strip() == "" or "undefined" in full_name.lower():
        full_name = f"LinkedIn Account ({member_id[:8]})"

    # ── Create or reactivate SocialAccount ────────────────────────────────────
    account, created = SocialAccount.objects.get_or_create(
        user                = request.user,
        platform            = platform,
        external_account_id = member_id,
        defaults={
            "account_label": full_name,
            "status":        SocialAccount.Status.ACTIVE,
        },
    )
    if not created:
        account.status        = SocialAccount.Status.ACTIVE
        account.account_label = full_name
        account.save(update_fields=["status", "account_label", "updated_at"])

    # ── Store / update credential ─────────────────────────────────────────────
    # token stored as plain text for now — encrypt at rest in production (spec §10)
    expires_at = None
    if expires_in:
        try:
            expires_at = timezone.now() + timezone.timedelta(seconds=int(expires_in))
        except (ValueError, TypeError):
            pass

    SocialAccountCredential.objects.update_or_create(
        social_account = account,
        defaults={
            "token_type":    token_data.get("token_type", "Bearer"),
            "access_token":  access_token,
            "refresh_token": token_data.get("refresh_token", ""),
            "scope":         scope,
            "expires_at":    expires_at,
        },
    )

    verb = "connected" if created else "reconnected"
    messages.success(request, f'LinkedIn account "{full_name}" {verb} successfully.')
    log.info(
        "LinkedIn OAuth success: user=%s account=%s member_id=%s action=%s",
        request.user.pk, account.pk, member_id[:8] + "…", verb,
    )
    return redirect("posts:social_account_list")


# ══════════════════════════════════════════════════════════════════════════════
#  FACEBOOK OAUTH
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_connect_social_accounts")
def facebook_connect(request):
    """
    Step 1 — Redirect the user to Facebook's authorization dialog.

    Generates a random `state` token, persists it to DB for CSRF protection,
    then sends the user to Facebook. Facebook will call facebook_callback() next.

    The user must be a Page Admin/Editor for at least one Facebook Page —
    the callback will list all pages they manage and connect each as a
    separate SocialAccount so they can pick which page(s) to publish to.
    """
    try:
        facebook_platform = Platform.objects.get(code="facebook")
    except Platform.DoesNotExist:
        messages.error(
            request,
            "Facebook platform is not configured. Run the seeder first."
        )
        return redirect("posts:social_account_list")

    auth_url, state = fb_build_authorization_url(request)

    # Persist state so we can verify it on callback (CSRF protection)
    OAuthState.objects.create(
        user     = request.user,
        state    = state,
        platform = facebook_platform,
    )

    return redirect(auth_url)


@rbac_permission_required("can_connect_social_accounts")
def facebook_callback(request):
    """
    Step 2 — Facebook redirects back here with ?code=...&state=...

    Flow:
        1.  Clean up expired OAuthState records (housekeeping)
        2.  Verify state matches a valid, non-expired OAuthState (CSRF check)
        3.  Exchange code for short-lived user access token
        4.  Exchange short-lived token for long-lived token (~60 days)
        5.  Fetch list of Facebook Pages the user manages
        6.  For each managed page:
              a. Create or reactivate a SocialAccount (one per page)
              b. Store the page's own access token in SocialAccountCredential
                 (page tokens don't expire while the user token is valid)
        7.  Delete the used OAuthState record
        8.  Redirect to social accounts list with success/info message

    Why page tokens?
        Publishing to a Facebook Page requires the Page's own access token,
        not the user token. We get page tokens as part of the /me/accounts
        response and store them per-page so we can publish later without
        making the user re-authenticate.

    Error cases all redirect safely to social_account_list with a message.
    """
    # ── Housekeeping: delete stale state records ──────────────────────────────
    fb_cleanup_expired_states()

    code  = request.GET.get("code",  "").strip()
    state = request.GET.get("state", "").strip()
    error = request.GET.get("error", "").strip()

    # ── Facebook returned an error or user clicked Cancel ─────────────────────
    if error:
        error_desc = request.GET.get("error_description", error)
        messages.warning(request, f"Facebook connection cancelled: {error_desc}")
        return redirect("posts:social_account_list")

    if not code or not state:
        messages.error(request, "Invalid callback — missing code or state parameter.")
        return redirect("posts:social_account_list")

    # ── CSRF state verification ───────────────────────────────────────────────
    try:
        oauth_state = OAuthState.objects.select_related("platform").get(
            state = state,
            user  = request.user,
        )
    except OAuthState.DoesNotExist:
        messages.error(
            request,
            "Invalid or expired authorization token. Please try connecting again."
        )
        return redirect("posts:social_account_list")

    if fb_is_state_expired(oauth_state):
        oauth_state.delete()
        messages.error(
            request,
            "Authorization timed out (> 10 minutes). Please try connecting again."
        )
        return redirect("posts:social_account_list")

    platform = oauth_state.platform
    oauth_state.delete()   # one-time use — delete immediately

    # ── Exchange code for short-lived user token ──────────────────────────────
    log.info("Facebook callback reached: code_present=%s state_present=%s user=%s",
             bool(code), bool(state), request.user.pk)
    try:
        short_token_data = fb_exchange_code_for_token(request, code)
    except FacebookOAuthError as exc:
        log.error("Facebook token exchange failed user=%s error=%s", request.user.pk, exc)
        messages.error(request, f"Could not connect Facebook: {exc}")
        return redirect("posts:social_account_list")

    short_lived_token = short_token_data["access_token"]

    # ── Exchange for long-lived token (~60 days) ──────────────────────────────
    try:
        long_token_data = fb_exchange_for_long_lived_token(short_lived_token)
    except FacebookOAuthError as exc:
        log.warning(
            "Facebook long-lived token exchange failed user=%s: %s",
            request.user.pk, exc,
        )
        messages.error(request, f"Could not extend Facebook token: {exc}")
        return redirect("posts:social_account_list")

    long_lived_token = long_token_data["access_token"]
    ll_expires_in    = long_token_data.get("expires_in")   # seconds, ~5184000 (~60 days)

    ll_expires_at = None
    if ll_expires_in:
        try:
            ll_expires_at = timezone.now() + timezone.timedelta(seconds=int(ll_expires_in))
        except (ValueError, TypeError):
            pass

    # ── Fetch user's managed Facebook Pages ───────────────────────────────────
    try:
        pages = fetch_facebook_pages(long_lived_token)
    except FacebookOAuthError as exc:
        log.warning(
            "Facebook pages fetch failed user=%s: %s", request.user.pk, exc,
        )
        messages.error(
            request,
            f"Connected but could not retrieve your Facebook Pages: {exc}"
        )
        return redirect("posts:social_account_list")

    if not pages:
        # No pages found — user may not manage any pages or permissions weren't granted
        messages.warning(
            request,
            "No Facebook Pages found. Make sure you have at least one Page "
            "where you are an Admin or Editor, then try connecting again."
        )
        return redirect("posts:social_account_list")

    # ── Create / reactivate one SocialAccount per Page ────────────────────────
    connected_count   = 0
    reconnected_count = 0

    for page in pages:
        page_id          = page.get("id", "")
        page_name        = page.get("name", f"Facebook Page ({page_id})")
        page_token       = page.get("access_token", "")

        if not page_id or not page_token:
            log.warning(
                "Skipping Facebook page with missing id or token: %s", page
            )
            continue

        # Page tokens don't expire while the long-lived user token is valid.
        # We still store the same expiry as the user token as a conservative estimate.
        account, created = SocialAccount.objects.get_or_create(
            user                = request.user,
            platform            = platform,
            external_account_id = page_id,
            defaults={
                "account_label": page_name,
                "status":        SocialAccount.Status.ACTIVE,
            },
        )
        if not created:
            account.status        = SocialAccount.Status.ACTIVE
            account.account_label = page_name
            account.save(update_fields=["status", "account_label", "updated_at"])

        # Store the Page access token (used for publishing to this page)
        SocialAccountCredential.objects.update_or_create(
            social_account = account,
            defaults={
                "token_type":    "Bearer",
                "access_token":  page_token,
                "refresh_token": "",          # Facebook page tokens have no refresh token
                "scope":         getattr(settings, "FACEBOOK_SCOPES", ""),
                "expires_at":    ll_expires_at,
            },
        )

        if created:
            connected_count += 1
        else:
            reconnected_count += 1

        log.info(
            "Facebook OAuth page: user=%s account=%s page_id=%s action=%s",
            request.user.pk, account.pk, page_id,
            "connected" if created else "reconnected",
        )

    # ── Summary message ───────────────────────────────────────────────────────
    parts = []
    if connected_count:
        parts.append(
            f"{connected_count} page{'s' if connected_count != 1 else ''} connected"
        )
    if reconnected_count:
        parts.append(
            f"{reconnected_count} page{'s' if reconnected_count != 1 else ''} reconnected"
        )

    if parts:
        messages.success(
            request,
            "Facebook: " + ", ".join(parts) + "."
        )
    else:
        messages.warning(
            request,
            "Facebook pages were found but none could be saved. Check logs."
        )

    return redirect("posts:social_account_list")


# ══════════════════════════════════════════════════════════════════════════════
#  LINKEDIN PAGE OAUTH
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_connect_social_accounts")
def linkedin_page_connect(request):
    """
    Step 1 — Redirect the user to LinkedIn's authorization page (Page app).

    Uses a separate LinkedIn Developer App with Marketing Developer Platform
    access. Generates a state token, saves it to OAuthState, then redirects.
    """
    from django.conf import settings as django_settings
    if not getattr(django_settings, "LINKEDIN_PAGE_CLIENT_ID", "").strip():
        messages.error(
            request,
            "LinkedIn Page app is not configured. "
            "Set LINKEDIN_PAGE_CLIENT_ID and LINKEDIN_PAGE_CLIENT_SECRET in .env."
        )
        return redirect("posts:social_account_list")

    try:
        linkedin_page_platform = Platform.objects.get(code="linkedin_page")
    except Platform.DoesNotExist:
        messages.error(
            request,
            "LinkedIn Page platform is not in the database. "
            "Run the seeder or add it manually."
        )
        return redirect("posts:social_account_list")

    auth_url, state = lip_build_authorization_url(request)

    OAuthState.objects.create(
        user     = request.user,
        state    = state,
        platform = linkedin_page_platform,
    )

    return redirect(auth_url)


@rbac_permission_required("can_connect_social_accounts")
def linkedin_page_callback(request):
    """
    Step 2 — LinkedIn redirects back here with ?code=...&state=...

    Flow:
        1.  Clean up expired OAuthState records
        2.  Verify state (CSRF check)
        3.  Exchange code for access token
        4.  Fetch organization pages the member admins
        5.  For each page: create/reactivate SocialAccount + store credential
        6.  Delete the used OAuthState record
        7.  Redirect with summary message

    One SocialAccount is created per managed page (same pattern as Facebook).
    The external_account_id stores the numeric org ID.
    Publishing uses urn:li:organization:{org_id} as the UGC post author.
    """
    lip_cleanup_expired_states()

    code  = request.GET.get("code",  "").strip()
    state = request.GET.get("state", "").strip()
    error = request.GET.get("error", "").strip()

    if error:
        error_desc = request.GET.get("error_description", error)
        messages.warning(request, f"LinkedIn Page connection cancelled: {error_desc}")
        return redirect("posts:social_account_list")

    if not code or not state:
        messages.error(request, "Invalid callback — missing code or state.")
        return redirect("posts:social_account_list")

    # ── CSRF state verification ───────────────────────────────────────────────
    try:
        oauth_state = OAuthState.objects.select_related("platform").get(
            state = state,
            user  = request.user,
        )
    except OAuthState.DoesNotExist:
        messages.error(
            request,
            "Invalid or expired authorization token. Please try connecting again."
        )
        return redirect("posts:social_account_list")

    if lip_is_state_expired(oauth_state):
        oauth_state.delete()
        messages.error(
            request,
            "Authorization timed out (> 10 minutes). Please try connecting again."
        )
        return redirect("posts:social_account_list")

    platform = oauth_state.platform
    oauth_state.delete()

    # ── Exchange code for access token ────────────────────────────────────────
    try:
        token_data = lip_exchange_code_for_token(request, code)
    except LinkedInPageOAuthError as exc:
        log.warning("LinkedIn Page token exchange failed user=%s: %s", request.user.pk, exc)
        messages.error(request, f"Could not connect LinkedIn Page: {exc}")
        return redirect("posts:social_account_list")

    access_token = token_data["access_token"]
    expires_in   = token_data.get("expires_in")
    scope        = token_data.get("scope", "")

    expires_at = None
    if expires_in:
        try:
            expires_at = timezone.now() + timezone.timedelta(seconds=int(expires_in))
        except (ValueError, TypeError):
            pass

    # ── Fetch member profile (for labeling only — non-fatal if fails) ─────────
    member_profile = lip_fetch_member_profile(access_token)
    member_name    = (
        member_profile.get("name") or
        member_profile.get("given_name") or
        "LinkedIn User"
    )

    # ── Fetch managed org pages ───────────────────────────────────────────────
    try:
        pages = lip_fetch_linkedin_pages(access_token)
    except LinkedInPageOAuthError as exc:
        log.warning(
            "LinkedIn Page ACLs fetch failed user=%s: %s", request.user.pk, exc
        )
        messages.error(
            request,
            f"Connected but could not retrieve LinkedIn Pages: {exc}"
        )
        return redirect("posts:social_account_list")

    if not pages:
        messages.warning(
            request,
            "No LinkedIn Pages found. Make sure you are an Administrator on at least "
            "one LinkedIn Company Page, then try connecting again."
        )
        return redirect("posts:social_account_list")

    # ── Create / reactivate one SocialAccount per org page ────────────────────
    connected_count   = 0
    reconnected_count = 0

    for page in pages:
        org_id   = page["org_id"]
        org_name = page["name"]

        account, created = SocialAccount.objects.get_or_create(
            user                = request.user,
            platform            = platform,
            external_account_id = org_id,
            defaults={
                "account_label": org_name,
                "status":        SocialAccount.Status.ACTIVE,
            },
        )
        if not created:
            account.status        = SocialAccount.Status.ACTIVE
            account.account_label = org_name
            account.save(update_fields=["status", "account_label", "updated_at"])

        SocialAccountCredential.objects.update_or_create(
            social_account = account,
            defaults={
                "token_type":    token_data.get("token_type", "Bearer"),
                "access_token":  access_token,
                "refresh_token": token_data.get("refresh_token", ""),
                "scope":         scope,
                "expires_at":    expires_at,
            },
        )

        if created:
            connected_count += 1
        else:
            reconnected_count += 1

        log.info(
            "LinkedIn Page OAuth: user=%s account=%s org_id=%s action=%s",
            request.user.pk, account.pk, org_id,
            "connected" if created else "reconnected",
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    parts = []
    if connected_count:
        parts.append(f"{connected_count} page{'s' if connected_count != 1 else ''} connected")
    if reconnected_count:
        parts.append(f"{reconnected_count} page{'s' if reconnected_count != 1 else ''} reconnected")

    if parts:
        messages.success(request, "LinkedIn Pages: " + ", ".join(parts) + ".")
    else:
        messages.warning(request, "LinkedIn Pages were found but none could be saved.")

    return redirect("posts:social_account_list")


# ══════════════════════════════════════════════════════════════════════════════
#  INSTAGRAM OAUTH
# ══════════════════════════════════════════════════════════════════════════════

@rbac_permission_required("can_connect_social_accounts")
def instagram_connect(request):
    """
    Step 1 — Redirect user to Facebook Login dialog requesting Instagram permissions.

    Uses the same Facebook App (FACEBOOK_APP_ID/SECRET) but with Instagram
    scopes: instagram_basic, instagram_content_publishing, etc.
    """
    try:
        instagram_platform = Platform.objects.get(code="instagram")
    except Platform.DoesNotExist:
        messages.error(
            request,
            "Instagram platform is not configured. Run the seeder first."
        )
        return redirect("posts:social_account_list")

    auth_url, state = ig_build_authorization_url(request)

    OAuthState.objects.create(
        user     = request.user,
        state    = state,
        platform = instagram_platform,
    )

    return redirect(auth_url)


@rbac_permission_required("can_connect_social_accounts")
def instagram_callback(request):
    """
    Step 2 — Facebook redirects back with ?code=...&state=...

    Flow:
        1.  Verify state (CSRF check)
        2.  Exchange code for short-lived user token
        3.  Exchange for long-lived token (~60 days)
        4.  Fetch Facebook Pages → find linked Instagram Business accounts
        5.  For each Instagram account: create/reactivate SocialAccount
            and store the Page access token as the credential
        6.  Redirect with summary message

    The credential stored is the Facebook Page token (not the user token)
    because the Instagram Content Publishing API requires the Page token.
    The external_account_id is the Instagram Account ID (e.g. "17841400000000000").
    """
    ig_cleanup_expired_states()

    code  = request.GET.get("code",  "").strip()
    state = request.GET.get("state", "").strip()
    error = request.GET.get("error", "").strip()

    if error:
        error_desc = request.GET.get("error_description", error)
        messages.warning(request, f"Instagram connection cancelled: {error_desc}")
        return redirect("posts:social_account_list")

    if not code or not state:
        messages.error(request, "Invalid callback — missing code or state.")
        return redirect("posts:social_account_list")

    # ── CSRF state verification ───────────────────────────────────────────────
    try:
        oauth_state = OAuthState.objects.select_related("platform").get(
            state = state,
            user  = request.user,
        )
    except OAuthState.DoesNotExist:
        messages.error(
            request,
            "Invalid or expired authorization token. Please try connecting again."
        )
        return redirect("posts:social_account_list")

    if ig_is_state_expired(oauth_state):
        oauth_state.delete()
        messages.error(
            request,
            "Authorization timed out (> 10 minutes). Please try connecting again."
        )
        return redirect("posts:social_account_list")

    platform = oauth_state.platform
    oauth_state.delete()

    # ── Exchange code for short-lived token ───────────────────────────────────
    try:
        short_token_data = ig_exchange_code_for_token(request, code)
    except InstagramOAuthError as exc:
        log.warning("Instagram token exchange failed user=%s: %s", request.user.pk, exc)
        messages.error(request, f"Could not connect Instagram: {exc}")
        return redirect("posts:social_account_list")

    short_lived_token = short_token_data["access_token"]

    # ── Exchange for long-lived token (~60 days) ──────────────────────────────
    try:
        long_token_data = ig_exchange_for_long_lived_token(short_lived_token)
    except InstagramOAuthError as exc:
        log.warning(
            "Instagram long-lived token exchange failed user=%s: %s",
            request.user.pk, exc,
        )
        messages.error(request, f"Could not extend Instagram token: {exc}")
        return redirect("posts:social_account_list")

    long_lived_token = long_token_data["access_token"]
    ll_expires_in    = long_token_data.get("expires_in")

    ll_expires_at = None
    if ll_expires_in:
        try:
            ll_expires_at = timezone.now() + timezone.timedelta(seconds=int(ll_expires_in))
        except (ValueError, TypeError):
            pass

    # ── Fetch Instagram accounts linked to Facebook Pages ─────────────────────
    try:
        ig_accounts = fetch_instagram_accounts(long_lived_token)
    except InstagramOAuthError as exc:
        log.warning(
            "Instagram accounts fetch failed user=%s: %s", request.user.pk, exc
        )
        messages.error(
            request,
            f"Connected but could not retrieve Instagram accounts: {exc}"
        )
        return redirect("posts:social_account_list")

    if not ig_accounts:
        messages.warning(
            request,
            "No Instagram Business or Creator accounts found. Make sure your "
            "Instagram account is a Professional account and is linked to a "
            "Facebook Page, then try connecting again."
        )
        return redirect("posts:social_account_list")

    # ── Create / reactivate one SocialAccount per Instagram account ───────────
    connected_count   = 0
    reconnected_count = 0

    for ig in ig_accounts:
        ig_id      = ig["ig_account_id"]
        ig_name    = ig.get("name") or ig.get("username") or f"Instagram ({ig_id})"
        page_token = ig["page_token"]   # Page token — used for publishing

        account, created = SocialAccount.objects.get_or_create(
            user                = request.user,
            platform            = platform,
            external_account_id = ig_id,
            defaults={
                "account_label": ig_name,
                "status":        SocialAccount.Status.ACTIVE,
            },
        )
        if not created:
            account.status        = SocialAccount.Status.ACTIVE
            account.account_label = ig_name
            account.save(update_fields=["status", "account_label", "updated_at"])

        # Store the Page access token (required for Content Publishing API)
        SocialAccountCredential.objects.update_or_create(
            social_account = account,
            defaults={
                "token_type":    "Bearer",
                "access_token":  page_token,
                "refresh_token": "",
                "scope":         getattr(settings, "INSTAGRAM_SCOPES", ""),
                "expires_at":    ll_expires_at,
            },
        )

        if created:
            connected_count += 1
        else:
            reconnected_count += 1

        log.info(
            "Instagram OAuth: user=%s account=%s ig_id=%s action=%s",
            request.user.pk, account.pk, ig_id,
            "connected" if created else "reconnected",
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    parts = []
    if connected_count:
        parts.append(
            f"{connected_count} account{'s' if connected_count != 1 else ''} connected"
        )
    if reconnected_count:
        parts.append(
            f"{reconnected_count} account{'s' if reconnected_count != 1 else ''} reconnected"
        )

    if parts:
        messages.success(request, "Instagram: " + ", ".join(parts) + ".")
    else:
        messages.warning(request, "Instagram accounts were found but none could be saved.")

    return redirect("posts:social_account_list")
