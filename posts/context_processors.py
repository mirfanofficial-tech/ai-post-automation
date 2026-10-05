"""
Context processor for the posts module.

Injects post summary counts into every template so the dashboard
and sidebar can show live numbers without each view having to query manually.

Available in all templates:
    posts_draft_count        – number of draft posts
    posts_scheduled_count    – number of scheduled posts
    posts_published_count    – number of published posts
    posts_total_count        – total posts
    posts_social_account_count – number of connected social accounts
"""

def posts_stats(request):
    if not request.user.is_authenticated:
        return {}

    try:
        from .models import Post, SocialAccount

        # Only count posts created by this user unless they have management perms
        try:
            user_perms = frozenset(
                request.user.profile.role.permissions.values_list("codename", flat=True)
            ) if request.user.profile.role else frozenset()
        except Exception:
            user_perms = frozenset()

        # Admins see all posts; others see only their own
        if "can_view_posts" in user_perms:
            qs = Post.objects
        else:
            return {}

        return {
            "posts_draft_count":          qs.filter(status=Post.Status.DRAFT).count(),
            "posts_scheduled_count":      qs.filter(status=Post.Status.SCHEDULED).count(),
            "posts_published_count":      qs.filter(status=Post.Status.PUBLISHED).count(),
            "posts_total_count":          qs.count(),
            "posts_social_account_count": SocialAccount.objects.filter(
                user=request.user,
                status=SocialAccount.Status.ACTIVE,
            ).count(),
        }
    except Exception:
        return {}
