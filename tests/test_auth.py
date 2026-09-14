"""Unit tests for auth.py configuration handling (no real MSAL network calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import auth


def test_get_client_id_raises_when_placeholder(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "00000000-0000-0000-0000-000000000000")
    with pytest.raises(auth.AuthConfigError):
        auth.get_client_id()


def test_get_client_id_raises_when_empty(monkeypatch):
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    with pytest.raises(auth.AuthConfigError):
        auth.get_client_id()


def test_get_client_id_returns_real_value(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "real-client-id-1234")
    assert auth.get_client_id() == "real-client-id-1234"


def test_get_tenant_id_defaults_to_consumers(monkeypatch):
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    assert auth.get_tenant_id() == "consumers"


def test_get_tenant_id_respects_env(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "common")
    assert auth.get_tenant_id() == "common"


def test_get_authority_uses_tenant(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "consumers")
    assert auth.get_authority() == "https://login.microsoftonline.com/consumers"


def test_get_token_cache_path_expands(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_CACHE_PATH", str(tmp_path / "cache.bin"))
    assert auth.get_token_cache_path() == (tmp_path / "cache.bin").resolve()
