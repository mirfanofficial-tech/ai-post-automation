"""
Background Tasks — Phase 5-7 Enhancements

Asynchronous task processing for:
  • Publishing posts (prevents HTTP timeout)
  • Processing scheduled posts (cron-like scheduler)
  • Automatic retries

Uses Django-Q for task queue management.

Start worker:   python manage.py qcluster
Monitor tasks:  python manage.py qmonitor
Schedule setup: python manage.py qschedule
"""

import logging
from datetime import timedelta

from django.utils import timezone
from django_q.models import Schedule
from django_q.tasks import async_task, schedule

from .models import Post, PostTarget, SocialAccount
from .services.publishing_manager import publish_post as svc_publish_post

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLISHING TASKS
# ══════════════════════════════════════════════════════════════════════════════

def publish_post_task(post_id: int, social_account_id: int):
    """
    Background task: Publish a post to a social account.
    
    This is the async version of the publishing tool.
    Called from views via async_task() to prevent HTTP blocking.
    
    Parameters:
        post_id           Post to publish
        social_account_id Target social account
    
    Returns:
        dict with result (logged by Django-Q)
    """
    log.info(f"[Task] Publishing Post #{post_id} → Account #{social_account_id}")
    
    result = svc_publish_post(
        post_id=post_id,
        social_account_id=social_account_id,
    )
    
    if result.get("ok"):
        log.info(
            f"[Task] ✓ Published Post #{post_id} → Account #{social_account_id} → "
            f"Status: {result.get('status')}"
        )
    else:
        log.warning(
            f"[Task] ✗ Failed Post #{post_id} → Account #{social_account_id}: "
            f"{result.get('error') or result.get('message')}"
        )
    
    return result


def schedule_post_task(post_id: int, social_account_id: int, scheduled_at):
    """
    Schedule a post for future publishing.
    
    Creates a PostTarget with scheduled_at, then schedules a Django-Q task
    to execute publish_post_task at the specified time.
    
    Parameters:
        post_id           Post to schedule
        social_account_id Target account
        scheduled_at      When to publish (datetime object)
    """
    from .services.publishing_manager import schedule_post as svc_schedule_post
    
    # Create PostTarget with SCHEDULED status
    result = svc_schedule_post(
        post_id=post_id,
        social_account_id=social_account_id,
        scheduled_at=scheduled_at,
    )
    
    if not result.get("ok"):
        log.error(f"Failed to schedule post: {result.get('error')}")
        return result
    
    target_id = result.get("target_id")
    
    # Schedule background task to execute at scheduled_at
    task_name = f"publish_scheduled_post_{target_id}"
    
    # Remove any existing schedule for this target (avoid duplicates)
    Schedule.objects.filter(name=task_name).delete()
    
    # Create new schedule
    schedule(
        "posts.tasks.publish_post_task",
        post_id,
        social_account_id,
        name=task_name,
        schedule_type=Schedule.ONCE,
        next_run=scheduled_at,
    )
    
    log.info(
        f"[Task] Scheduled Post #{post_id} → Account #{social_account_id} "
        f"for {scheduled_at.isoformat()}"
    )
    
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  SCHEDULED POST PROCESSOR (Cron Job)
# ══════════════════════════════════════════════════════════════════════════════

def process_scheduled_posts():
    """
    CRITICAL TASK: Process all PostTargets with scheduled_at <= now().
    
    This is the heart of Phase 6 scheduling functionality.
    Should run every minute via Django-Q scheduler.
    
    Setup (run once):
        python manage.py shell
        >>> from posts.tasks import setup_scheduled_processor
        >>> setup_scheduled_processor()
    
    Or manually:
        python manage.py qschedule --create --func=posts.tasks.process_scheduled_posts --minutes=1
    """
    now = timezone.now()
    
    # Find all targets that are scheduled to publish now or in the past
    targets = PostTarget.objects.filter(
        status=PostTarget.Status.SCHEDULED,
        scheduled_at__lte=now,
    ).select_related("post", "social_account")
    
    count = targets.count()
    if count == 0:
        log.debug("[Scheduler] No scheduled posts due for publishing")
        return {"processed": 0}
    
    log.info(f"[Scheduler] Found {count} scheduled post(s) due for publishing")
    
    for target in targets:
        log.info(
            f"[Scheduler] Processing PostTarget #{target.pk}: "
            f"Post #{target.post_id} → Account #{target.social_account_id}"
        )
        
        # Update status to PUBLISHING to prevent duplicate processing
        target.status = PostTarget.Status.PUBLISHING
        target.save(update_fields=["status", "updated_at"])
        
        # Queue async publishing task
        async_task(
            "posts.tasks.publish_post_task",
            target.post_id,
            target.social_account_id,
            task_name=f"scheduled_publish_{target.pk}",
        )
    
    log.info(f"[Scheduler] Queued {count} scheduled post(s) for publishing")
    return {"processed": count}


