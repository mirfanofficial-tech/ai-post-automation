from django.urls import path
from . import views

app_name = "rbac"

urlpatterns = [
    # ── Users ──────────────────────────────────────────────
    path("users/",              views.user_list,   name="user_list"),
    path("users/create/",       views.user_create, name="user_create"),
    path("users/<int:pk>/",     views.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/",views.user_edit,   name="user_edit"),
    path("users/<int:pk>/delete/", views.user_delete, name="user_delete"),

    # ── Roles ───────────────────────────────────────────────
    path("roles/",              views.role_list,   name="role_list"),
    path("roles/create/",       views.role_create, name="role_create"),
    path("roles/<int:pk>/",     views.role_detail, name="role_detail"),
    path("roles/<int:pk>/edit/",views.role_edit,   name="role_edit"),
    path("roles/<int:pk>/delete/", views.role_delete, name="role_delete"),

    # ── Permissions ─────────────────────────────────────────
    path("permissions/",              views.permission_list,   name="permission_list"),
    path("permissions/create/",       views.permission_create, name="permission_create"),
    path("permissions/<int:pk>/edit/",views.permission_edit,   name="permission_edit"),
    path("permissions/<int:pk>/delete/", views.permission_delete, name="permission_delete"),
]
