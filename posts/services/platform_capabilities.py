"""
Platform Capabilities Registry
───────────────────────────────
Single source of truth for what each platform supports.

When adding a new platform (Facebook, Twitter, Discord…):
  1. Add its entry here
  2. Write its adapter (publisher class)
  3. Register it in publishing_manager._REGISTRY
  4. Templates and adapters automatically pick up the new capabilities — zero
     changes needed anywhere else.

Media priority rule (enforced by each adapter):
  1. Featured video  — if platform supports it AND post has a video → use video
  2. Featured image  — if platform supports it AND post has an image → use image
  3. Text-only       — if neither is available or supported

Keys per platform
─────────────────
color                   Tailwind bg-* class for the platform icon badge
icon                    Lucide icon name  (https://lucide.dev/icons/)

max_text_length         Maximum characters (text + hashtags combined)

supports_featured_image Can attach one featured/cover image to the post
supports_featured_video Can attach one featured video to the post
                        Video takes priority over image when both are present
                        and the platform supports video.

max_extra_images        Additional images beyond the featured one.
                        0  = only one image total (the featured one)
                        9  = up to 9 extra (so 10 total, e.g. Facebook carousel)

max_documents           How many document files (PDF, DOCX, etc.) can be attached.
                        0  = documents not supported
supported_doc_types     List of accepted document MIME type prefixes.
                        Empty list means no documents accepted.
                        e.g. ["application/pdf", "application/msword",
                               "application/vnd.openxmlformats"]

supports_delete         Platform API supports deleting a published post
max_retries             Maximum publishing attempts before marking FAILED
retry_delays            Seconds to wait before each retry [attempt1, attempt2, …]
"""

from typing import TypedDict


class PlatformCaps(TypedDict):
    # UI
    color:                  str
    icon:                   str
    # Text
    max_text_length:        int
    # Featured media (priority: video > image)
    supports_featured_image: bool
    supports_featured_video: bool
    # Extra attachments
    max_extra_images:       int
    max_documents:          int
    supported_doc_types:    list[str]
    # Lifecycle
    supports_delete:        bool
    max_retries:            int
    retry_delays:           list[int]


PLATFORM_CAPABILITIES: dict[str, PlatformCaps] = {

    # ── LinkedIn ──────────────────────────────────────────────────────────────
    # UGC Posts API: text-only OR text + 1 image.
    # Video requires a separate chunked-upload API (not implemented yet).
    # Documents require a separate Document Share API (not implemented yet).
    "linkedin": {
        "color":                   "bg-blue-600",
        "icon":                    "linkedin",
        "max_text_length":         3000,
        "supports_featured_image": True,
        "supports_featured_video": False,   # chunked upload API — future
        "max_extra_images":        0,       # only 1 image total via UGC API
        "max_documents":           0,
        "supported_doc_types":     [],
        "supports_delete":         True,
        "max_retries":             4,
        "retry_delays":            [60, 120, 300],
    },

    # ── LinkedIn Page (Organization/Company Page) ─────────────────────────────
    # Same UGC Posts API as personal, but author = urn:li:organization:{id}.
    # Requires a separate LinkedIn Developer App with Marketing Developer Platform.
    "linkedin_page": {
        "color":                   "bg-blue-800",
        "icon":                    "building-2",
        "max_text_length":         3000,
        "supports_featured_image": True,
        "supports_featured_video": False,   # chunked upload API — future
        "max_extra_images":        0,
        "max_documents":           0,
        "supported_doc_types":     [],
        "supports_delete":         True,
        "max_retries":             4,
        "retry_delays":            [60, 120, 300],
    },

    # ── Facebook ──────────────────────────────────────────────────────────────
    # Graph API: text, single photo, video, or photo carousel (up to 10).
    "facebook": {
        "color":                   "bg-blue-700",
        "icon":                    "facebook",
        "max_text_length":         63206,
        "supports_featured_image": True,
        "supports_featured_video": True,
        "max_extra_images":        9,       # up to 10 total in a carousel
        "max_documents":           0,
        "supported_doc_types":     [],
        "supports_delete":         True,
        "max_retries":             4,
        "retry_delays":            [60, 120, 300],
    },

    # ── X (Twitter) ───────────────────────────────────────────────────────────
    # v2 API: text + up to 4 images OR 1 video (not both in same tweet).
    "x": {
        "color":                   "bg-slate-900",
        "icon":                    "twitter",
        "max_text_length":         280,
        "supports_featured_image": True,
        "supports_featured_video": True,    # 1 video per tweet
        "max_extra_images":        3,       # up to 4 images total (no video if images used)
        "max_documents":           0,
        "supported_doc_types":     [],
        "supports_delete":         True,
        "max_retries":             4,
        "retry_delays":            [60, 120, 300],
    },

    # ── Discord ───────────────────────────────────────────────────────────────
    # Webhook / Bot API: text + file attachments (images, video, docs all OK).
    "discord": {
        "color":                   "bg-indigo-600",
        "icon":                    "message-circle",
        "max_text_length":         2000,
        "supports_featured_image": True,
        "supports_featured_video": True,
        "max_extra_images":        9,
        "max_documents":           10,      # Discord accepts any file attachment
        "supported_doc_types":     [
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument",
            "text/plain",
            "text/csv",
        ],
        "supports_delete":         True,
        "max_retries":             3,
        "retry_delays":            [30, 60, 120],
    },

    # ── Instagram ─────────────────────────────────────────────────────────────
    # Graph API: image or video required (text-only not supported).
    # Carousel: up to 10 items (images or videos mixed).
    "instagram": {
        "color":                   "bg-pink-500",
        "icon":                    "instagram",
        "max_text_length":         2200,
        "supports_featured_image": True,
        "supports_featured_video": True,
        "max_extra_images":        9,
        "max_documents":           0,
        "supported_doc_types":     [],
        "supports_delete":         False,   # Graph API does not support delete
        "max_retries":             4,
        "retry_delays":            [60, 120, 300],
    },

    # ── Fallback for any unknown platform ────────────────────────────────────
    "_default": {
        "color":                   "bg-slate-500",
        "icon":                    "globe",
        "max_text_length":         5000,
        "supports_featured_image": True,
        "supports_featured_video": False,
        "max_extra_images":        0,
        "max_documents":           0,
        "supported_doc_types":     [],
        "supports_delete":         False,
        "max_retries":             4,
        "retry_delays":            [60, 120, 300],
    },
}


