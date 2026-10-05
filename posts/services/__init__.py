"""
posts.services — Public tool API.

Import from here in views and (future) AI tool adapters:

    from posts.services import publish_post, schedule_post, get_post_status
"""

from .publishing_manager import (  # noqa: F401  (re-export as public API)
    # Post tools
    create_post,
    get_post,
    list_posts,
    update_post,
    delete_post,
    # Social account tools
    list_social_accounts,
    # Publishing tools
    publish_post,
    schedule_post,
    cancel_scheduled_post,
    unpublish_post_targets,
    # Status & log tools
    get_post_status,
    get_post_target_status,
    get_publishing_logs,
    retry_post_target,
)
