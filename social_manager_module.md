# Post Publishing & Multi-Platform Social Media System

## 1. Purpose

Build a reusable, multi-platform post publishing system inside the Django application.

The first supported platform will be **LinkedIn**.

The architecture must be designed so additional platforms can be added later without redesigning the core post system.

Future platforms may include:

- LinkedIn
- X / Twitter
- Facebook
- Discord
- Other social/content platforms

The system must support both:

1. Human-controlled publishing through the Django UI.
2. Future AI-agent-controlled publishing through reusable backend tools/services.

The human UI and future AI agents must ultimately use the same underlying post/publishing services.

---

# 2. Core Concept

The system has a distinction between:

## Post

A `Post` is the common content created by a human or AI.

Example:

> "AI agents are changing how we build applications..."

The Post itself does not represent a single platform publication.

## Post Target

A `PostTarget` represents:

> "Publish this Post to this specific social account."

For example:

```text
Post #100

PostTarget #1
Platform: LinkedIn
Account: My LinkedIn
Action: Publish Now
Status: SUCCESS

PostTarget #2
Platform: X
Account: My X Account
Action: Schedule
Scheduled At: 2026-09-25 14:00
Status: SCHEDULED
```

This separation is fundamental.

Each platform target has an independent lifecycle.

---

# 3. Independent Platform Scheduling

Every selected platform/account must independently support:

- Publish Now
- Schedule for later

Example:

```text
Post #100

LinkedIn
    Publish Now
    → SUCCESS

X
    Schedule
    → 2026-09-25 15:00
    → SCHEDULED

Discord
    Publish Now
    → SUCCESS
```

It must NOT be necessary for all selected platforms to use the same publishing time.

One platform can publish immediately while another is scheduled for later.

Each `PostTarget` owns its own:

- scheduled_at
- status
- published_at
- retry state
- external post ID
- external post URL
- publishing logs

---

# 4. Platform Architecture

## Table: `platform`

Recommended table name:

```text
platform
```

Purpose:

Defines supported platforms.

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK | Internal ID |
| `name` | string | Human-readable name, e.g. LinkedIn |
| `code` | string unique | Stable identifier, e.g. `linkedin` |
| `status` | enum | `active`, `inactive` |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

Example:

```text
id: 1
name: LinkedIn
code: linkedin
status: active
```

```text
id: 2
name: X
code: x
status: active
```

## Why use `code`?

The platform code is a stable machine-readable identifier.

Examples:

```text
linkedin
x
facebook
discord
```

Do not use the display name as application logic.

The display name can change.

The code should remain stable.

---

# 5. Platform Content Requirements

A platform can have different content requirements.

For example:

### LinkedIn

```text
Content: required
Plain content: supported
HTML: platform-dependent
Hashtags: optional
Image: supported
Multiple images: supported
Title: not required
```

### Discord

```text
Content: required
Title: required for the selected publishing format
Image: supported
Multiple images: platform-dependent
Hashtags: optional/not applicable
```

The system must not hard-code LinkedIn requirements into the main Post model.

Platform-specific requirements belong to the platform publishing layer.

---

# 6. Platform Content Table

Recommended name:

```text
post_target_content
```

Alternative:

```text
platform_post_content
```

Use:

```text
post_target_content
```

because this content belongs to a particular `PostTarget`.

The PostTarget already identifies:

- Platform
- Social Account

Therefore the content is specifically prepared for that target.

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK | Internal ID |
| `post_target_id` | FK | Related PostTarget |
| `title` | nullable text | Platform-specific title |
| `content_plain` | nullable text | Plain text content |
| `content_html` | nullable text | HTML content where supported |
| `hashtags` | nullable text | Platform-specific hashtags |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update |

---

# 7. Important distinction: Requirements vs Content

The system must distinguish between:

### Platform capability/requirement

Example:

```text
LinkedIn supports multiple images.
```

and:

### Actual PostTarget content

Example:

```text
This particular LinkedIn post has 3 images.
```