# ══════════════════════════════════════════════════════════════════════════════
#  AUTOMATIC RETRY PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════

def process_retries():
    """
    Process PostTargets in RETRYING status where next_retry_at <= now().
    
    Complements the scheduled post processor for retry handling.
    Should run every minute.
    
    Setup (run once):
        python manage.py shell
        >>> from posts.tasks import setup_retry_processor
        >>> setup_retry_processor()
    """
    now = timezone.now()
    
    # Find all targets ready for retry
    targets = PostTarget.objects.filter(
        status=PostTarget.Status.RETRYING,
        next_retry_at__lte=now,
    ).select_related("post", "social_account")
    
    count = targets.count()
    if count == 0:
        log.debug("[Retry] No posts due for retry")
        return {"retried": 0}
    
    log.info(f"[Retry] Found {count} post(s) ready for retry")
    
    for target in targets:
        log.info(
            f"[Retry] Processing PostTarget #{target.pk} "
            f"(Attempt {target.attempt_count + 1}/4)"
        )
        
        # Update to PUBLISHING to prevent duplicate retry
        target.status = PostTarget.Status.PUBLISHING
        target.next_retry_at = None
        target.save(update_fields=["status", "next_retry_at", "updated_at"])
        
        # Queue retry task
        async_task(
            "posts.tasks.publish_post_task",
            target.post_id,
            target.social_account_id,
            task_name=f"retry_publish_{target.pk}",
        )
    
    log.info(f"[Retry] Queued {count} post(s) for retry")
    return {"retried": count}


# ══════════════════════════════════════════════════════════════════════════════
#  SCHEDULER SETUP HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def setup_scheduled_processor():
    """
    One-time setup: Create recurring schedule for process_scheduled_posts().
    
    Run this after deploying to production:
        python manage.py shell
        >>> from posts.tasks import setup_scheduled_processor
        >>> setup_scheduled_processor()
    """
    # Remove old schedules
    Schedule.objects.filter(
        func="posts.tasks.process_scheduled_posts"
    ).delete()
    
    # Create new minute-by-minute schedule
    schedule(
        "posts.tasks.process_scheduled_posts",
        name="process_scheduled_posts",
        schedule_type=Schedule.MINUTES,
        minutes=1,
        repeats=-1,  # infinite
    )
    
    log.info("✓ Scheduled post processor configured (runs every minute)")
    return "✓ Setup complete"


def setup_retry_processor():
    """
    One-time setup: Create recurring schedule for process_retries().
    
    Run this after deploying:
        python manage.py shell
        >>> from posts.tasks import setup_retry_processor
        >>> setup_retry_processor()
    """
    # Remove old schedules
    Schedule.objects.filter(
        func="posts.tasks.process_retries"
    ).delete()
    
    # Create new schedule
    schedule(
        "posts.tasks.process_retries",
        name="process_retries",
        schedule_type=Schedule.MINUTES,
        minutes=1,
        repeats=-1,
    )
    
    log.info("✓ Retry processor configured (runs every minute)")
    return "✓ Setup complete"


def setup_all_schedulers():
    """
    Convenience: Set up all background schedulers at once.
    
    Usage:
        python manage.py shell
        >>> from posts.tasks import setup_all_schedulers
        >>> setup_all_schedulers()
    """
    setup_scheduled_processor()
    setup_retry_processor()
    log.info("✓ All schedulers configured")
    print("""
╔══════════════════════════════════════════════════════════╗
║  Django-Q Schedulers Configured                          ║
╠══════════════════════════════════════════════════════════╣
║  ✓ Scheduled post processor (every minute)               ║
║  ✓ Retry processor (every minute)                        ║
║                                                          ║
║  Start worker:   python manage.py qcluster              ║
║  Monitor:        python manage.py qmonitor              ║
║  View schedules: python manage.py qschedule             ║
╚══════════════════════════════════════════════════════════╝
    """)
    return "✓ All schedulers ready"
