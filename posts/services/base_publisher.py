"""
BasePublisher — abstract interface every platform adapter must implement.

Adding a new platform (X, Discord, Facebook) = subclass this + register
in PublishingManager. Nothing else changes.

Each method is a TOOL — callable independently by the UI or an AI agent.
"""

from abc import ABC, abstractmethod

from posts.models import PostTarget

from .result import PublishResult


class BasePublisher(ABC):
    """
    Abstract base class for all platform publishing adapters.

    Subclasses must implement:
        platform_code  — class-level string, e.g. "linkedin"
        validate()     — check content meets platform requirements
        publish()      — send the post to the platform API
        classify_error()  — decide if an error is retryable

    All methods receive a fully-hydrated PostTarget with related
    post, media, social_account, and credential already loaded.
    """

    # ── Subclass must set this ────────────────────────────────────────────────
    platform_code: str = ""

    # ── Retry config (can be overridden per platform) ─────────────────────────
    MAX_ATTEMPTS:    int       = 4
    RETRY_DELAYS:    list[int] = [60, 120, 300]   # seconds between attempts

    # ═════════════════════════════════════════════════════════════════════════
    # Abstract interface — every adapter must implement these
    # ═════════════════════════════════════════════════════════════════════════

    @abstractmethod
    def validate(self, target: PostTarget) -> PublishResult:
        """
        TOOL: validate_post_target

        Validate that the PostTarget's content meets this platform's
        requirements before attempting to publish.

        Returns PublishResult.ok() if valid.
        Returns PublishResult.fail(..., retryable=False) if invalid.
        Never raises — always returns a result.
        """

    @abstractmethod
    def publish(self, target: PostTarget) -> PublishResult:
        """
        TOOL: publish_post_target

        Send the post to the platform API.

        Must NOT be called inside an open database transaction (spec §25).
        Returns PublishResult with external_post_id and external_post_url on success.
        Never raises — always returns a result.
        """

    @abstractmethod
    def classify_error(self, status_code: int, body: str) -> bool:
        """
        Decide whether an API error is retryable.

        Returns True  → retryable (network timeout, 5xx, rate limit).
        Returns False → permanent (bad content, auth revoked, 4xx).
        """

    # ═════════════════════════════════════════════════════════════════════════
    # Shared helpers (available to all subclasses)
    # ═════════════════════════════════════════════════════════════════════════

    def get_content(self, target: PostTarget) -> tuple[str, str]:
        """
        Return (content, hashtags) for this target.

        Uses PostTargetContent override if one exists,
        otherwise falls back to the post's common content.
        """
        post = target.post
        try:
            override = target.content_override
            content  = override.content_plain or post.content_plain
            hashtags = override.hashtags       or post.hashtags
        except Exception:
            content  = post.content_plain
            hashtags = post.hashtags
        return content.strip(), hashtags.strip()

    def build_full_text(self, target: PostTarget) -> str:
        """
        Combine content + hashtags into a single string ready for publishing.

        Example output:
            "AI agents are changing how we build...

            #AI #Python #Django"
        """
        content, hashtags = self.get_content(target)
        if hashtags:
            return f"{content}\n\n{hashtags}"
        return content

    def get_credential(self, target: PostTarget):
        """
        Return the SocialAccountCredential for this target.
        Raises PublisherError if missing or expired.
        """
        try:
            cred = target.social_account.credential
        except Exception:
            raise PublisherError(
                "No credential found for this account. Please reconnect.",
                retryable=False,
            )
        if cred.is_expired:
            raise PublisherError(
                "Access token has expired. Please reconnect the account.",
                retryable=False,
            )
        return cred

    def retry_delay_for(self, attempt: int) -> int:
        """Return the delay in seconds before the next retry attempt."""
        delays = self.RETRY_DELAYS
        idx = min(attempt - 1, len(delays) - 1)
        return delays[idx]


# ── Publisher-level exception ─────────────────────────────────────────────────

class PublisherError(Exception):
    """
    Raised inside publishers for known, classifiable failures.
    Always carry a `retryable` flag so the manager can decide what to do.
    """
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