Do not store a field such as:

```text
linkedin_requires_image = 1
```

inside every post.

Instead, the platform configuration/adapter defines what the platform supports and requires.

The actual PostTarget determines what content is being sent.

---

# 8. Platform Capability Configuration

The platform publishing adapter should define capabilities such as:

```text
supports_title
requires_title
supports_plain_content
supports_html
supports_hashtags
supports_single_image
supports_multiple_images
supports_scheduling
supports_publish_now
```

This can initially live in application configuration/code rather than requiring another database table.

Example concept:

```python
LinkedInCapabilities(
    supports_title=False,
    requires_title=False,
    supports_plain_content=True,
    supports_html=False,
    supports_hashtags=True,
    supports_single_image=True,
    supports_multiple_images=True,
    supports_scheduling=True,
)
```

This prevents the database from becoming full of platform-specific boolean columns.

---

# 9. Social Account

Recommended table:

```text
social_account
```

Purpose:

Represents a connected account/page/channel belonging to a platform.

A user may connect multiple accounts on the same platform.

Example:

```text
User
 ├── LinkedIn → "My Main LinkedIn"
 ├── LinkedIn → "Company LinkedIn"
 └── X → "My X Account"
```

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK | Internal ID |
| `user_id` | FK | Application user who owns/connected account |
| `platform_id` | FK | Related platform |
| `account_label` | string | User-friendly name |
| `external_account_id` | nullable string | Platform's account/page ID |
| `status` | enum | `active`, `inactive` |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update |

Example:

```text
account_label = "My Main LinkedIn"
platform = linkedin
```

The user can choose any useful label.

---

# 10. Social Account Credentials

Recommended table:

```text
social_account_credential
```

This must be separate from `social_account`.

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK | Internal ID |
| `social_account_id` | OneToOne/FK | Related account |
| `token_type` | string | e.g. Bearer |
| `access_token` | encrypted text | OAuth access token |
| `refresh_token` | encrypted text | OAuth refresh token where applicable |
| `scope` | text | Granted scopes |
| `expires_at` | nullable datetime | Token expiry |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update |

## Security

Tokens must never be displayed in normal UI.

Tokens must never be written to application logs.

Tokens should be encrypted at rest.

The credential model should be accessed only by the publishing/authentication service.

Do not expose access tokens to AI tools.

AI tools should receive a safe account identifier such as:

```text
social_account_id
```

not the actual credential.

---

# 11. Post

Recommended table:

```text
post
```

This represents the common content.

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK/UUID | Internal post ID |
| `title` | nullable string | Optional internal/common title |
| `content_plain` | text | Main plain-text content |
| `content_html` | nullable text | Optional HTML representation |
| `hashtags` | nullable text | Common hashtags |
| `created_by_id` | nullable FK User | Human creator |
| `created_by_type` | enum | `user`, `ai_system` |
| `status` | enum | Draft/lifecycle status |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

---

# 12. Creator Design

The Post may eventually be created by:

- Human
- AI system

Do not use fake user IDs such as `0` to represent AI.

Instead, use a nullable user relationship plus a clear creator/source field.

Recommended concept:

```text
created_by_id = NULL
created_by_type = ai_system
```

for AI-created posts.

Human:

```text
created_by_id = 25
created_by_type = user
```

This avoids foreign-key integrity problems.

If later the AI has its own system identity, the architecture can evolve without changing historical records.

---

# 13. Post Status

Recommended Post statuses:

```text
DRAFT
SCHEDULED
PUBLISHING
PUBLISHED
PARTIALLY_PUBLISHED
FAILED
```

Avoid duplicate statuses such as:

```text
success
published
success
```

Use one clear lifecycle.

Important:

The overall Post status is calculated/updated based on its PostTargets.

Example:

```text
LinkedIn → SUCCESS
X → SUCCESS

Post → PUBLISHED
```

Example:

```text
LinkedIn → SUCCESS
X → FAILED

Post → PARTIALLY_PUBLISHED
```

Example:

