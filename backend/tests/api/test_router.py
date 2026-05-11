from app.api.v1.router import _playground_enabled


def test_playground_is_disabled_in_production_envs():
    assert _playground_enabled("production") is False
    assert _playground_enabled("prod") is False
    assert _playground_enabled(" PRODUCTION ") is False


def test_playground_is_enabled_outside_production():
    assert _playground_enabled("development") is True
    assert _playground_enabled("staging") is True
    assert _playground_enabled("") is True
