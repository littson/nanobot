import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.config.schema import Config
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.openai_codex_provider import _strip_model_prefix
from nanobot.providers.registry import find_by_model
from nanobot.utils.llm_metrics import extract_cached_tokens, resolve_provider_name

runner = CliRunner()


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with patch("nanobot.config.loader.get_config_path") as mock_cp, \
         patch("nanobot.config.loader.save_config") as mock_sc, \
         patch("nanobot.config.loader.load_config") as mock_lc, \
         patch("nanobot.utils.helpers.get_workspace_path") as mock_ws:

        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_sc.side_effect = lambda config: config_file.write_text("{}")

        yield config_file, workspace_dir

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """No existing config — should create from scratch."""
    config_file, workspace_dir = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "nanobot is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


def test_config_matches_github_copilot_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "github-copilot/gpt-5.3-codex"

    assert config.get_provider_name() == "github_copilot"


def test_config_matches_openai_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.1-codex"

    assert config.get_provider_name() == "openai_codex"


def test_find_by_model_prefers_explicit_prefix_over_generic_codex_keyword():
    spec = find_by_model("github-copilot/gpt-5.3-codex")

    assert spec is not None
    assert spec.name == "github_copilot"


def test_litellm_provider_canonicalizes_github_copilot_hyphen_prefix():
    provider = LiteLLMProvider(default_model="github-copilot/gpt-5.3-codex")

    resolved = provider._resolve_model("github-copilot/gpt-5.3-codex")

    assert resolved == "github_copilot/gpt-5.3-codex"


def test_openai_codex_strip_prefix_supports_hyphen_and_underscore():
    assert _strip_model_prefix("openai-codex/gpt-5.1-codex") == "gpt-5.1-codex"
    assert _strip_model_prefix("openai_codex/gpt-5.1-codex") == "gpt-5.1-codex"


def test_resolve_provider_name_uses_explicit_name():
    record = {
        "provider": "custom",
        "provider_name": "github-copilot",
        "model": "github-copilot/gpt-5.3-codex",
    }

    assert resolve_provider_name(record) == "github_copilot"


def test_resolve_provider_name_uses_model_before_backend():
    record = {
        "provider": "litellm",
        "model": "openrouter/anthropic/claude-3.5-sonnet",
    }

    assert resolve_provider_name(record) == "openrouter"


def test_resolve_provider_name_supports_vertex_alias():
    record = {
        "provider": "vertex_native",
        "model": "google/gemini-2.5-flash",
    }

    assert resolve_provider_name(record) == "vertex"


def test_extract_cached_tokens_from_nested_details():
    usage = {"prompt_tokens_details": {"cached_tokens": 321}}

    assert extract_cached_tokens(usage) == 321


def test_extract_cached_tokens_from_vertex_usage_metadata():
    usage = {"cachedContentTokenCount": 88}

    assert extract_cached_tokens(usage) == 88


def test_metrics_group_by_provider_prefers_provider_name(tmp_path: Path):
    metrics_path = tmp_path / "llm_metrics.jsonl"
    rows = [
        {
            "timestamp": "2026-03-04T00:00:00+00:00",
            "provider": "custom",
            "provider_name": "vertex",
            "model": "google/gemini-2.5-flash",
            "elapsed_ms": 10,
            "cached_tokens": 4,
            "error": False,
        },
        {
            "timestamp": "2026-03-04T00:01:00+00:00",
            "provider": "vertex_native",
            "provider_name": "vertex",
            "model": "google/gemini-2.5-flash",
            "elapsed_ms": 20,
            "cached_tokens": 8,
            "error": False,
        },
    ]
    metrics_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["metrics", "--group-by", "provider", "--path", str(metrics_path), "--tail", "50"],
    )

    assert result.exit_code == 0
    assert "Grouped by provider" in result.stdout
    assert "vertex" in result.stdout
    assert "vertex_native" not in result.stdout
    assert "Cached Tokens: 12" in result.stdout