```text
LinkedIn → FAILED
X → FAILED

Post → FAILED
```

---

# 14. Post Media

Recommended table:

```text
post_media
```

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK | Internal ID |
| `post_id` | FK | Related Post |
| `media_type` | string/enum | image/video/etc. |
| `mime_type` | string | e.g. image/jpeg |
| `file` | FileField | Actual uploaded file |
| `file_size` | integer | Size in bytes |
| `sort_order` | integer | Display/publishing order |
| `is_main` | boolean | Main/thumbnail image |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update |

Initially support:

```text
image
```

The design should allow future media types.

---

# 15. Post Target

Recommended table:

```text
post_target
```

This is one of the most important tables.

A PostTarget means:

> Publish this Post to this specific Social Account.

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK | Internal ID |
| `post_id` | FK | Related Post |
| `social_account_id` | FK | Target account |
| `status` | enum | Target publishing state |
| `scheduled_at` | nullable datetime | This target's schedule |
| `published_at` | nullable datetime | Actual publishing time |
| `external_post_id` | nullable string | ID returned by platform |
| `external_post_url` | nullable URL/string | URL returned by platform |
| `next_retry_at` | nullable datetime | Next retry time |
| `attempt_count` | integer | Number of attempts |
| `last_error` | nullable text | Latest failure reason |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update |

---

# 16. Do Not Duplicate Platform Code Unnecessarily

Because:

```text
PostTarget
    ↓
SocialAccount
    ↓
Platform
```

the platform can already be determined through:

```text
post_target.social_account.platform
```

Therefore a `platform_code` column in `PostTarget` is generally unnecessary.

Avoid storing the same relationship twice unless there is a demonstrated performance requirement.

This prevents inconsistent data such as:

```text
social_account.platform = linkedin
post_target.platform_code = x
```

The SocialAccount should be the source of truth.

---

# 17. Post Target Status

Recommended values:

```text
PENDING
SCHEDULED
PUBLISHING
RETRYING
SUCCESS
FAILED
CANCELLED
```

Meaning:

### PENDING

Target exists but has not been sent yet.

### SCHEDULED

Target has a future publishing time.

### PUBLISHING

Publishing request is currently being processed.

### RETRYING

Previous attempt failed and another attempt is scheduled.

### SUCCESS

Successfully published.

### FAILED

All allowed attempts have failed or the error is non-retryable.

### CANCELLED

Publishing was intentionally cancelled.

---

# 18. Publishing Log

Recommended table:

```text
publishing_log
```

Purpose:

Record every individual publishing attempt.

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | PK | Internal ID |
| `post_target_id` | FK | Related PostTarget |
| `attempt_number` | integer | Attempt number |
| `started_at` | datetime | Attempt start |
| `ended_at` | datetime | Attempt finish |
| `response_type` | string | HTTP/API/error type |
| `response_message` | text | Sanitized response/error |
| `status` | enum | `SUCCESS`, `FAILED` |
| `created_at` | datetime | Log creation time |
| `updated_at` | datetime | Last update |

The actual platform response should be sanitized before storage.

Never store:

- Access tokens
- Refresh tokens
- Authorization headers
- Sensitive credentials

---

# 19. Retry Strategy

Default retry schedule:

```text
Attempt 1
    ↓ failure
wait 1 minute

Attempt 2
    ↓ failure
wait 2 minutes

Attempt 3
    ↓ failure
wait 5 minutes

Attempt 4
    ↓ failure
FINAL FAILED
```

Therefore:

```text
MAX_ATTEMPTS = 4
```

Retry delays:

```text
60 seconds
120 seconds
300 seconds
```

The retry system must be platform-target specific.

Example:

```text
LinkedIn
Attempt 1 → FAILED
Attempt 2 → SUCCESS

X
Attempt 1 → FAILED
Attempt 2 → FAILED
Attempt 3 → FAILED
Attempt 4 → FAILED
```

LinkedIn becomes:

```text
SUCCESS
```

while X becomes:

```text
FAILED
```

