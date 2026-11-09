"""`plugin.yaml` is valid for the SDK's manifest helpers and matches the plugin's code."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from horae_sdk import Permissions, dump_manifest
from horae_sdk import manifest as build_manifest

from mcp_brief import VERSION, manifest
from mcp_brief.__main__ import HEADER, main

ROOT = Path(__file__).parent.parent
PLUGIN_YAML = ROOT / "plugin.yaml"


def on_disk() -> dict[str, Any]:
    document = yaml.safe_load(PLUGIN_YAML.read_text("utf-8"))
    assert isinstance(document, dict)
    return document


def test_plugin_yaml_passes_the_sdk_checks() -> None:
    """Rebuilt from the file alone, so a hand edit is checked too (`ManifestError`)."""
    document = on_disk()
    perms = document["permissions"]
    rebuilt = build_manifest(
        name=document["name"],
        version=document["version"],
        provides=document["provides"],
        permissions=Permissions(
            intents=perms.get("intents", []),
            network=perms.get("network", []),
            secrets=perms.get("secrets", []),
            optional_secrets=perms.get("optional_secrets", []),
        ),
        command=document["runtime"]["command"],
        horae=document["horae"],
        description=document.get("description"),
        license=document.get("license"),
        homepage=document.get("homepage"),
    )
    assert rebuilt == document


def test_plugin_yaml_is_what_the_code_declares() -> None:
    """Regenerate with `uv run python -m mcp_brief --manifest > plugin.yaml` after a change."""
    assert on_disk() == manifest()
    assert PLUGIN_YAML.read_text("utf-8") == HEADER + dump_manifest(manifest())


def test_main_prints_the_manifest(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--manifest"])
    assert capsys.readouterr().out == PLUGIN_YAML.read_text("utf-8")


def test_main_refuses_unknown_arguments() -> None:
    with pytest.raises(SystemExit, match="usage"):
        main(["--nope"])


def test_the_permissions_are_the_least_the_plugin_needs() -> None:
    permissions = on_disk()["permissions"]
    assert "secrets" not in permissions  # every token is optional (core decision 56)
    assert permissions["optional_secrets"] == [f"MCP_BRIEF_TOKEN_{n}" for n in range(1, 9)]
    assert permissions["intents"] == ["note.replace_owned_block", "task.create", "notify.push"]
    assert "network" not in permissions  # per instance: the services are the operator's own
    assert on_disk()["provides"] == {"sources": ["mcp-brief.collect"]}


def test_the_version_is_the_same_everywhere() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert f'version = "{VERSION}"' in pyproject
    assert on_disk()["version"] == VERSION == "0.1.1"
    assert f"## {VERSION}" in (ROOT / "CHANGELOG.md").read_text("utf-8")
