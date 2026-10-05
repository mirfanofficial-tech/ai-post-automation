import json

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import PasswordChangeForm, ProfileForm


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

# Status → FullCalendar colour mapping
_STATUS_COLOURS = {
    "scheduled":  "#0ea5e9",  # sky blue
    "publishing": "#f59e0b",  # amber
    "retrying":   "#f59e0b",  # amber
    "success":    "#22c55e",  # green
    "failed":     "#ef4444",  # red
    "cancelled":  "#94a3b8",  # slate
    "pending":    "#a78bfa",  # violet
}


def _build_event(target):
    """Convert a PostTarget into a FullCalendar event dict."""
    # Prefer scheduled_at; fall back to published_at
    event_dt = target.scheduled_at or target.published_at
    if not event_dt:
        return None

    post = target.post
    title = post.title.strip() if post.title else ""
    if not title:
        title = post.content_plain[:60]
        if len(post.content_plain) > 60:
            title += "…"

    return {
        "id":       target.pk,
        "post_id":  post.pk,
        "title":    title,
        "start":    event_dt.isoformat(),
        "url":      f"/posts/{post.pk}/",
        "status":   target.status,
        "color":    _STATUS_COLOURS.get(target.status, "#6b7280"),
        "extendedProps": {
            "status":   target.status,
            "platform": target.social_account.platform.name,
            "account":  target.social_account.account_label,
        },
    }


@login_required
def dashboard(request):
    """
    Main dashboard view.

    Passes to the template:
      social_accounts   – active accounts for the current user (calendar tabs)
      calendar_events   – JSON string mapping account_id → [event, …]
                          used to bootstrap FullCalendar without an extra round-trip
      cal_start / cal_end – date-range filter values preserved across page loads
    """
    from posts.models import SocialAccount, PostTarget

    social_accounts = list(
        SocialAccount.objects
        .filter(user=request.user, status=SocialAccount.Status.ACTIVE)
        .select_related("platform")
        .order_by("platform__name", "account_label")
    )

    # Read date-range from GET params (persisted in URL, spec §43)
    cal_start  = request.GET.get("cal_start",  "")
    cal_end    = request.GET.get("cal_end",    "")
    cal_status = request.GET.get("cal_status", "")

    # Silently drop an invalid range (end before start) so a crafted URL
    # like ?cal_start=2026-09-23&cal_end=2026-09-01 can't cause weird results.
    if cal_start and cal_end and cal_end < cal_start:
        cal_start = ""
        cal_end   = ""

    # Whitelist status values to prevent injection via query string
    _VALID_STATUSES = {"pending", "scheduled", "publishing", "retrying",
                       "success", "failed", "cancelled"}
    if cal_status not in _VALID_STATUSES:
        cal_status = ""

    # Build a bootstrap JSON blob: { "<account_id>": [ ...events ] }
    # Scope: current month ±1 so the calendar looks populated on first load
    now = timezone.now()
    # Use the filter range if provided, else show ±45 days
    try:
        from django.utils.dateparse import parse_date
        from datetime import datetime, time
        if cal_start:
            bs_start = timezone.make_aware(
                datetime.combine(parse_date(cal_start), time.min)
            )
        else:
            bs_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # Go back one month
            if bs_start.month == 1:
                bs_start = bs_start.replace(year=bs_start.year - 1, month=12)
            else:
                bs_start = bs_start.replace(month=bs_start.month - 1)

        if cal_end:
            bs_end = timezone.make_aware(
                datetime.combine(parse_date(cal_end), time.max)
            )
        else:
            # Two months forward from today
            end_month = now.month + 2
            end_year  = now.year
            if end_month > 12:
                end_month -= 12
                end_year  += 1
            bs_end = now.replace(year=end_year, month=end_month, day=1,
                                 hour=23, minute=59, second=59)
    except Exception:
        bs_start = now
        bs_end   = now

    events_map = {}
    if social_accounts:
        from django.db.models import Q
        account_ids = [a.pk for a in social_accounts]
        targets = (
            PostTarget.objects
            .filter(social_account_id__in=account_ids)
            .filter(
                Q(scheduled_at__range=(bs_start, bs_end)) |
                Q(published_at__range=(bs_start, bs_end))
            )
            .select_related("post", "social_account__platform")
        )
        if cal_status:
            targets = targets.filter(status=cal_status)
        for target in targets:
            ev = _build_event(target)
            if ev is None:
                continue
            key = str(target.social_account_id)
            events_map.setdefault(key, []).append(ev)

    return render(request, "dashboard.html", {
        "social_accounts":  social_accounts,
        "calendar_events":  json.dumps(events_map),
        "cal_start":        cal_start,
        "cal_end":          cal_end,
        "cal_status":       cal_status,
    })


# ---------------------------------------------------------------------------
# Dashboard — Calendar Events AJAX endpoint
# ---------------------------------------------------------------------------

