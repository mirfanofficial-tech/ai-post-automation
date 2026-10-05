"""
Forms for the Social Publishing module (Phase 2).
"""

from django import forms

from .models import Post, PostMedia, SocialAccount


# ── shared Tailwind input classes ─────────────────────────────────────────────
_INPUT  = "w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-400 focus:border-transparent transition"
_AREA   = _INPUT + " resize-none"
_SELECT = "w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-violet-400 focus:border-transparent transition bg-white"


class PostForm(forms.ModelForm):
    """Create or edit a Post."""

    class Meta:
        model  = Post
        fields = ["title", "content_plain", "hashtags"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": _INPUT,
                "placeholder": "Optional title (for your reference only)",
            }),
            "content_plain": forms.Textarea(attrs={
                "class": _AREA,
                "rows": 6,
                "placeholder": "Write your post content here…",
            }),
            "hashtags": forms.TextInput(attrs={
                "class": _INPUT,
                "placeholder": "#AI #Python #Django",
            }),
        }

    def clean_content_plain(self):
        content = self.cleaned_data.get("content_plain", "").strip()
        if not content:
            raise forms.ValidationError("Post content is required.")
        return content


class PostMediaForm(forms.ModelForm):
    """Upload multiple media files (images, documents, PDFs) for a post."""

    class Meta:
        model  = PostMedia
        fields = ["file", "is_main"]
        widgets = {
            "file":    forms.FileInput(attrs={
                "class":    "hidden",
                "id":       "media-file-input",
                "accept":   "image/*,video/*,.pdf,.doc,.docx,.txt,.csv,.xls,.xlsx",
            }),
            "is_main": forms.CheckboxInput(attrs={
                "class": "rounded border-slate-300 text-violet-600 focus:ring-violet-400",
            }),
        }


class SocialAccountForm(forms.ModelForm):
    """
    Manually register a social account.
    Phase 3 will replace this with a real OAuth flow for LinkedIn.
    """

    class Meta:
        model  = SocialAccount
        fields = ["platform", "account_label", "external_account_id"]
        widgets = {
            "platform": forms.Select(attrs={"class": _SELECT}),
            "account_label": forms.TextInput(attrs={
                "class":       _INPUT,
                "placeholder": "e.g. My LinkedIn Profile",
            }),
            "external_account_id": forms.TextInput(attrs={
                "class":       _INPUT,
                "placeholder": "Platform account/page ID (optional for now)",
            }),
        }
        labels = {
            "platform":            "Platform",
            "account_label":       "Account Label",
            "external_account_id": "Platform Account ID (optional)",
        }
        help_texts = {
            "account_label":       "A friendly name so you can identify this account.",
            "external_account_id": "The platform's own ID for this account. Can be left blank until OAuth is set up.",
        }

