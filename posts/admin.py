"""
Admin registration for the Social Publishing module.

Provides full visibility into all tables for debugging and manual data management.
Credentials are deliberately excluded from admin list/detail views.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Platform,
    Post,
    PostMedia,
    PostTarget,
    PostTargetContent,
    PublishingLog,
    SocialAccount,
    SocialAccountCredential,
)


# ─────────────────────────────────────────────────────────
# Platform
# ─────────────────────────────────────────────────────────

@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display  = ["name", "code", "status", "created_at"]
    list_filter   = ["status"]
    search_fields = ["name", "code"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["name"]


# ─────────────────────────────────────────────────────────
# SocialAccount
# ─────────────────────────────────────────────────────────

@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display  = ["account_label", "user", "platform", "status",
                     "external_account_id", "created_at"]
    list_filter   = ["status", "platform"]
    search_fields = ["account_label", "user__username", "external_account_id"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields   = ["user"]


# ─────────────────────────────────────────────────────────
# SocialAccountCredential
# Intentionally restricted — no token values are shown.
# ─────────────────────────────────────────────────────────

@admin.register(SocialAccountCredential)
class SocialAccountCredentialAdmin(admin.ModelAdmin):
    list_display  = ["social_account", "token_type", "expires_at",
                     "token_status", "created_at"]
    readonly_fields = [
        "social_account", "token_type", "scope",
        "expires_at", "created_at", "updated_at",
        # access_token and refresh_token are intentionally hidden
    ]
    search_fields = ["social_account__account_label"]
    # Prevent accidental creation/editing of tokens through admin
    def has_add_permission(self, request):
        return False

    @admin.display(description="Token Status")
    def token_status(self, obj):
        if obj.is_expired:
            return format_html('<span style="color:red;">Expired</span>')
        return format_html('<span style="color:green;">Valid</span>')


# ─────────────────────────────────────────────────────────
# PostMedia (inline)
# ─────────────────────────────────────────────────────────

class PostMediaInline(admin.TabularInline):
    model       = PostMedia
    extra       = 0
    fields      = ["media_type", "mime_type", "file", "file_size",
                   "sort_order", "is_main"]
    readonly_fields = ["file_size"]


# ─────────────────────────────────────────────────────────
# PostTargetContent (inline)
# ─────────────────────────────────────────────────────────

class PostTargetContentInline(admin.StackedInline):
    model  = PostTargetContent
    extra  = 0
    fields = ["title", "content_plain", "content_html", "hashtags"]


# ─────────────────────────────────────────────────────────
# PublishingLog (inline)
# ─────────────────────────────────────────────────────────

class PublishingLogInline(admin.TabularInline):
    model  = PublishingLog
    extra  = 0
    fields = ["attempt_number", "started_at", "ended_at", "response_type",
              "response_message", "status"]
    readonly_fields = ["attempt_number", "started_at", "ended_at",
                       "response_type", "response_message", "status"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────
# PostTarget
# ─────────────────────────────────────────────────────────

@admin.register(PostTarget)
class PostTargetAdmin(admin.ModelAdmin):
    list_display  = ["__str__", "platform_name", "status", "scheduled_at",
                     "published_at", "attempt_count", "created_at"]
    list_filter   = ["status", "social_account__platform"]
    search_fields = ["post__title", "post__content_plain",
                     "social_account__account_label"]
    readonly_fields = [
        "platform_name", "published_at", "next_retry_at",
        "attempt_count", "last_error",
        "external_post_id", "external_post_url",
        "created_at", "updated_at",
    ]
    inlines = [PostTargetContentInline, PublishingLogInline]

    @admin.display(description="Platform")
    def platform_name(self, obj):
        return obj.social_account.platform.name


# ─────────────────────────────────────────────────────────
# PostTarget (inline — used inside Post admin)
# ─────────────────────────────────────────────────────────

class PostTargetInline(admin.TabularInline):
    model  = PostTarget
    extra  = 0
    fields = ["social_account", "status", "scheduled_at", "published_at",
              "attempt_count"]
    readonly_fields = ["status", "published_at", "attempt_count"]
    show_change_link = True


# ─────────────────────────────────────────────────────────
# Post
# ─────────────────────────────────────────────────────────

@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display  = ["__str__", "status", "created_by_type", "created_by",
                     "target_count", "created_at"]
    list_filter   = ["status", "created_by_type"]
    search_fields = ["title", "content_plain"]
    readonly_fields = ["created_at", "updated_at", "target_count"]
    raw_id_fields   = ["created_by"]
    fieldsets = [
        ("Content", {
            "fields": ["title", "content_plain", "content_html", "hashtags"],
        }),
        ("Creator", {
            "fields": ["created_by", "created_by_type"],
        }),
        ("Status", {
            "fields": ["status"],
        }),
        ("Timestamps", {
            "fields": ["created_at", "updated_at"],
            "classes": ["collapse"],
        }),
    ]
    inlines = [PostMediaInline, PostTargetInline]

    @admin.display(description="Targets")
    def target_count(self, obj):
        return obj.target_count


# ─────────────────────────────────────────────────────────
# PublishingLog (standalone)
# ─────────────────────────────────────────────────────────

@admin.register(PublishingLog)
class PublishingLogAdmin(admin.ModelAdmin):
    list_display  = ["post_target", "attempt_number", "status",
                     "response_type", "started_at", "ended_at"]
    list_filter   = ["status"]
    search_fields = ["post_target__post__title", "response_message"]
    readonly_fields = ["post_target", "attempt_number", "started_at",
                       "ended_at", "response_type", "response_message",
                       "status", "created_at", "updated_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # logs are immutable
