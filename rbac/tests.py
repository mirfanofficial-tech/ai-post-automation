"""
RBAC authorization test suite.

Tests
─────
1.  Anonymous user → 302 to login on all protected pages
2.  No-role user   → 403 on all RBAC pages
3.  Viewer role    → 200 on view pages, 403 on write actions
4.  Admin role     → 200 on all pages, all write actions succeed
5.  Security       → Viewer cannot bypass 403 by sending POST directly
6.  Dashboard      → accessible to all authenticated users
7.  Sidebar        → user_permissions injected correctly per role
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import Permission, Role, UserProfile

User = get_user_model()


# ──────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────

def _create_user(username, password="testpass123", role=None):
    user = User.objects.create_user(username=username, password=password)
    profile = UserProfile.objects.create(user=user, role=role)
    return user


def _get_or_create_roles():
    """Return (admin_role, viewer_role) using the seeded data or create fresh."""
    # Permissions
    perm_defs = [
        ("can_view_users",        "View Users"),
        ("can_create_users",      "Create Users"),
        ("can_edit_users",        "Edit Users"),
        ("can_delete_users",      "Delete Users"),
        ("can_view_roles",        "View Roles"),
        ("can_manage_roles",      "Manage Roles"),
        ("can_view_permissions",  "View Permissions"),
        ("can_manage_permissions","Manage Permissions"),
    ]
    perms = {}
    for codename, name in perm_defs:
        p, _ = Permission.objects.get_or_create(
            codename=codename, defaults={"name": name}
        )
        perms[codename] = p

    admin_role, _ = Role.objects.get_or_create(
        name="Administrator_test",
        defaults={"description": "All permissions"},
    )
    admin_role.permissions.set(list(perms.values()))

    viewer_role, _ = Role.objects.get_or_create(
        name="Viewer_test",
        defaults={"description": "Read-only"},
    )
    viewer_role.permissions.set([
        perms["can_view_users"],
        perms["can_view_roles"],
        perms["can_view_permissions"],
    ])

    return admin_role, viewer_role


# ──────────────────────────────────────────────────────────────────────────
#  Base test case — sets up roles + users once per class
# ──────────────────────────────────────────────────────────────────────────

class RBACTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_role, cls.viewer_role = _get_or_create_roles()

        cls.admin_user  = _create_user("test_admin",  role=cls.admin_role)
        cls.viewer_user = _create_user("test_viewer", role=cls.viewer_role)
        cls.norole_user = _create_user("test_norole", role=None)

        # A target user/role/permission to hit with pk URLs
        cls.target_user = _create_user("test_target", role=cls.viewer_role)

        # A role and permission we can try to delete in tests
        cls.target_role, _ = Role.objects.get_or_create(
            name="TargetRole_test", defaults={"description": "Deletable role"}
        )
        cls.target_perm, _ = Permission.objects.get_or_create(
            codename="can_target_test", defaults={"name": "Target Test"}
        )


# ──────────────────────────────────────────────────────────────────────────
#  1. Anonymous — all protected URLs → 302 to login
# ──────────────────────────────────────────────────────────────────────────

class AnonymousAccessTests(RBACTestBase):
    def setUp(self):
        self.client = Client()   # not logged in

    def _assert_redirects_to_login(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_dashboard_redirects(self):
        self._assert_redirects_to_login(reverse("core:dashboard"))

    def test_user_list_redirects(self):
        self._assert_redirects_to_login(reverse("rbac:user_list"))

    def test_user_create_redirects(self):
        self._assert_redirects_to_login(reverse("rbac:user_create"))

    def test_user_detail_redirects(self):
        self._assert_redirects_to_login(
            reverse("rbac:user_detail", args=[self.target_user.pk])
        )

    def test_user_edit_redirects(self):
        self._assert_redirects_to_login(
            reverse("rbac:user_edit", args=[self.target_user.pk])
        )

    def test_role_list_redirects(self):
        self._assert_redirects_to_login(reverse("rbac:role_list"))

    def test_role_create_redirects(self):
        self._assert_redirects_to_login(reverse("rbac:role_create"))

    def test_role_detail_redirects(self):
        self._assert_redirects_to_login(
            reverse("rbac:role_detail", args=[self.target_role.pk])
        )

    def test_permission_list_redirects(self):
        self._assert_redirects_to_login(reverse("rbac:permission_list"))

    def test_permission_create_redirects(self):
        self._assert_redirects_to_login(reverse("rbac:permission_create"))


# ──────────────────────────────────────────────────────────────────────────
#  2. No-role user — all RBAC pages → 403
# ──────────────────────────────────────────────────────────────────────────

class NoRoleAccessTests(RBACTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.norole_user)

    def _assert_403(self, url, method="get", data=None):
        fn = getattr(self.client, method)
        resp = fn(url, data=data or {})
        self.assertEqual(
            resp.status_code, 403,
            f"Expected 403 for {method.upper()} {url}, got {resp.status_code}"
        )

    def test_dashboard_accessible(self):
        # Dashboard itself requires only login, not a role
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_user_list_forbidden(self):
        self._assert_403(reverse("rbac:user_list"))

    def test_user_create_get_forbidden(self):
        self._assert_403(reverse("rbac:user_create"))

    def test_user_create_post_forbidden(self):
        self._assert_403(reverse("rbac:user_create"), method="post",
                         data={"username": "x", "password1": "p", "password2": "p"})

    def test_user_edit_forbidden(self):
        self._assert_403(reverse("rbac:user_edit", args=[self.target_user.pk]))

    def test_user_delete_forbidden(self):
        self._assert_403(reverse("rbac:user_delete", args=[self.target_user.pk]),
                         method="post")

    def test_role_list_forbidden(self):
        self._assert_403(reverse("rbac:role_list"))

    def test_role_create_forbidden(self):
        self._assert_403(reverse("rbac:role_create"))

    def test_role_edit_forbidden(self):
        self._assert_403(reverse("rbac:role_edit", args=[self.target_role.pk]))

    def test_permission_list_forbidden(self):
        self._assert_403(reverse("rbac:permission_list"))

    def test_permission_create_forbidden(self):
        self._assert_403(reverse("rbac:permission_create"))


# ──────────────────────────────────────────────────────────────────────────
#  3. Viewer — can view, cannot write
# ──────────────────────────────────────────────────────────────────────────

class ViewerAccessTests(RBACTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.viewer_user)

    # ── CAN access ─────────────────────────────────────────────────────

    def test_can_view_user_list(self):
        resp = self.client.get(reverse("rbac:user_list"))
        self.assertEqual(resp.status_code, 200)

    def test_can_view_user_detail(self):
        resp = self.client.get(
            reverse("rbac:user_detail", args=[self.target_user.pk])
        )
        self.assertEqual(resp.status_code, 200)

    def test_can_view_role_list(self):
        resp = self.client.get(reverse("rbac:role_list"))
        self.assertEqual(resp.status_code, 200)

    def test_can_view_role_detail(self):
        resp = self.client.get(
            reverse("rbac:role_detail", args=[self.target_role.pk])
        )
        self.assertEqual(resp.status_code, 200)

    def test_can_view_permission_list(self):
        resp = self.client.get(reverse("rbac:permission_list"))
        self.assertEqual(resp.status_code, 200)

    def test_can_access_dashboard(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)

    # ── CANNOT write ───────────────────────────────────────────────────

    def _assert_403(self, url, method="get", data=None):
        fn = getattr(self.client, method)
        resp = fn(url, data=data or {})
        self.assertEqual(
            resp.status_code, 403,
            f"Expected 403 for {method.upper()} {url}, got {resp.status_code}"
        )

    def test_cannot_get_user_create(self):
        self._assert_403(reverse("rbac:user_create"))

    def test_cannot_post_user_create(self):
        self._assert_403(reverse("rbac:user_create"), method="post",
                         data={"username": "x", "password1": "p", "password2": "p"})

    def test_cannot_get_user_edit(self):
        self._assert_403(reverse("rbac:user_edit", args=[self.target_user.pk]))

    def test_cannot_post_user_edit(self):
        self._assert_403(reverse("rbac:user_edit", args=[self.target_user.pk]),
                         method="post", data={"username": "x"})

    def test_cannot_post_user_delete(self):
        self._assert_403(reverse("rbac:user_delete", args=[self.target_user.pk]),
                         method="post")

    def test_cannot_get_role_create(self):
        self._assert_403(reverse("rbac:role_create"))

    def test_cannot_post_role_create(self):
        self._assert_403(reverse("rbac:role_create"), method="post",
                         data={"name": "x"})

    def test_cannot_get_role_edit(self):
        self._assert_403(reverse("rbac:role_edit", args=[self.target_role.pk]))

    def test_cannot_post_role_delete(self):
        self._assert_403(reverse("rbac:role_delete", args=[self.target_role.pk]),
                         method="post")

    def test_cannot_get_permission_create(self):
        self._assert_403(reverse("rbac:permission_create"))

    def test_cannot_post_permission_create(self):
        self._assert_403(reverse("rbac:permission_create"), method="post",
                         data={"name": "x", "codename": "x"})

    def test_cannot_get_permission_edit(self):
        self._assert_403(
            reverse("rbac:permission_edit", args=[self.target_perm.pk])
        )

    def test_cannot_post_permission_edit(self):
        self._assert_403(
            reverse("rbac:permission_edit", args=[self.target_perm.pk]),
            method="post", data={"name": "x", "codename": "x"}
        )

    def test_cannot_post_permission_delete(self):
        self._assert_403(
            reverse("rbac:permission_delete", args=[self.target_perm.pk]),
            method="post"
        )

    # ── UI: action buttons hidden ────────────────────────────────────

    def test_user_list_no_add_button(self):
        resp = self.client.get(reverse("rbac:user_list"))
        self.assertNotContains(resp, reverse("rbac:user_create"))

    def test_user_list_no_edit_button(self):
        resp = self.client.get(reverse("rbac:user_list"))
        self.assertNotContains(resp, reverse("rbac:user_edit", args=[self.target_user.pk]))

    def test_role_list_no_add_button(self):
        resp = self.client.get(reverse("rbac:role_list"))
        self.assertNotContains(resp, reverse("rbac:role_create"))

    def test_permission_list_no_add_button(self):
        resp = self.client.get(reverse("rbac:permission_list"))
        self.assertNotContains(resp, reverse("rbac:permission_create"))

    # ── Sidebar shows only view links ───────────────────────────────

    def test_sidebar_shows_users_link(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertContains(resp, reverse("rbac:user_list"))

    def test_sidebar_shows_roles_link(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertContains(resp, reverse("rbac:role_list"))

    def test_sidebar_shows_permissions_link(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertContains(resp, reverse("rbac:permission_list"))


# ──────────────────────────────────────────────────────────────────────────
#  4. Administrator — full access
# ──────────────────────────────────────────────────────────────────────────

class AdminAccessTests(RBACTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin_user)

    def _assert_ok(self, url):
        resp = self.client.get(url)
        self.assertEqual(
            resp.status_code, 200,
            f"Expected 200 for GET {url}, got {resp.status_code}"
        )

    def test_can_view_user_list(self):
        self._assert_ok(reverse("rbac:user_list"))

    def test_can_view_user_detail(self):
        self._assert_ok(reverse("rbac:user_detail", args=[self.target_user.pk]))

    def test_can_get_user_create(self):
        self._assert_ok(reverse("rbac:user_create"))

    def test_can_get_user_edit(self):
        self._assert_ok(reverse("rbac:user_edit", args=[self.target_user.pk]))

    def test_can_view_role_list(self):
        self._assert_ok(reverse("rbac:role_list"))

    def test_can_view_role_detail(self):
        self._assert_ok(reverse("rbac:role_detail", args=[self.target_role.pk]))

    def test_can_get_role_create(self):
        self._assert_ok(reverse("rbac:role_create"))

    def test_can_get_role_edit(self):
        self._assert_ok(reverse("rbac:role_edit", args=[self.target_role.pk]))

    def test_can_view_permission_list(self):
        self._assert_ok(reverse("rbac:permission_list"))

    def test_can_get_permission_create(self):
        self._assert_ok(reverse("rbac:permission_create"))

    def test_can_get_permission_edit(self):
        self._assert_ok(reverse("rbac:permission_edit", args=[self.target_perm.pk]))

    def test_can_post_create_user(self):
        resp = self.client.post(reverse("rbac:user_create"), {
            "username": "brand_new_user",
            "password1": "strongpass999",
            "password2": "strongpass999",
            "is_active": "on",
        })
        # Successful create → redirect to user list
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username="brand_new_user").exists())

    def test_can_post_create_role(self):
        resp = self.client.post(reverse("rbac:role_create"), {
            "name": "NewRoleByAdmin",
            "description": "created in test",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Role.objects.filter(name="NewRoleByAdmin").exists())

    def test_can_post_create_permission(self):
        resp = self.client.post(reverse("rbac:permission_create"), {
            "name": "New Admin Perm",
            "codename": "new_admin_perm_test",
            "description": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Permission.objects.filter(codename="new_admin_perm_test").exists()
        )

    def test_sidebar_shows_all_links(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertContains(resp, reverse("rbac:user_list"))
        self.assertContains(resp, reverse("rbac:role_list"))
        self.assertContains(resp, reverse("rbac:permission_list"))

    def test_user_list_shows_add_button(self):
        resp = self.client.get(reverse("rbac:user_list"))
        self.assertContains(resp, reverse("rbac:user_create"))

    def test_role_list_shows_add_button(self):
        resp = self.client.get(reverse("rbac:role_list"))
        self.assertContains(resp, reverse("rbac:role_create"))

    def test_permission_list_shows_add_button(self):
        resp = self.client.get(reverse("rbac:permission_list"))
        self.assertContains(resp, reverse("rbac:permission_create"))


# ──────────────────────────────────────────────────────────────────────────
#  5. Security — viewer cannot bypass via direct POST
# ──────────────────────────────────────────────────────────────────────────

class SecurityBypassTests(RBACTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.viewer_user)

    def test_post_user_create_blocked(self):
        initial_count = User.objects.count()
        resp = self.client.post(reverse("rbac:user_create"), {
            "username": "injected_user",
            "password1": "pass",
            "password2": "pass",
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(User.objects.count(), initial_count)

    def test_post_role_create_blocked(self):
        initial_count = Role.objects.count()
        resp = self.client.post(reverse("rbac:role_create"), {"name": "HackedRole"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Role.objects.count(), initial_count)

    def test_post_role_delete_blocked(self):
        # The target role should still exist after a denied delete
        resp = self.client.post(
            reverse("rbac:role_delete", args=[self.target_role.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Role.objects.filter(pk=self.target_role.pk).exists())

    def test_post_permission_create_blocked(self):
        initial_count = Permission.objects.count()
        resp = self.client.post(reverse("rbac:permission_create"), {
            "name": "Hacked Perm",
            "codename": "hacked_perm",
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Permission.objects.count(), initial_count)


# ──────────────────────────────────────────────────────────────────────────
#  6. No-role sidebar — management section hidden entirely
# ──────────────────────────────────────────────────────────────────────────

class NoRoleSidebarTests(RBACTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.norole_user)

    def test_sidebar_hides_users_link(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(resp, reverse("rbac:user_list"))

    def test_sidebar_hides_roles_link(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(resp, reverse("rbac:role_list"))

    def test_sidebar_hides_permissions_link(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(resp, reverse("rbac:permission_list"))

    def test_dashboard_still_accessible(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)


# ──────────────────────────────────────────────────────────────────────────
#  7. 403 page content
# ──────────────────────────────────────────────────────────────────────────

class ForbiddenPageTests(RBACTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.norole_user)

    def test_403_page_contains_access_denied(self):
        resp = self.client.get(reverse("rbac:user_list"))
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "Access Denied", status_code=403)

    def test_403_page_contains_dashboard_link(self):
        resp = self.client.get(reverse("rbac:user_list"))
        self.assertContains(resp, reverse("core:dashboard"), status_code=403)
