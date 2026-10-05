"""
RBAC management views — with full server-side authorization enforcement.

Permission map
──────────────
Users
  GET  /users/              can_view_users
  GET  /users/<pk>/         can_view_users
  GET  /users/create/       can_create_users
  POST /users/create/       can_create_users
  GET  /users/<pk>/edit/    can_edit_users
  POST /users/<pk>/edit/    can_edit_users
  POST /users/<pk>/delete/  can_delete_users

Roles
  GET  /roles/              can_view_roles
  GET  /roles/<pk>/         can_view_roles
  GET  /roles/create/       can_manage_roles
  POST /roles/create/       can_manage_roles
  GET  /roles/<pk>/edit/    can_manage_roles
  POST /roles/<pk>/edit/    can_manage_roles
  POST /roles/<pk>/delete/  can_manage_roles

Permissions
  GET  /permissions/              can_view_permissions
  GET  /permissions/create/       can_manage_permissions
  POST /permissions/create/       can_manage_permissions
  GET  /permissions/<pk>/edit/    can_manage_permissions
  POST /permissions/<pk>/edit/    can_manage_permissions
  POST /permissions/<pk>/delete/  can_manage_permissions
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .decorators import rbac_permission_required
from .forms import (
    PermissionForm,
    RoleForm,
    UserCreateForm,
    UserEditForm,
)
from .models import Permission, Role, UserProfile

User = get_user_model()


# ===================================================================
#  USERS
# ===================================================================

@rbac_permission_required("can_view_users")
def user_list(request):
    users = (
        User.objects
        .select_related("profile__role")
        .order_by("username")
    )
    return render(request, "rbac/users/list.html", {"users": users})


@rbac_permission_required("can_view_users")
def user_detail(request, pk):
    user = get_object_or_404(
        User.objects.select_related("profile__role"),
        pk=pk,
    )
    permissions = []
    role = None
    try:
        role = user.profile.role
        if role:
            permissions = list(role.permissions.all())
    except UserProfile.DoesNotExist:
        pass
    return render(request, "rbac/users/detail.html", {
        "object": user,
        "permissions": permissions,
        "role": role,
    })


@rbac_permission_required("can_create_users")
def user_create(request):
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f'User "{user.username}" created successfully.')
            return redirect("rbac:user_list")
    else:
        form = UserCreateForm()
    return render(request, "rbac/users/form.html", {"form": form, "action": "Create"})


@rbac_permission_required("can_edit_users")
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f'User "{user.username}" updated successfully.')
            return redirect("rbac:user_list")
    else:
        form = UserEditForm(instance=user)
    return render(request, "rbac/users/form.html", {
        "form": form,
        "action": "Edit",
        "object": user,
    })


@rbac_permission_required("can_delete_users")
@require_http_methods(["POST"])
def user_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect("rbac:user_list")
    username = user.username
    user.delete()
    messages.success(request, f'User "{username}" deleted.')
    return redirect("rbac:user_list")


# ===================================================================
#  ROLES
# ===================================================================

@rbac_permission_required("can_view_roles")
def role_list(request):
    roles = Role.objects.prefetch_related("permissions", "user_profiles").order_by("name")
    return render(request, "rbac/roles/list.html", {"roles": roles})


@rbac_permission_required("can_view_roles")
def role_detail(request, pk):
    role = get_object_or_404(
        Role.objects.prefetch_related("permissions", "user_profiles__user"),
        pk=pk,
    )
    return render(request, "rbac/roles/detail.html", {"object": role})


@rbac_permission_required("can_manage_roles")
def role_create(request):
    if request.method == "POST":
        form = RoleForm(request.POST)
        if form.is_valid():
            role = form.save()
            messages.success(request, f'Role "{role.name}" created successfully.')
            return redirect("rbac:role_list")
    else:
        form = RoleForm()
    return render(request, "rbac/roles/form.html", {"form": form, "action": "Create"})


@rbac_permission_required("can_manage_roles")
def role_edit(request, pk):
    role = get_object_or_404(Role, pk=pk)
    if request.method == "POST":
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            form.save()
            messages.success(request, f'Role "{role.name}" updated successfully.')
            return redirect("rbac:role_list")
    else:
        form = RoleForm(instance=role)
    return render(request, "rbac/roles/form.html", {
        "form": form,
        "action": "Edit",
        "object": role,
    })


@rbac_permission_required("can_manage_roles")
@require_http_methods(["POST"])
def role_delete(request, pk):
    role = get_object_or_404(Role, pk=pk)
    name = role.name
    role.delete()
    messages.success(request, f'Role "{name}" deleted.')
    return redirect("rbac:role_list")


# ===================================================================
#  PERMISSIONS
# ===================================================================

@rbac_permission_required("can_view_permissions")
def permission_list(request):
    permissions = Permission.objects.prefetch_related("roles").order_by("name")
    return render(request, "rbac/permissions/list.html", {"permissions": permissions})


@rbac_permission_required("can_manage_permissions")
def permission_create(request):
    if request.method == "POST":
        form = PermissionForm(request.POST)
        if form.is_valid():
            perm = form.save()
            messages.success(request, f'Permission "{perm.name}" created successfully.')
            return redirect("rbac:permission_list")
    else:
        form = PermissionForm()
    return render(request, "rbac/permissions/form.html", {"form": form, "action": "Create"})


@rbac_permission_required("can_manage_permissions")
def permission_edit(request, pk):
    perm = get_object_or_404(Permission, pk=pk)
    if request.method == "POST":
        form = PermissionForm(request.POST, instance=perm)
        if form.is_valid():
            form.save()
            messages.success(request, f'Permission "{perm.name}" updated.')
            return redirect("rbac:permission_list")
    else:
        form = PermissionForm(instance=perm)
    return render(request, "rbac/permissions/form.html", {
        "form": form,
        "action": "Edit",
        "object": perm,
    })


@rbac_permission_required("can_manage_permissions")
@require_http_methods(["POST"])
def permission_delete(request, pk):
    perm = get_object_or_404(Permission, pk=pk)
    name = perm.name
    perm.delete()
    messages.success(request, f'Permission "{name}" deleted.')
    return redirect("rbac:permission_list")
