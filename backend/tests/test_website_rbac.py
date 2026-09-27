"""Phase 11 (Phase 10 of PHASE_11_WEBSITE_BUILDER_DESIGN.md): RBAC matrix
for READ_WEBSITE / MANAGE_WEBSITE / PUBLISH_WEBSITE."""

import pytest

from app.models.rbac import Permission, Role, role_has_permission


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN])
def test_owner_and_admin_have_full_website_access(role: Role) -> None:
    assert role_has_permission(role, Permission.READ_WEBSITE)
    assert role_has_permission(role, Permission.MANAGE_WEBSITE)
    assert role_has_permission(role, Permission.PUBLISH_WEBSITE)


def test_manager_has_full_website_access() -> None:
    assert role_has_permission(Role.MANAGER, Permission.READ_WEBSITE)
    assert role_has_permission(Role.MANAGER, Permission.MANAGE_WEBSITE)
    assert role_has_permission(Role.MANAGER, Permission.PUBLISH_WEBSITE)


def test_staff_can_edit_but_not_publish() -> None:
    assert role_has_permission(Role.STAFF, Permission.READ_WEBSITE)
    assert role_has_permission(Role.STAFF, Permission.MANAGE_WEBSITE)
    assert not role_has_permission(Role.STAFF, Permission.PUBLISH_WEBSITE)


def test_read_only_can_only_read() -> None:
    assert role_has_permission(Role.READ_ONLY, Permission.READ_WEBSITE)
    assert not role_has_permission(Role.READ_ONLY, Permission.MANAGE_WEBSITE)
    assert not role_has_permission(Role.READ_ONLY, Permission.PUBLISH_WEBSITE)


@pytest.mark.parametrize("role", [Role.TECHNICIAN, Role.ACCOUNTANT])
def test_operationally_unrelated_roles_have_no_website_access(role: Role) -> None:
    assert not role_has_permission(role, Permission.READ_WEBSITE)
    assert not role_has_permission(role, Permission.MANAGE_WEBSITE)
    assert not role_has_permission(role, Permission.PUBLISH_WEBSITE)
