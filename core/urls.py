from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    # Dashboard
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/calendar-events/", views.dashboard_calendar_events, name="dashboard_calendar_events"),

    # Profile
    path("profile/",          views.profile_view,        name="profile"),
    path("profile/password/", views.password_change_view, name="password_change"),

    # Authentication
    path("login/",  views.login_view,  name="login"),
    path("logout/", views.logout_view, name="logout"),
]