def get_caps(platform_code: str) -> PlatformCaps:
    """
    Return capabilities for the given platform code.
    Falls back to _default for unknown platforms — never raises.

    Usage:
        from posts.services.platform_capabilities import get_caps

        caps = get_caps("linkedin")
        caps["max_text_length"]          # 3000
        caps["supports_featured_video"]  # False
        caps["supports_delete"]          # True
    """
    return PLATFORM_CAPABILITIES.get(platform_code, PLATFORM_CAPABILITIES["_default"])


def pick_featured_media(platform_code: str, media_list: list):
    """
    Choose the best single featured media item for this platform.

    Priority:
        1. Video  — if platform supports_featured_video AND a video exists
        2. Image  — if platform supports_featured_image AND an image exists
        3. None   — text-only post

    Parameters:
        platform_code   e.g. "linkedin"
        media_list      list of PostMedia objects ordered by sort_order

    Returns:
        (media_object, media_kind)   where media_kind is "video", "image", or None
        (None, None)                 if nothing suitable found
    """
    caps = get_caps(platform_code)

    videos    = [m for m in media_list if m.media_type == "video"]
    images    = [m for m in media_list if m.media_type == "image"]
    documents = [m for m in media_list if m.media_type == "document"]

    # Priority 1: featured video
    if caps["supports_featured_video"] and videos:
        featured = next((v for v in videos if v.is_main), videos[0])
        return featured, "video"

    # Priority 2: featured image
    if caps["supports_featured_image"] and images:
        featured = next((i for i in images if i.is_main), images[0])
        return featured, "image"

    # Nothing suitable
    return None, None


def get_skipped_media(platform_code: str, media_list: list, featured) -> list:
    """
    Return all media items that will NOT be sent to the platform.

    Used for logging — lets the adapter report exactly what was skipped and why.

    Parameters:
        platform_code  e.g. "linkedin"
        media_list     full list of PostMedia objects
        featured       the media object chosen by pick_featured_media (or None)

    Returns:
        list of (media_object, reason_string) tuples
    """
    caps    = get_caps(platform_code)
    skipped = []

    for m in media_list:
        if m is featured:
            continue  # this one is being used

        if m.media_type == "video" and not caps["supports_featured_video"]:
            skipped.append((m, "platform does not support video"))
        elif m.media_type == "image" and caps["max_extra_images"] == 0:
            skipped.append((m, "platform only supports 1 image total"))
        elif m.media_type == "document" and caps["max_documents"] == 0:
            skipped.append((m, "platform does not support document attachments"))
        else:
            skipped.append((m, "extra attachment — not included"))

    return skipped