The X failure must not affect the LinkedIn result.

---

# 20. Retry Classification

Not every failure should be retried.

## Retryable

Examples:

- Temporary network failure
- Timeout
- HTTP 5xx
- Temporary platform outage
- Retryable rate limit

## Non-retryable

Examples:

- Invalid content
- Invalid account
- Missing required field
- Invalid request
- Permanent permission failure

## Authentication failure

Examples:

- Expired access token
- Revoked authorization

These should normally stop publishing and require account reconnection rather than blindly retrying.

The platform adapter must classify errors.

---

# 21. External Post Information

When a platform successfully publishes a post, the platform may return:

```text
external_post_id
external_post_url
```

Store both.

Example:

```text
external_post_id = "urn:li:share:123456"
external_post_url = "https://..."
```

These values belong to `PostTarget`.

Do not store them only in logs because the application will need them for:

- Status display
- Open published post
- Future update/delete actions
- AI tools
- Audit history

---

# 22. Publishing Architecture

Use a service/adapter architecture.

Recommended structure:

```text
posts/
    services/
        publishing/
            base.py
            manager.py
            linkedin.py
```

Later:

```text
posts/
    services/
        publishing/
            base.py
            manager.py
            linkedin.py
            x.py
            facebook.py
            discord.py
```

---

# 23. Base Publisher

Every platform should follow a common interface.

Conceptually:

```python
class BasePublisher:
    def validate(self, post_target):
        ...

    def publish(self, post_target):
        ...

    def classify_error(self, error):
        ...
```

The exact implementation can vary.

The important point is that the publishing manager should not contain LinkedIn-specific API logic.

---

# 24. Publishing Manager

The publishing manager coordinates the process.

Conceptually:

```text
PublishingManager
        │
        ├── identify platform
        │
        ├── validate target
        │
        ├── select adapter
        │
        ├── execute publishing
        │
        ├── record result
        │
        ├── create PublishingLog
        │
        └── schedule retry if necessary
```

Example:

```text
PostTarget
    ↓
SocialAccount
    ↓
Platform = linkedin
    ↓
LinkedInPublisher
    ↓
LinkedIn API
```

---

# 25. Transaction Best Practice

Do NOT keep a database transaction open while calling an external platform API.

Bad:

```text
BEGIN TRANSACTION

Create Post
Call LinkedIn API
Wait
Call X API
Wait

COMMIT
```

This can cause long-running transactions and database locks.

Instead:

```text
Transaction 1

Create Post
Create PostMedia
Create PostTargets

COMMIT
```

Then independently:

```text
LinkedIn target
    ↓
external API
    ↓
database update
```

and:

```text
X target
    ↓
external API
    ↓
database update
```

Each individual database state transition should use appropriate atomic transactions.

---

# 26. Platform Failure Isolation

If one platform fails:

```text
LinkedIn → SUCCESS
X → FAILED
Discord → SUCCESS
```

do NOT roll back the entire Post.

The Post must preserve successful platform results.

The failed target can be retried independently.

This is a core requirement.

---

# 27. Human Publishing UI

Create a Post interface.

Example:

```text
Create Post
────────────────────────────────

Title
[ Optional........................ ]

Content
[..................................]
[..................................]
[..................................]

Hashtags
[ #AI #Python #Django............. ]

Media
[ Upload Image ]

Platforms

☑ LinkedIn
☐ X
☐ Discord

LinkedIn Account
[ My Main LinkedIn ▼ ]

Publishing

○ Publish Now
○ Schedule

Date
[................]

Time
[................]

[ Save Draft ] [ Publish ]
```

---

# 28. Platform-Specific Scheduling UI

Each selected target must have its own publishing mode.

Example:

```text
LinkedIn
☑ Selected

Publishing:
○ Publish Now
○ Schedule

Schedule:
2026-09-25
10:00 AM
```

Then:

```text
X
☑ Selected

Publishing:
○ Publish Now
○ Schedule

Schedule:
2026-09-25
02:00 PM
```

