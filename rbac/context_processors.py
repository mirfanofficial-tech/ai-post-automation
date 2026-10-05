"""
Context processors for RBAC.

Injects into every template context:
  rbac_user_count       – total User count
  rbac_role_count       – total Role count
  rbac_permission_count – total Permission count
  user_permissions      – frozenset of permission codenames the current user holds

Template usage::

  {% if "can_create_users" in user_permissions %}
    <a href="...">Add User</a>
  {% endif %}
"""

from django.contrib.auth import get_user_model


def rbac_stats(request):
    if not request.user.is_authenticated:
        return {"user_permissions": frozenset()}

    try:
        from .models import Permission, Role

        User = get_user_model()

        # Collect this user's permission codenames
        perms: frozenset = frozenset()
        try:
            role = request.user.profile.role
            if role is not None:
                perms = frozenset(
                    role.permissions.values_list("codename", flat=True)
                )
        except Exception:
            pass

        return {
            "rbac_user_count":       User.objects.count(),
            "rbac_role_count":       Role.objects.count(),
            "rbac_permission_count": Permission.objects.count(),
            "user_permissions":      perms,
        }
    except Exception:
        return {
            "rbac_user_count":       0,
            "rbac_role_count":       0,
            "rbac_permission_count": 0,
            "user_permissions":      frozenset(),
        }
