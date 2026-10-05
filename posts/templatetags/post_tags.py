"""
Custom template tags for the posts module.

Tags
────
{% status_badge <status> %}
    Renders a coloured status pill inline — no template include needed.
    Avoids Django Context stack overhead (RecursionError on Python 3.14).

{% platform_color <platform_code> %}
    Returns the Tailwind bg-* class for the platform icon badge.
    e.g.  {% platform_color "linkedin" %}  →  bg-blue-600

{% platform_icon <platform_code> %}
    Returns the Lucide icon name for the platform.
    e.g.  {% platform_icon "linkedin" %}  →  linkedin

{% platform_supports_delete <platform_code> %}
    Returns "true" or "false" (JS-safe string).
    e.g.  {% platform_supports_delete "linkedin" %}  →  true

{% platform_cap <platform_code> <key> %}
    Returns any single capability value as a string.
    e.g.  {% platform_cap "linkedin" "max_text_length" %}  →  3000
"""

from django import template
from django.utils import timezone
from django.utils.html import format_html

from posts.services.platform_capabilities import get_caps

register = template.Library()


# ── Status badge ──────────────────────────────────────────────────────────────

_STATUS_STYLES = {
    "draft":               ("bg-slate-100",  "text-slate-600",  "bg-slate-400",                "Draft"),
    "scheduled":           ("bg-sky-100",    "text-sky-700",    "bg-sky-400",                  "Scheduled"),
    "publishing":          ("bg-violet-100", "text-violet-700", "bg-violet-400 animate-pulse", "Publishing"),
    "published":           ("bg-emerald-100","text-emerald-700","bg-emerald-400",               "Published"),
    "partially_published": ("bg-amber-100",  "text-amber-700",  "bg-amber-400",                "Partial"),
    "failed":              ("bg-red-100",    "text-red-700",    "bg-red-400",                  "Failed"),
    "pending":             ("bg-slate-100",  "text-slate-500",  "bg-slate-300",                "Pending"),
    "retrying":            ("bg-orange-100", "text-orange-700", "bg-orange-400 animate-pulse", "Retrying"),
    "success":             ("bg-emerald-100","text-emerald-700","bg-emerald-400",               "Success"),
    "cancelled":           ("bg-slate-100",  "text-slate-500",  "bg-slate-300",                "Cancelled"),
}


@register.simple_tag
def status_badge(status):
    """
    Render a coloured status pill.

    Usage:  {% status_badge post.status %}
            {% status_badge target.status %}
    """
    status_str = str(status) if status else ""
    style      = _STATUS_STYLES.get(status_str)

    if style:
        bg, text, dot, label = style
    else:
        bg, text, dot = "bg-slate-100", "text-slate-500", "bg-slate-300"
        label = status_str.replace("_", " ").title() if status_str else ""

    return format_html(
        '<span class="inline-flex items-center gap-1 rounded-full {} {} px-2.5 py-0.5 text-xs font-medium">'
        '<span class="w-1.5 h-1.5 rounded-full {}"></span>{}'
        '</span>',
        bg, text, dot, label,
    )


# ── Platform capability tags ──────────────────────────────────────────────────

@register.simple_tag
def platform_color(platform_code: str) -> str:
    """
    Return the Tailwind bg-* class for a platform icon badge.

    Usage:  <span class="{% platform_color account.platform.code %}">
    """
    return get_caps(platform_code)["color"]


@register.simple_tag
def platform_icon(platform_code: str) -> str:
    """
    Return the Lucide icon name for a platform.

    Usage:  <i data-lucide="{% platform_icon account.platform.code %}"></i>
    """
    return get_caps(platform_code)["icon"]


@register.simple_tag
def platform_supports_delete(platform_code: str) -> str:
    """
    Return JS-safe "true" or "false" for whether the platform supports post deletion.

    Usage (in a <script> block):
        supportsDelete: {% platform_supports_delete target.social_account.platform.code %},
    """
    return "true" if get_caps(platform_code)["supports_delete"] else "false"


@register.simple_tag
def platform_cap(platform_code: str, key: str) -> str:
    """
    Return any single capability value as a string.

    Usage:  {% platform_cap "linkedin" "max_text_length" %}  →  3000
            {% platform_cap "linkedin" "supports_video" %}   →  False
    """
    caps = get_caps(platform_code)
    return str(caps.get(key, ""))


@register.simple_tag
def platform_media_note(platform_code: str) -> str:
    """
    Return a human-readable note about what media this platform accepts.
    Used in the publish panel to warn users about attachment limits.
    Returns empty string if no restrictions worth noting.
    """
    caps  = get_caps(platform_code)
    notes = []

    if caps["supports_featured_video"] and caps["supports_featured_image"]:
        notes.append("Video is used as featured media if provided (takes priority over image)")
    elif caps["supports_featured_image"] and not caps["supports_featured_video"]:
        notes.append("1 image only — video and documents not supported")

    if caps["max_extra_images"] == 0 and caps["supports_featured_image"]:
        pass  # already covered above
    elif caps["max_extra_images"] > 0:
        notes.append(f"up to {caps['max_extra_images'] + 1} images in carousel")

    if caps["max_documents"] == 0 and caps["supported_doc_types"] == []:
        pass  # no doc support — already noted
    elif caps["max_documents"] > 0:
        notes.append(f"up to {caps['max_documents']} document(s) accepted")

    return " · ".join(notes) if notes else ""


# ── Scheduled time countdown ──────────────────────────────────────────────────

@register.simple_tag
def time_until(dt) -> str:
    """
    Return a human-readable countdown to a future datetime.

    - If dt is None              → ""
    - If time has already passed → "00:00"
    - Otherwise                  → "in Xd Xh Xm" (largest two units only)

    Usage in template:
        {% time_until target.scheduled_at %}
        → "in 2h 15m"  or  "00:00"  or  ""
    """
    if not dt:
        return ""

    now   = timezone.now()
    delta = dt - now
    total = int(delta.total_seconds())

    if total <= 0:
        return "00:00"

    days    = total // 86400
    hours   = (total % 86400) // 3600
    minutes = (total % 3600) // 60

    if days > 0:
        return f"in {days}d {hours}h"
    if hours > 0:
        return f"in {hours}h {minutes}m"
    return f"in {minutes}m"