These schedules are independent.

---

# 29. Platform-Specific Validation

When a platform is selected, validate according to its capabilities.

Example LinkedIn:

```text
Content: required
Hashtags: optional
Image: optional
Multiple images: supported
Title: not required
```

Example Discord:

```text
Content: required
Title: required for selected Discord format
Image: optional
```

The common Post should remain reusable.

The platform adapter should perform final platform-specific validation before publishing.

---

# 30. Draft Behavior

A Post can be saved as:

```text
DRAFT
```

without publishing.

Drafts can later be edited.

A draft may have:

- No PostTargets
- Some PostTargets
- Multiple PostTargets

Publishing should only happen when the user explicitly publishes/schedules it.

---

# 31. Editing

Users with appropriate permissions can edit a draft.

For already-published targets:

Do not silently overwrite external platform content.

Future functionality may support:

```text
Edit Published Post
```

but this should be implemented separately because every platform has different update capabilities.

For the first version:

- Draft → editable
- Scheduled → editable before publishing
- Published → read-only from this system

unless explicit platform update support is later implemented.

---

# 32. Deletion

Deleting a local Post must be treated carefully.

If it has already been published externally, deleting the local record must NOT automatically mean deleting the external post.

External deletion should be a separate explicit action.

---

# 33. Publishing Result UI

The Post detail page should show each platform separately.

Example:

```text
Post: AI Agents

LinkedIn
━━━━━━━━━━━━━━━━━━━━
Status: SUCCESS
Published: 10:05 AM

View Published Post
https://...

X
━━━━━━━━━━━━━━━━━━━━
Status: RETRYING
Next retry: 10:07 AM

Last error:
Temporary API timeout

Discord
━━━━━━━━━━━━━━━━━━━━
Status: SUCCESS
Published: 10:06 AM
```

This makes platform-specific results immediately understandable.

---

# 34. Publishing History

Each PostTarget should have a publishing history.

Example:

```text
LinkedIn

Attempt #1
FAILED
10:00:01
Timeout

Attempt #2
FAILED
10:01:04
Temporary API error

Attempt #3
SUCCESS
10:03:12
```

The current status should be shown prominently.

The logs provide detailed history.

---

# 35. Permissions / RBAC

The existing RBAC system must be used.

Potential permissions:

```text
can_view_posts
can_create_posts
can_edit_posts
can_delete_posts
can_publish_posts
can_schedule_posts

can_view_social_accounts
can_connect_social_accounts
can_edit_social_accounts
can_disconnect_social_accounts

can_view_publishing_logs
```

Do not allow the new system to bypass the existing authorization layer.

Human UI actions must use the same permission system.

---

# 36. Social Account Security

The UI may show:

```text
My Main LinkedIn
LinkedIn
Connected
```

It must NOT show:

```text
Access Token:
AQAA...
```

Credentials are internal secrets.

Only the publishing/authentication service may access them.

---

# 37. AI Agent Integration

The long-term goal is to expose the system as reusable tools.

The AI should not directly manipulate database tables.

The AI should call application services/tools.

Potential tools:

```text
create_post
update_post
get_post
list_posts
delete_post

upload_post_media

list_social_accounts
connect_social_account

publish_post
schedule_post

get_post_status
get_post_target_status
get_publishing_logs

retry_post_target
cancel_scheduled_post
```

---

# 38. AI Content Generation

Future AI workflow:

```text
User:
"Create a LinkedIn post about Laravel AI agents."

        ↓

AI generates:

Content
Hashtags
Optional media suggestion

        ↓

Create Draft

        ↓

Human Review

        ↓

Approve

        ↓

AI calls publish_post()

        ↓

Publishing Manager

        ↓

LinkedIn Adapter

        ↓

LinkedIn API
```

The AI should not publish without the appropriate permission/authorization.

Human approval can remain part of the workflow.

---

# 39. AI Should Use the Same Tools as Humans

This is a key architectural requirement.

Do NOT create:

```text
Human Publishing System
```

