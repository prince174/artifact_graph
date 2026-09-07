import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("checkout", ["", "bootstrap-secret"])
def test_bootstrap_rejects_missing_or_reused_checkout_token(monkeypatch, checkout):
    root = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap"
    monkeypatch.syspath_prepend(str(root))
    for key, value in {"BITBUCKET_WORKSPACE": "test", "BB_BOOTSTRAP_EMAIL": "test@example.com",
                       "BB_BOOTSTRAP_TOKEN": "bootstrap-secret", "TC_ADMIN_TOKEN": "test-admin",
                       "BB_CHECKOUT_TOKEN": checkout}.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("audit_bootstrap", root / "bootstrap_cloud.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="separate read-only"):
        module.bootstrap_teamcity()


def test_tls_default_is_enabled():
    from app.config import Settings
    assert Settings(_env_file=None).verify_tls is True
