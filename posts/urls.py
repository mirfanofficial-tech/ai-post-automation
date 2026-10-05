from django.urls import path
from . import views

app_name = "posts"

urlpatterns = [

    # ── Posts ────────────────────────────────────────────────────────────────
    path("posts/",
         views.post_list,    name="post_list"),
    path("posts/create/",
         views.post_create,  name="post_create"),
    path("posts/media/<int:pk>/delete/",
         views.media_delete, name="media_delete"),
    path("posts/<int:pk>/",
         views.post_detail,  name="post_detail"),
    path("posts/<int:pk>/edit/",
         views.post_edit,    name="post_edit"),
    path("posts/<int:pk>/delete/",
         views.post_delete,  name="post_delete"),
    path("posts/<int:pk>/unpublish/",
         views.post_unpublish, name="post_unpublish"),

    # Phase 5 — Publish Now
    path("posts/<int:pk>/publish-now/",
         views.post_publish_now, name="post_publish_now"),

    # Phase 6 — Schedule
    path("posts/<int:pk>/schedule/",
         views.post_schedule, name="post_schedule"),

    # Phase 7 — Target actions (retry / cancel)
    path("posts/targets/<int:pk>/retry/",
         views.target_retry,  name="target_retry"),
    path("posts/targets/<int:pk>/cancel/",
         views.target_cancel, name="target_cancel"),

    # ── Social Accounts ──────────────────────────────────────────────────────
    path("posts/social-accounts/",
         views.social_account_list,   name="social_account_list"),
    path("posts/social-accounts/<int:pk>/disconnect/",
         views.social_account_delete, name="social_account_delete"),
    path("posts/social-accounts/<int:pk>/edit-label/",
         views.social_account_edit_label, name="social_account_edit_label"),

    # ── LinkedIn OAuth ────────────────────────────────────────────────────────
    path("posts/social-accounts/linkedin/connect/",
         views.linkedin_connect,  name="linkedin_connect"),
    path("posts/social-accounts/linkedin/callback/",
         views.linkedin_callback, name="linkedin_callback"),

    # ── LinkedIn Page OAuth ───────────────────────────────────────────────────
    #
    #  Callback URL — register EXACTLY this in your LinkedIn Page App → Auth:
    #  Local:      http://127.0.0.1:8000/posts/social-accounts/linkedin-page/callback/
    #  Production: https://yourdomain.com/posts/social-accounts/linkedin-page/callback/
    #
    path("posts/social-accounts/linkedin-page/connect/",
         views.linkedin_page_connect,  name="linkedin_page_connect"),
    path("posts/social-accounts/linkedin-page/callback/",
         views.linkedin_page_callback, name="linkedin_page_callback"),

    # ── Facebook OAuth ────────────────────────────────────────────────────────
    path("posts/social-accounts/facebook/connect/",
         views.facebook_connect,  name="facebook_connect"),
    path("posts/social-accounts/facebook/callback/",
         views.facebook_callback, name="facebook_callback"),

    # ── Instagram OAuth ───────────────────────────────────────────────────────
    #
    #  Uses the same Facebook App. Register this callback URL in:
    #  Facebook App → Facebook Login → Valid OAuth Redirect URIs
    #  Ngrok: https://follow-macaroni-handiwork.ngrok-free.dev/posts/social-accounts/instagram/callback/
    #
    path("posts/social-accounts/instagram/connect/",
         views.instagram_connect,  name="instagram_connect"),
    path("posts/social-accounts/instagram/callback/",
         views.instagram_callback, name="instagram_callback"),
]