and separately:

```text
AI Publishing System
```

Instead:

```text
Human UI ─────┐
              ├──→ Application Services ──→ Platform Adapters
AI Tools ─────┘
```

Both paths use the same business logic.

This prevents duplicated behavior and security problems.

---

# 40. Future AI Tool Example

Eventually the AI could call:

```json
{
  "tool": "create_post",
  "arguments": {
    "content": "AI agents are changing...",
    "hashtags": ["AI", "Laravel", "Python"]
  }
}
```

Then:

```json
{
  "tool": "schedule_post",
  "arguments": {
    "post_id": "...",
    "platform": "linkedin",
    "social_account_id": "...",
    "scheduled_at": "2026-09-25T10:00:00"
  }
}
```

The backend handles:

- Validation
- RBAC
- Credentials
- Scheduling
- API calls
- Retries
- Logging
- External IDs

---

# 41. Background Processing

Publishing and scheduling should not block normal HTTP requests.

The eventual architecture should use a background worker/task mechanism.

Conceptually:

```text
HTTP Request
     ↓
Create PostTarget
     ↓
Commit
     ↓
Queue Publishing Job
     ↓
Background Worker
     ↓
Platform API
```

For scheduled posts:

```text
scheduled_at
     ↓
Scheduler
     ↓
Publishing Job
     ↓
Platform Adapter
```

The exact queue technology can be selected during implementation.

Do not introduce a queue only for the sake of complexity if the current version can safely use a simpler mechanism.

---

# 42. Idempotency

Publishing systems must consider duplicate publishing.

A retry must not accidentally create duplicate external posts when the previous request actually succeeded but the response was lost.

The implementation should consider:

- Idempotency keys where the platform supports them.
- Local publishing attempt IDs.
- External response tracking.
- Reconciliation where possible.

This is especially important for retries.

Example:

```text
Request sent to LinkedIn
        ↓
LinkedIn successfully publishes
        ↓
Network connection fails before response reaches application
        ↓
Application thinks request failed
        ↓
Retry
```

Without protection, two posts may be created.

The platform adapter must handle this scenario as safely as the platform API allows.

---

# 43. Time and Scheduling

Store timestamps consistently.

Recommended:

- Store database timestamps in UTC.
- Convert to the user's timezone in the UI.
- Store the selected scheduling time in a timezone-aware form.

Do not store naive local times for scheduled publishing.

---

# 44. Auditability

The system should make it possible to answer:

```text
Who created this post?
Which platforms were selected?
Which account was used?
When was it scheduled?
When was it published?
How many attempts happened?
Why did an attempt fail?
What external post was created?
```

This is why `PostTarget` and `PublishingLog` are separate.

---

# 45. Initial Platform: LinkedIn

The first implementation should focus on LinkedIn only.

Initial scope:

```text
Platform
Social Account
Social Account Credential
Post
Post Media
Post Target
Post Target Content
Publishing Log
LinkedIn Publisher
```

Do not implement X/Discord/Facebook API integration yet.

However, their future addition must not require changing the Post model.

---

# 46. Initial LinkedIn Flow

```text
Admin
  ↓
Connect LinkedIn Account
  ↓
OAuth
  ↓
Store encrypted credentials
  ↓
Account = ACTIVE
```

Then:

```text
Create Post
  ↓
Upload image
  ↓
Enter content
  ↓
Optional hashtags
  ↓
Select LinkedIn account
  ↓
Publish Now OR Schedule
```

Publish:

```text
PostTarget
  ↓
LinkedInPublisher
  ↓
Validate
  ↓
Get credentials
  ↓
Call LinkedIn API
  ↓
SUCCESS
```

Save:

```text
status = SUCCESS
published_at
external_post_id
external_post_url
attempt_count
```

---

# 47. Error Flow

Example:

```text
LinkedIn API
     ↓
Timeout
     ↓
PublishingLog
     ↓
status = FAILED
     ↓
Is retryable?
     ↓ YES
next_retry_at = +1 minute
     ↓
Background worker
     ↓
Attempt #2
```

