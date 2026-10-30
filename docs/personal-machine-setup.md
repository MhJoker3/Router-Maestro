# Personal Codex setup

This branch contains the Copilot duplicate-message fix, a Codex development
container, and a portable setup script for maximum advertised context windows
and automatic approval review. Generate the model catalog on each machine from
its installed Codex version and authenticated Router-Maestro server.

## Install on another machine

Install Git, Python 3.14 through uv, Docker Desktop/Engine, and Codex first.
On macOS the desktop app's bundled CLI can also be used; ensure `codex` is on
`PATH` and `codex debug models --bundled` succeeds.

```bash
git clone --branch users/MhJoker3/codex-machine-setup \
  https://github.com/MadSkittles/Router-Maestro.git
cd Router-Maestro
uv sync --python 3.14 --locked --extra dev
```

Build this branch's server image so the duplicate-message fix is included:

```bash
mkdir -p ~/.config/router-maestro ~/.local/share/router-maestro
docker compose -f docker-compose.dev.yml up --build -d
uv run --no-sync router-maestro context test
uv run --no-sync router-maestro auth login github-copilot
uv run --no-sync router-maestro model refresh
```

Use the browser device flow with your own GitHub account. An existing
`router-maestro` container on that machine must be stopped/renamed before
starting this Compose service. For Docker bind-mount permissions and other
server options, see [deployment](deployment.md#local-docker).

## Apply Codex preferences

Run these on the host where Codex is installed. The script uses the active
Router-Maestro context, keeps unrelated Codex settings, and backs up any existing
configuration and catalog under `~/.codex/backups/` before writing.

```bash
uv run --no-sync python scripts/setup_codex.py --dry-run
uv run --no-sync python scripts/setup_codex.py --model github-copilot/gpt-6-astra
```

Omit `--model` to retain an available current model, or choose another model
from `router-maestro model list`. The script:

- reads maximum prompt/total windows from the live server;
- synchronizes bundled bare-name aliases when exactly one provider matches;
- removes global window overrides so model-specific limits take effect;
- sets `approvals_reviewer = "auto_review"`, `approval_policy = "on-request"`,
  and `sandbox_mode = "workspace-write"`;
- preserves the installed Codex model instructions and its context safety margin.

Advertised limits are provider-dependent; this does not increase the provider's
actual capacity. Re-run the script after catalog changes or upgrading Codex.
For ordinary catalog refreshes, `router-maestro config codex` now also keeps
unambiguous bare-name context limits in sync.

Supply the local server key to the Codex launch environment without putting it
in this repository or printing it:

```bash
export ROUTER_MAESTRO_API_KEY="$(uv run --no-sync python -c \
  'from router_maestro.config.server import get_current_context_api_key; print(get_current_context_api_key() or "", end="")')"
codex --approve-for-me
```

For the macOS desktop app, before fully reopening it:

```bash
launchctl setenv ROUTER_MAESTRO_API_KEY "$ROUTER_MAESTRO_API_KEY"
```

Repeat the launch-environment step after signing out/restarting the machine.
Existing chats may retain their permission mode; select **Approve for me**
in that chat or start a new one. The script configures automatic review while
retaining the workspace sandbox. It does not copy desktop session databases,
authentication files, API keys, or device-specific app paths.

Validate the loaded catalog and settings:

```bash
codex debug models
codex doctor --summary
```

The optional Docker development environment is documented under
[Codex CLI development container](deployment.md#codex-cli-development-container).
