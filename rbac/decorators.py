"""
RBAC authorization decorator.

Usage
─────
from rbac.decorators import rbac_permission_required

@rbac_permission_required("can_view_users")
def my_view(request):
    ...

Behaviour
─────────
- Unauthenticated request  → 302 to LOGIN_URL  (Django @login_required handles this)
- Authenticated, no profile or no role, or missing permission → HTTP 403
- Authenticated + permission present → view executes normally

The decorator stacks cleanly with @require_http_methods.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render


def _forbidden(request):
    """Return a styled 403 response using the project 403 template."""
    return render(request, "403.html", status=403)


def rbac_permission_required(codename: str):
    """
    Decorator factory that gates a view on a single RBAC permission codename.

    Example::

        @rbac_permission_required("can_delete_users")
        def user_delete(request, pk):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required          # unauthenticated → login page
        def _wrapped(request, *args, **kwargs):
            # Resolve profile → role → permissions in one try/except
            try:
                profile = request.user.profile
            except Exception:
                # No profile at all → treat as no permissions
                return _forbidden(request)

            if not profile.has_permission(codename):
                return _forbidden(request)

            return view_func(request, *args, **kwargs)

        return _wrapped
    return decorator
