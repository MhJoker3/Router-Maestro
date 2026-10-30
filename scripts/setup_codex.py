"""Apply this branch's portable Codex preferences using the active RM context."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import tomlkit

from router_maestro.cli.client import get_admin_client
from router_maestro.cli.client_configs.base import _bare_upstream_model_id, _model_key
from router_maestro.cli.client_configs.codex import (
    _build_codex_model_catalog,
    _build_router_maestro_provider_table,
)
from router_maestro.config.settings import write_json_owner_only


def configure_codex(
    *,
    models: list[dict],
    endpoint: str,
    codex_home: Path,
    model: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Back up and merge local preferences without copying credentials."""
    catalog = _build_codex_model_catalog(models)
    if catalog is None:
        raise ValueError("Install Codex and ensure `codex debug models --bundled` works first")
    if not models:
        raise ValueError("The active Router-Maestro context has no available models")
    config_path = codex_home / "config.toml"
    catalog_path = codex_home / "router-maestro-models.json"
    config = tomlkit.parse(config_path.read_text()) if config_path.exists() else tomlkit.document()
    available = {_model_key(item) for item in models}
    bare_ids = {_bare_upstream_model_id(item) for item in models}
    selected = model or config.get("model")
    if selected not in available | bare_ids:
        if model is not None:
            raise ValueError(f"Model {model!r} is not in the active server catalog")
        selected = _model_key(models[0])
    config["model"] = selected
    config["model_provider"] = "router-maestro"
    config["model_catalog_json"] = str(catalog_path.resolve())
    config.pop("model_context_window", None)
    config.pop("model_auto_compact_token_limit", None)
    if "model_providers" not in config:
        config["model_providers"] = tomlkit.table()
    config["model_providers"]["router-maestro"] = _build_router_maestro_provider_table(
        endpoint.rstrip("/") + "/api/openai/v1"
    )
    config["approval_policy"] = "on-request"
    config["approvals_reviewer"] = "auto_review"
    config["sandbox_mode"] = "workspace-write"
    if "features" not in config:
        config["features"] = tomlkit.table()
    config["features"]["guardian_approval"] = True
    rendered = tomlkit.dumps(config)

    if not dry_run:
        codex_home.mkdir(parents=True, exist_ok=True)
        existing = [p for p in (config_path, catalog_path) if p.exists()]
        if existing:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            backup = codex_home / "backups" / f"router-maestro-{stamp}"
            backup.mkdir(parents=True, mode=0o700)
            for path in existing:
                shutil.copy2(path, backup / path.name)
        write_json_owner_only(catalog_path, catalog)
        config_path.write_text(rendered)
        config_path.chmod(0o600)
    return {"model": selected, "models": len(catalog["models"]), "config": str(config_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Select a model advertised by the active server")
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing files")
    args = parser.parse_args()
    client = get_admin_client()
    result = configure_codex(
        models=asyncio.run(client.list_models()),
        endpoint=client.endpoint,
        codex_home=args.codex_home.expanduser().resolve(),
        model=args.model,
        dry_run=args.dry_run,
    )
    print(f"{'Would configure' if args.dry_run else 'Configured'} {result['config']}")
    print(f"Model: {result['model']}; catalog entries: {result['models']}")
    print("Maximum advertised context per model; automatic review with the workspace sandbox.")
    print("Set ROUTER_MAESTRO_API_KEY in the Codex launch environment, then reopen Codex.")


if __name__ == "__main__":
    main()