@login_required
def dashboard_calendar_events(request):
    """
    GET /dashboard/calendar-events/
        ?account=<social_account_id>
        &start=<ISO-datetime>
        &end=<ISO-datetime>

    Returns a JSON array of FullCalendar event objects for one social account
    within the requested date window.  The account must belong to request.user.
    """
    from posts.models import SocialAccount, PostTarget
    from django.db.models import Q
    from django.utils.dateparse import parse_datetime

    account_id = request.GET.get("account")
    start_str  = request.GET.get("start")
    end_str    = request.GET.get("end")
    status_str = request.GET.get("cal_status", "")

    # Whitelist to prevent injection
    _VALID_STATUSES = {"pending", "scheduled", "publishing", "retrying",
                       "success", "failed", "cancelled"}
    if status_str not in _VALID_STATUSES:
        status_str = ""

    if not account_id:
        return JsonResponse({"error": "account param required"}, status=400)

    # Security: account must belong to the requesting user
    try:
        account = SocialAccount.objects.get(
            pk=account_id,
            user=request.user,
        )
    except SocialAccount.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    # Parse date range supplied by FullCalendar (ISO 8601)
    try:
        start_dt = parse_datetime(start_str) if start_str else None
        end_dt   = parse_datetime(end_str)   if end_str   else None
        # FullCalendar sends naive UTC strings like "2026-09-01T00:00:00"
        if start_dt and timezone.is_naive(start_dt):
            start_dt = timezone.make_aware(start_dt)
        if end_dt and timezone.is_naive(end_dt):
            end_dt = timezone.make_aware(end_dt)
    except Exception:
        start_dt = end_dt = None

    qs = PostTarget.objects.filter(
        social_account=account
    ).select_related("post", "social_account__platform")

    if start_dt and end_dt:
        # Reject invalid range: end before start
        if end_dt < start_dt:
            return JsonResponse({"error": "end date cannot be before start date"}, status=400)
        qs = qs.filter(
            Q(scheduled_at__range=(start_dt, end_dt)) |
            Q(published_at__range=(start_dt, end_dt))
        )
    elif start_dt:
        qs = qs.filter(
            Q(scheduled_at__gte=start_dt) |
            Q(published_at__gte=start_dt)
        )
    elif end_dt:
        qs = qs.filter(
            Q(scheduled_at__lte=end_dt) |
            Q(published_at__lte=end_dt)
        )

    events = []
    for target in qs:
        ev = _build_event(target)
        if ev:
            # Apply status filter after building (keeps the query simple)
            if status_str and ev["status"] != status_str:
                continue
            events.append(ev)

    return JsonResponse(events, safe=False)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@login_required
def profile_view(request):
    """
    GET  → show profile form pre-populated with current data.
    POST → validate and save; re-render on error, redirect on success.
    """
    user = request.user
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("core:profile")
    else:
        form = ProfileForm(instance=user)

    return render(request, "profile.html", {
        "form": form,
        "active_tab": "profile",
    })


@login_required
def password_change_view(request):
    """
    GET  → show password change form.
    POST → validate, save, keep session alive, redirect back to profile.
    """
    user = request.user
    if request.method == "POST":
        form = PasswordChangeForm(user, request.POST)
        if form.is_valid():
            form.save()
            # Re-hash the session so the user stays logged in after password change
            update_session_auth_hash(request, user)
            messages.success(request, "Password changed successfully.")
            return redirect("core:profile")
    else:
        form = PasswordChangeForm(user)

    return render(request, "profile.html", {
        "password_form": form,
        "active_tab": "password",
    })


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login_view(request):
    """
    GET  → render the login form.
    POST → validate credentials; on success redirect to `next` or dashboard.
    
    Accepts both username and email for login.
    """
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    error = None
    username = ""

    if request.method == "POST":
        username_or_email = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        username = username_or_email  # Keep for form repopulation

        # Try to authenticate with username first
        user = authenticate(request, username=username_or_email, password=password)

        # If authentication failed and input looks like an email, try finding user by email
        if user is None and "@" in username_or_email:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                user_obj = User.objects.get(email=username_or_email)
                # Now authenticate with the actual username
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                pass
            except User.MultipleObjectsReturned:
                # Multiple users with same email - require username
                pass

        if user is not None:
            login(request, user)
            next_url = request.POST.get("next") or request.GET.get("next") or "core:dashboard"
            if next_url.startswith("http") or " " in next_url:
                next_url = "core:dashboard"
            return redirect(next_url)
        else:
            error = "Invalid username/email or password. Please try again."

    return render(request, "auth/login.html", {
        "error": error,
        "username": username,
        "next": request.GET.get("next", ""),
    })


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@require_http_methods(["POST"])
def logout_view(request):
    """POST-only logout — avoids CSRF-free GET logout vulnerabilities."""
    logout(request)
    return redirect("core:login")
