from app.api.v1.router import _playground_enabled, v1_router


def test_playground_is_disabled_in_production_envs():
    assert _playground_enabled("production") is False
    assert _playground_enabled("prod") is False
    assert _playground_enabled(" PRODUCTION ") is False


def test_playground_is_enabled_outside_production():
    assert _playground_enabled("development") is True
    assert _playground_enabled("staging") is True
    assert _playground_enabled("") is True


def test_activity_routes_are_exposed_for_admin_proxy():
    """The Studio frontend calls activity through /admin-proxy.

    That proxy prepends /api/v1/admin, so the backend must expose admin
    aliases for the activity feed; otherwise the full-analysis button
    starts a pipeline but the checklist polls 404 forever.
    """
    paths = {route.path for route in v1_router.routes}

    assert "/sites/{site_id}/activity" in paths
    assert "/admin/sites/{site_id}/activity" in paths
    assert "/admin/sites/{site_id}/activity/last" in paths
    assert "/admin/sites/{site_id}/activity/current-run" in paths
