"""Distribution checks: wheel installs expose the real stdio server offline."""
import asyncio
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


def test_python_and_npm_releases_share_version_and_runtime_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    npm = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    runtime_inputs = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert pyproject["project"]["version"] == npm["version"]
    assert pyproject["project"]["license"] == "MIT"
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["project"]["dependencies"] == runtime_inputs
    assert "LICENSE" in npm["files"]


def test_built_wheel_installs_modules_and_completes_stdio_handshake(tmp_path):
    wheel_dir = tmp_path / "wheelhouse"
    install_dir = tmp_path / "installed"
    cwd = tmp_path / "unrelated-working-directory"
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    for directory in (wheel_dir, install_dir, cwd, config_dir, data_dir):
        directory.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(ROOT),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        assert {"auth.py", "graph_client.py", "server.py", "onedrive_mcp_setup.py"} <= names
        entry_points = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        metadata = wheel.read(entry_points).decode("utf-8")
        assert "onedrive-mcp = server:main" in metadata
        assert "onedrive-mcp-setup = onedrive_mcp_setup:main" in metadata
        assert any(name.endswith("/LICENSE") for name in names)

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(install_dir), str(wheels[0])],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(install_dir),
            "ONEDRIVE_MCP_CONFIG_DIR": str(config_dir),
            "ONEDRIVE_MCP_DATA_DIR": str(data_dir),
        }
    )
    env.pop("AZURE_CLIENT_ID", None)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-c", "import server; server.main()"],
        cwd=str(cwd),
        env=env,
    )

    async def handshake():
        with anyio.fail_after(30):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    assert tools == {
                        "search_onedrive",
                        "list_folder",
                        "get_item_metadata",
                        "download_file",
                        "get_drive_info",
                    }

    asyncio.run(handshake())
