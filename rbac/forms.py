"""
Forms for the RBAC management UI.
"""

from django import forms
from django.contrib.auth import get_user_model

from .models import Permission, Role, UserProfile

User = get_user_model()


# ── Permission ────────────────────────────────────────────────────────────

class PermissionForm(forms.ModelForm):
    class Meta:
        model = Permission
        fields = ["name", "codename", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "codename": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. can_view_users"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


# ── Role ──────────────────────────────────────────────────────────────────

class RoleForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="Permissions",
    )

    class Meta:
        model = Role
        fields = ["name", "description", "permissions"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


# ── User CRUD ─────────────────────────────────────────────────────────────

class UserCreateForm(forms.ModelForm):
    """Create a new user with optional role assignment."""

    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    role = forms.ModelChoiceField(
        queryset=Role.objects.all(),
        required=False,
        empty_label="— No role —",
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Role",
    )

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "is_active", "is_staff"]
        widgets = {
            "username":   forms.TextInput(attrs={"class": "form-control"}),
            "email":      forms.EmailInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name":  forms.TextInput(attrs={"class": "form-control"}),
            "is_active":  forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_staff":   forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            role = self.cleaned_data.get("role")
            UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        return user


class UserEditForm(forms.ModelForm):
    """Edit an existing user — password is optional."""

    password1 = forms.CharField(
        label="New password",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Leave blank to keep current"}),
    )
    password2 = forms.CharField(
        label="Confirm new password",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Leave blank to keep current"}),
    )
    role = forms.ModelChoiceField(
        queryset=Role.objects.all(),
        required=False,
        empty_label="— No role —",
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Role",
    )

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "is_active", "is_staff"]
        widgets = {
            "username":   forms.TextInput(attrs={"class": "form-control"}),
            "email":      forms.EmailInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name":  forms.TextInput(attrs={"class": "form-control"}),
            "is_active":  forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_staff":   forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate role from the user's profile if it exists
        if self.instance and self.instance.pk:
            try:
                self.fields["role"].initial = self.instance.profile.role
            except UserProfile.DoesNotExist:
                pass

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        p1 = self.cleaned_data.get("password1")
        if p1:
            user.set_password(p1)
        if commit:
            user.save()
            role = self.cleaned_data.get("role")
            UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        return user


# ── Assign role to user (quick form) ─────────────────────────────────────

class AssignRoleForm(forms.Form):
    role = forms.ModelChoiceField(
        queryset=Role.objects.all(),
        required=False,
        empty_label="— No role —",
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Role",
    )
