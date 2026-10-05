"""
Shared result dataclass returned by every publisher and tool.

Using a typed result object (instead of raise-on-failure) means the
PublishingManager can handle partial failures cleanly — one platform
failing does not stop the others (spec §26).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PublishResult:
    """
    Outcome of a single publishing attempt.

    Fields
    ──────
    success          True if the platform accepted the post.
    external_post_id Platform-assigned post ID (e.g. LinkedIn URN).
    external_post_url Direct URL to the published post.
    response_type    Short code for the response kind, e.g. HTTP_201, TIMEOUT.
    message          Human-readable success or sanitized error message.
    retryable        True if a retry might succeed (network/5xx). False for
                     permanent failures (bad content, auth revoked).
    """

    success:           bool
    external_post_id:  str            = ""
    external_post_url: str            = ""
    response_type:     str            = ""
    message:           str            = ""
    retryable:         bool           = False

    # ── Convenience constructors ──────────────────────────────────────────────

    @classmethod
    def ok(cls, external_post_id: str = "",
               external_post_url: str = "",
               message: str = "Published successfully.") -> "PublishResult":
        return cls(
            success           = True,
            external_post_id  = external_post_id,
            external_post_url = external_post_url,
            response_type     = "SUCCESS",
            message           = message,
            retryable         = False,
        )

    @classmethod
    def fail(cls, message: str, retryable: bool = False,
                  response_type: str = "ERROR") -> "PublishResult":
        return cls(
            success       = False,
            message       = message,
            response_type = response_type,
            retryable     = retryable,
        )
