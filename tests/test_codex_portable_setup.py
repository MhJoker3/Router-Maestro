"""Portable preferences and bare model aliases should survive machine changes."""

import importlib.util
import json
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from router_maestro.cli.client_configs import codex


@pytest.fixture
def catalog(monkeypatch):
    value = {
        "models": [
            {
                "slug": "gpt-6-astra",
                "context_window": 272_000,
                "max_context_window": 872_000,
                "auto_compact_token_limit": 250_000,
                "base_instructions": "keep model-specific instructions",
                "node_repl_auto_review_required": True,
            }
        ]
    }
    monkeypatch.setattr(codex, "_load_bundled_codex_catalog", lambda: value)
    return value


def _model(provider="github-copilot"):
    return {
        "id": f"{provider}/gpt-6-astra",
        "provider": provider,
        "name": "Astra",
        "max_prompt_tokens": 1_050_000,
        "max_context_window_tokens": 1_050_000,
    }


def test_bare_alias_uses_live_maximum_without_changing_bundled_instructions(catalog):
    original = deepcopy(catalog)
    built = codex._build_codex_model_catalog([_model()])
    bare, qualified = built["models"]
    assert bare["context_window"] == qualified["context_window"] == 1_050_000
    assert bare["max_context_window"] == qualified["max_context_window"] == 1_050_000
    assert bare["auto_compact_token_limit"] is None
    assert bare["base_instructions"] == original["models"][0]["base_instructions"]
    assert bare["node_repl_auto_review_required"] is True
    assert catalog == original


def test_ambiguous_bare_alias_retains_bundled_limits(catalog):
    built = codex._build_codex_model_catalog([_model(), _model("openai")])
    assert built["models"][0] == catalog["models"][0]


@pytest.mark.parametrize("dry_run", [True, False])
def test_setup_merges_preferences_with_backups_and_no_credentials(catalog, tmp_path, dry_run):
    spec = importlib.util.spec_from_file_location(
        "setup_codex", Path(__file__).parents[1] / "scripts" / "setup_codex.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = (
        'model = "gpt-6-astra"\nmodel_context_window = 272000\n[desktop]\ncodeFontSize = 13\n'
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(original)
    module.configure_codex(
        models=[_model()], endpoint="http://127.0.0.1:8080/", codex_home=tmp_path, dry_run=dry_run
    )
    if dry_run:
        assert config_path.read_text() == original
        assert list(tmp_path.iterdir()) == [config_path]
        return
    config = tomllib.loads(config_path.read_text())
    assert config["model"] == "gpt-6-astra"
    assert config["desktop"]["codeFontSize"] == 13
    assert "model_context_window" not in config
    assert config["approvals_reviewer"] == "auto_review"
    assert config["approval_policy"] == "on-request"
    assert config["sandbox_mode"] == "workspace-write"
    assert config["model_providers"]["router-maestro"]["env_key"] == "ROUTER_MAESTRO_API_KEY"
    assert "api_key" not in config
    assert next((tmp_path / "backups").glob("*/config.toml")).read_text() == original
    models = json.loads((tmp_path / "router-maestro-models.json").read_text())["models"]
    assert models[0]["context_window"] == 1_050_000
