import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit(monkeypatch):
    import nhso_right_check.srm as srm_mod
    monkeypatch.setattr(srm_mod, "_BLOCKED_UNTIL", 0.0)
    yield
