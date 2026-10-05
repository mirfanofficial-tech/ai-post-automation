from django.contrib import admin
from .models import Permission, Role, UserProfile


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("name", "codename", "description", "created_at")
    search_fields = ("name", "codename")
    prepopulated_fields = {"codename": ("name",)}
    ordering = ("name",)


class PermissionInline(admin.TabularInline):
    model = Role.permissions.through
    extra = 1
    verbose_name = "Permission"
    verbose_name_plural = "Permissions"


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "permission_count", "user_count", "created_at")
    search_fields = ("name",)
    filter_horizontal = ("permissions",)
    ordering = ("name",)

    @admin.display(description="Permissions")
    def permission_count(self, obj):
        return obj.permission_count

    @admin.display(description="Users")
    def user_count(self, obj):
        return obj.user_count


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ("user",)
    ordering = ("user__username",)
