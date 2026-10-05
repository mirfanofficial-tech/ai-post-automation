"""
Superadmin seeder.

Creates the Administrator role with all permissions, then creates (or
updates) a superuser and assigns that role so they can log in and use
the full application immediately.

Run once after migrations:
    .venv\\Scripts\\python.exe seeder.py

Idempotent — safe to run multiple times.
"""

import os
import sys
import django

# ── Bootstrap Django (picks up .env via manage.py's dotenv loader) ──
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

django.setup()

# ── Imports after setup ──────────────────────────────────────────────
from django.contrib.auth import get_user_model
from rbac.models import Permission, Role, UserProfile

User = get_user_model()

# ────────────────────────────────────────────────────────────────────
# 1.  Permissions
# ────────────────────────────────────────────────────────────────────
PERMISSIONS = [
    # ── User management ──────────────────────────────────────────────────────
    ("can_view_users",         "View Users",         "Can view the users list and detail pages"),
    ("can_create_users",       "Create Users",       "Can create new users"),
    ("can_edit_users",         "Edit Users",         "Can edit existing users"),
    ("can_delete_users",       "Delete Users",       "Can delete users"),
    # ── Role / permission management ─────────────────────────────────────────
    ("can_view_roles",         "View Roles",         "Can view roles and their details"),
    ("can_manage_roles",       "Manage Roles",       "Can create, edit, and delete roles"),
    ("can_view_permissions",   "View Permissions",   "Can view permissions"),
    ("can_manage_permissions", "Manage Permissions", "Can create, edit, and delete permissions"),
    # ── Social publishing ─────────────────────────────────────────────────────
    ("can_view_posts",         "View Posts",         "Can view the posts list and detail pages"),
    ("can_create_posts",       "Create Posts",       "Can create new posts"),
    ("can_edit_posts",         "Edit Posts",         "Can edit existing posts"),
    ("can_delete_posts",       "Delete Posts",       "Can delete posts"),
    ("can_publish_posts",      "Publish Posts",      "Can publish posts to social accounts"),
    ("can_schedule_posts",     "Schedule Posts",     "Can schedule posts for future publishing"),
    ("can_view_social_accounts",       "View Social Accounts",       "Can view connected social accounts"),
    ("can_connect_social_accounts",    "Connect Social Accounts",    "Can connect new social accounts via OAuth"),
    ("can_edit_social_accounts",       "Edit Social Accounts",       "Can edit social account labels"),
    ("can_disconnect_social_accounts", "Disconnect Social Accounts", "Can disconnect social accounts"),
    ("can_view_publishing_logs",       "View Publishing Logs",       "Can view publishing attempt history"),
]

print("\n[1/4] Syncing permissions ...")
perms = {}
for codename, name, description in PERMISSIONS:
    perm, created = Permission.objects.get_or_create(
        codename=codename,
        defaults={"name": name, "description": description},
    )
    perms[codename] = perm
    status = "created" if created else "exists "
    print(f"      {status}  {codename}")

# ────────────────────────────────────────────────────────────────────
# 2.  Administrator role
# ────────────────────────────────────────────────────────────────────
print("\n[2/4] Syncing Administrator role ...")
admin_role, created = Role.objects.get_or_create(
    name="Administrator",
    defaults={"description": "Full access to all management features"},
)
admin_role.permissions.set(list(perms.values()))
admin_role.save()
status = "created" if created else "exists "
print(f"      {status}  Administrator  ({admin_role.permissions.count()} permissions)")

# ────────────────────────────────────────────────────────────────────
# 3.  Seed default platforms
# ────────────────────────────────────────────────────────────────────
from posts.models import Platform

PLATFORMS = [
    ("LinkedIn",  "linkedin"),
    ("X",         "x"),
    ("Facebook",  "facebook"),
    ("Discord",   "discord"),
    ("Instagram", "instagram"),
]

print("\n[3/5] Syncing platforms ...")
for name, code in PLATFORMS:
    p, created = Platform.objects.get_or_create(
        code=code,
        defaults={"name": name, "status": Platform.Status.ACTIVE},
    )
    status = "created" if created else "exists "
    print(f"      {status}  {code}")

# ────────────────────────────────────────────────────────────────────
# 4.  Superuser
# ────────────────────────────────────────────────────────────────────
SUPERUSER_USERNAME = "admin"
SUPERUSER_EMAIL    = "admin@example.com"
SUPERUSER_PASSWORD = "Admin@1234"   # change after first login

print("\n[4/5] Creating / updating superuser ...")
user, created = User.objects.get_or_create(
    username=SUPERUSER_USERNAME,
    defaults={
        "email":        SUPERUSER_EMAIL,
        "is_staff":     True,
        "is_superuser": True,
        "is_active":    True,
    },
)
# Always ensure password and flags are correct even on re-run
user.set_password(SUPERUSER_PASSWORD)
user.is_staff     = True
user.is_superuser = True
user.is_active    = True
user.save()
status = "created" if created else "updated "
print(f"      {status}  {SUPERUSER_USERNAME}  ({SUPERUSER_EMAIL})")

# ────────────────────────────────────────────────────────────────────
# 5.  Assign Administrator role to the superuser
# ────────────────────────────────────────────────────────────────────
print("\n[5/5] Assigning Administrator role to superuser ...")
profile, _ = UserProfile.objects.get_or_create(user=user)
profile.role = admin_role
profile.save()
print(f"      assigned  Administrator  →  {SUPERUSER_USERNAME}")

# ────────────────────────────────────────────────────────────────────
print("""
╔══════════════════════════════════════════════════════╗
║              Seeding complete                        ║
╠══════════════════════════════════════════════════════╣
║  URL       http://127.0.0.1:8000/                    ║
║  Username  admin                                     ║
║  Password  Admin@1234                                ║
║                                                      ║
║  Change the password after your first login!         ║
╚══════════════════════════════════════════════════════╝
""")