If all attempts fail:

```text
PostTarget
status = FAILED

attempt_count = 4

last_error = "...reason..."

next_retry_at = NULL
```

No further automatic retries.

---

# 48. Overall Post Status Calculation

The Post status should reflect its targets.

Example:

```text
No targets
→ DRAFT

Targets exist but all scheduled
→ SCHEDULED

Any target currently publishing
→ PUBLISHING

All targets successful
→ PUBLISHED

Some successful + some failed
→ PARTIALLY_PUBLISHED

All targets permanently failed
→ FAILED
```

The exact state transition implementation should be centralized rather than duplicated across views.

---

# 49. Database Relationship Diagram

Conceptually:

```text
┌──────────────┐
│    User      │
└──────┬───────┘
       │
       │ owns
       ▼
┌─────────────────────┐
│   SocialAccount     │
└─────────┬───────────┘
          │
          │ belongs to
          ▼
┌─────────────────────┐
│      Platform       │
└─────────────────────┘


┌──────────────┐
│     Post     │
└──────┬───────┘
       │
       ├───────────────┐
       │               │
       ▼               ▼
┌──────────────┐   ┌────────────────┐
│  PostMedia   │   │   PostTarget   │
└──────────────┘   └───────┬────────┘
                           │
                           │
                           ▼
                  ┌─────────────────┐
                  │ SocialAccount   │
                  └─────────────────┘

PostTarget
    │
    ├── PostTargetContent
    │
    └── PublishingLog
```

---

# 50. Final Database Summary

The initial database should contain approximately:

```text
platform
social_account
social_account_credential

post
post_media
post_target
post_target_content

publishing_log
```

Potential future tables:

```text
scheduled_job
platform_webhook
oauth_state
media_processing
```

These should only be introduced when actually required.

---

# 51. Recommended Constraints

Use database constraints wherever appropriate.

Examples:

### Platform

```text
code UNIQUE
```

### SocialAccount

Potentially:

```text
(user_id, platform_id, external_account_id) UNIQUE
```

depending on the platform/account ownership model.

### SocialAccountCredential

```text
social_account_id UNIQUE
```

if each account has one current credential record.

### PostTarget

Prevent accidental duplicate targets for the same post/account:

```text
(post_id, social_account_id) UNIQUE
```

unless the business later requires multiple publications of the same Post to the same account.

---

# 52. Soft Deactivation

For Platforms and Social Accounts, prefer:

```text
status = inactive
```

over deleting historical records.

This preserves historical publishing information.

Example:

```text
LinkedIn account disconnected
```

Historical posts should still show:

```text
LinkedIn
Published successfully
```

even though the account is now inactive.

---

# 53. Do Not Delete Publishing History

Publishing logs are historical records.

Do not automatically delete them when:

- A SocialAccount becomes inactive.
- A platform becomes inactive.
- A PostTarget fails.
- A credential expires.

Historical information is valuable for:

- Debugging
- Audit
- AI reasoning
- User visibility
- Future analytics

---

# 54. Development Phases

## Phase 1 — Data Foundation

Build:

- Platform
- SocialAccount
- SocialAccountCredential
- Post
- PostMedia
- PostTarget
- PostTargetContent
- PublishingLog

Create migrations and tests.

No external API calls yet.

---

## Phase 2 — Post Management

Build:

- Post list
- Create Post
- Edit Post
- View Post
- Draft
- Delete
- Media upload
- Platform selection

No real publishing yet.

---

## Phase 3 — Social Account Connection

Implement LinkedIn connection.

Build:

- Connect account
- OAuth flow
- Callback
- Credential storage
- Account listing
- Account status
- Disconnect/deactivate

Credentials must be securely stored.

---

## Phase 4 — Publishing Service

Implement:

```text
BasePublisher
PublishingManager
LinkedInPublisher
```

Add:

- Validation
- Publishing
- Result handling
- External ID
- External URL
- Error classification

