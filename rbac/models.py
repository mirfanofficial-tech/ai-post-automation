"""
RBAC data models.

Structure
─────────
Permission   – a named capability (e.g. "can_view_users")
Role         – a named role that carries zero-or-more Permissions
UserProfile  – extends Django's User 1-to-1; assigns exactly one Role
"""

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Permission(models.Model):
    """A single application capability / action."""

    name = models.CharField(max_length=100, unique=True)
    codename = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return self.name


class Role(models.Model):
    """A role groups permissions and is assigned to users."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name="roles",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Role"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.name

    @property
    def permission_count(self):
        return self.permissions.count()

    @property
    def user_count(self):
        return self.user_profiles.count()


class UserProfile(models.Model):
    """Extends the built-in User with exactly one Role."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_profiles",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self):
        return f"{self.user.username} — {self.role or 'No role'}"

    def has_permission(self, codename: str) -> bool:
        """Return True if this user's role grants the given permission codename."""
        if self.role is None:
            return False
        return self.role.permissions.filter(codename=codename).exists()
