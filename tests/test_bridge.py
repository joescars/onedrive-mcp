import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

from scripts import run_mcp_bridge as bridge


@pytest.fixture(autouse=True)
def bridge_environment(monkeypatch):
    for key in ("MCPO_API_KEY", "MCPO_HOST", "MCPO_PORT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", ["run_mcp_bridge.py"])


def test_missing_api_key_refuses_start(capsys):
    assert bridge.main() == 1
    assert "MCPO_API_KEY is not set" in capsys.readouterr().err


@pytest.mark.parametrize("port", ["bad", "0", "65536"])
def test_invalid_port_is_actionable(monkeypatch, capsys, port):
    monkeypatch.setenv("MCPO_API_KEY", "dummy-secret")
    monkeypatch.setenv("MCPO_PORT", port)
    assert bridge.main() == 1
    assert "between 1 and 65535" in capsys.readouterr().err


def test_launch_passes_key_in_process_and_requires_strict_auth(monkeypatch, capsys, tmp_path):
    run = AsyncMock()
    module = ModuleType("mcpo.main")
    module.run = run
    monkeypatch.setitem(sys.modules, "mcpo", ModuleType("mcpo"))
    monkeypatch.setitem(sys.modules, "mcpo.main", module)
    monkeypatch.setenv("MCPO_API_KEY", "dummy-secret")
    monkeypatch.chdir(tmp_path)
    assert bridge.main() == 0
    kwargs = run.call_args.kwargs
    assert kwargs["api_key"] == "dummy-secret"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["strict_auth"] is True
    assert kwargs["server_command"] == [sys.executable, str(bridge.PROJECT_ROOT / "server.py")]
    output = capsys.readouterr()
    assert "dummy-secret" not in output.out + output.err
    assert "dummy-secret" not in " ".join(sys.argv)


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem mode guarantee")
def test_env_file_permissions_are_required(monkeypatch, tmp_path, capsys):
    path = tmp_path / "bridge.env"
    path.write_text("MCPO_API_KEY=dummy-secret\n")
    path.chmod(0o644)
    monkeypatch.setattr(sys, "argv", ["bridge.py", str(path)])
    assert bridge.main() == 1
    assert "REFUSING" in capsys.readouterr().err


def test_env_exports_take_precedence(monkeypatch, tmp_path):
    path = tmp_path / "bridge.env"
    path.write_text("MCPO_API_KEY='file-secret'\nMCPO_PORT=9876\n")
    monkeypatch.setenv("MCPO_API_KEY", "exported-secret")
    bridge._load_env_file(path)
    assert os.environ["MCPO_API_KEY"] == "exported-secret"
    assert os.environ["MCPO_PORT"] == "9876"