---

## Phase 5 — Publishing Now

Implement:

```text
Publish Now
```

for LinkedIn.

Create the PostTarget.

Execute the publishing service.

Record result.

---

## Phase 6 — Scheduling

Implement independent target scheduling.

Example:

```text
LinkedIn → now
X → tomorrow
Discord → next week
```

Each target has its own `scheduled_at`.

---

## Phase 7 — Retry System

Implement:

```text
1 minute
2 minutes
5 minutes
```

with maximum 4 attempts.

Record every attempt in PublishingLog.

---

## Phase 8 — UI Status and Logs

Show:

- Current platform status
- Published time
- External URL
- Retry status
- Last error
- Publishing history

---

## Phase 9 — Additional Platforms

Add adapters independently:

```text
XPublisher
DiscordPublisher
FacebookPublisher
```

Do not modify the core Post architecture unnecessarily.

---

## Phase 10 — AI Tools

Expose application-level tools:

```text
create_post
update_post
get_post
upload_post_media

list_social_accounts
connect_social_account

publish_post
schedule_post
cancel_scheduled_post

get_post_status
get_publishing_logs
retry_post_target
```

The AI should use these tools rather than directly accessing database tables.

---

# 55. AI Safety and Human Approval

The future AI agent should support a human-review workflow.

Example:

```text
AI generates post
       ↓
DRAFT
       ↓
Human reviews
       ↓
APPROVED
       ↓
AI publishes/schedules
```

The AI should not automatically publish unless the application's permission/workflow explicitly allows it.

Human and AI actions must both pass through the same authorization/business rules.

---

# 56. Design Principles

The implementation must follow these principles:

1. **Platform-independent Post model**
2. **Platform-specific PostTarget**
3. **Independent scheduling per target**
4. **Independent publishing status per target**
5. **Independent retry per target**
6. **Independent publishing logs**
7. **One platform failure must not roll back another successful platform**
8. **External API calls must not be held inside long database transactions**
9. **Credentials must be encrypted**
10. **Never expose credentials to AI tools**
11. **Use platform adapters**
12. **Keep platform-specific requirements out of the core Post model**
13. **Use server-side validation**
14. **Use existing RBAC**
15. **Make services reusable by both UI and future AI agents**
16. **Store external post IDs and URLs**
17. **Preserve historical publishing logs**
18. **Use timezone-aware scheduling**
19. **Design for idempotent/reliable publishing**
20. **Add new platforms through adapters instead of rewriting the system**

---

# 57. Final Target Architecture

The final system should conceptually look like:

```text
                         ┌──────────────────┐
                         │    Django UI     │
                         └────────┬─────────┘
                                  │
                                  │
                         ┌────────▼─────────┐
                         │   Post Service   │
                         └────────┬─────────┘
                                  │
                         ┌────────▼─────────┐
                         │ PublishingManager│
                         └────────┬─────────┘
                                  │
             ┌────────────────────┼────────────────────┐
             │                    │                    │
             ▼                    ▼                    ▼
       LinkedInAdapter        XAdapter          DiscordAdapter
             │                    │                    │
             ▼                    ▼                    ▼
       LinkedIn API            X API              Discord API


Data Layer:

User
 │
 └── SocialAccount
       │
       ├── Platform
       └── Credentials

Post
 │
 ├── PostMedia
 │
 └── PostTarget
       │
       ├── SocialAccount
       ├── PostTargetContent
       └── PublishingLog
```

---

# 58. First Implementation Scope

For the first implementation, DO NOT attempt to build everything above at once.

The first coding milestone should be:

```text
Platform
SocialAccount
SocialAccountCredential
Post
PostMedia
PostTarget
PostTargetContent
PublishingLog
```

with:

```text
Django migrations
Django models
relationships
constraints
indexes
admin registration
model tests
```

No real LinkedIn API publishing in this first milestone.

Once the data foundation is verified, build the UI and then the LinkedIn adapter.

This keeps the implementation safe, testable, and easy to extend.