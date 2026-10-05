"""
Root URL configuration.

Pattern:
  /           → redirects to /dashboard/
  /dashboard/ → core app dashboard
  /admin/     → Django admin
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Redirect root to dashboard
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),

    # Core app (dashboard, auth)
    path("", include("core.urls")),

    # RBAC management
    path("", include("rbac.urls")),

    # Social Publishing
    path("", include("posts.urls")),

    # Django admin
    path("admin/", admin.site.urls),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
