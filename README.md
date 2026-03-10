# your-issue-is-unclear

`your-issue-is-unclear` is the bot display name and CLI name for this project.

`your-issue-is-unclear` is a local Python worker that watches GitHub Issues, asks clarification questions when requirements are unclear, analyzes a local checkout with a pluggable agent backend, and writes the result back to GitHub comments and labels.

## Current status

- Initial MVP skeleton is implemented.
- The first agent backend is `codex`.
- Authentication is based on a private GitHub App.
- Runtime data uses OS-standard app directories via `platformdirs`.

## Requirements

- Python 3.11+
- `uv`
- `git`
- a local agent CLI
  - initial backend: `codex`
- a private GitHub App with:
  - `Issues: Read & write`
  - `Contents: Read`
  - `Metadata: Read`
- for personal Projects sync: a `PAT classic` with `project`
  - add `repo` too if you sync private repository issues into the Project

## Quick start

1. Create a virtual environment and install dependencies.

```bash
uv sync
```

2. Copy the example env file and fill in GitHub App credentials.

```bash
cp .env.example .env
```

3. Copy the repo config example and register at least one repository.

```bash
cp config/repos.example.toml config/repos.toml
```

4. Run bootstrap.

```bash
uv run your-issue-is-unclear bootstrap
```

5. Run one worker iteration.

```bash
uv run your-issue-is-unclear worker --once
```

Optional: launch the interactive command selector.

```bash
uv run your-issue-is-unclear ui
```

## Environment variables

- `GIA_GITHUB_APP_ID`
- `GIA_GITHUB_APP_PRIVATE_KEY_PATH`
- `GIA_GITHUB_PROJECT_TOKEN` (optional, required for personal GitHub Projects sync)
- `GIA_GITHUB_API_BASE_URL` (optional)
- `GIA_STATE_DIR` (optional)
- `GIA_DB_PATH` (optional)
- `GIA_CHECKOUT_ROOT` (optional)
- `GIA_LOG_ROOT` (optional)
- `GIA_CLARIFICATION_DEBOUNCE_SECONDS` (optional)
- `GIA_ACTIVE_CLARIFICATION_POLLING_SECONDS` (optional)
- `GIA_CLARIFICATION_TIMEOUT_SECONDS` (optional)
- `GIA_ESTIMATE_TIMEOUT_SECONDS` (optional)
- `GIA_DEFAULT_AGENT_BACKEND` (optional)
- `GIA_DEFAULT_AGENT_MODEL` (optional)
- `GIA_DEFAULT_AGENT_REASONING_EFFORT` (optional)
- `GIA_DEFAULT_AGENT_ROLE` (optional)
- `GIA_DEFAULT_AGENT_LANGUAGE` (optional)
- `GIA_LOG_LEVEL` (optional)

## Logs

- logs are still emitted to stdout
- logs are also written to `app.log` under `GIA_LOG_ROOT`
- if `GIA_LOG_ROOT` is unset, the default OS log directory from `platformdirs` is used
- log files rotate at midnight UTC and keep 7 backups
- uncaught command failures and HTTP status details are recorded in the log file

## CLI

```bash
uv run your-issue-is-unclear --help
```

Available commands:

- `bootstrap`
- `worker`
- `refresh`
- `ui`

Interactive launcher:

```bash
uv run your-issue-is-unclear ui
```

The `ui` command opens a menu-driven terminal UI so you can choose:

- `bootstrap` with all enabled repos or one enabled repo
- `worker` with `--once` or `--no-once`
- `refresh` with a configured repo and issue number

Before execution, it shows a summary and the equivalent CLI command and asks for confirmation.

## Manual refresh

After `bootstrap`, the worker also creates the `ai:refresh` label.

- add `ai:refresh` to an issue to request a one-time reevaluation
- the worker removes `ai:refresh` after consuming it
- `/refresh` issue comments still work too

## Config file

The default config file path is [config/repos.toml](config/repos.toml).

An example is included at [config/repos.example.toml](config/repos.example.toml).

Agent selection settings:

- `GIA_DEFAULT_AGENT_BACKEND` sets the global backend default
- `GIA_DEFAULT_AGENT_MODEL` sets the global model passed to the agent CLI
- `GIA_DEFAULT_AGENT_REASONING_EFFORT` sets the global reasoning effort passed to the agent CLI
- `GIA_DEFAULT_AGENT_ROLE` sets the agent persona used in the prompt, defaulting to `Android developer`
- `GIA_DEFAULT_AGENT_LANGUAGE` sets the language for model-generated text such as reasons and clarification prompts
- `agent_backend_override` overrides the backend for one repo
- `agent_model_override` overrides the model for one repo
- `agent_role_override` overrides the prompt persona for one repo
- `agent_language_override` overrides the language for one repo
- `project_v2_impact_field_name` and `project_v2_create_if_missing` can be set under `[defaults]` and inherited by each repo
- `project_v2_priority_field_name` can also be set under `[defaults]`

If no model or reasoning effort is configured here, the worker falls back to the Codex CLI defaults from `~/.codex/config.toml`.

## Project sync

If you want GitHub Projects v2 sync, add these fields to a repo entry:

- `project_v2_impact_field_name`
- `project_v2_priority_field_name` (optional)
- `project_v2_create_if_missing`

If those values are the same for all repositories, you can put them under `[defaults]` instead.

`project_v2_title` is optional. If you omit it, the worker derives `<repo>_project_issue_prioritization`.

With `GIA_GITHUB_PROJECT_TOKEN`, the worker uses your personal token for Projects only:

- it finds a personal Project by `project_v2_title` or the derived default title
- if `project_v2_create_if_missing = true`, it creates the Project when missing
- it links the repository to the Project during bootstrap
- it creates the configured `Number` fields when missing
- it writes a single representative total-impact value using the midpoint of the estimated range
- if `project_v2_priority_field_name` is configured, it creates that `Number` field so you can manage priority manually in GitHub Projects
- if you prefer a native GitHub formula or derived column, create it directly in the Project UI; the worker only writes `Total Impact`

Example:

```toml
[defaults]
project_v2_impact_field_name = "Total Impact"
project_v2_priority_field_name = "Priority"
project_v2_create_if_missing = true
```

Issue comments and labels still use the GitHub App installation token.

If you already have a Project, you can still point at it explicitly with `project_v2_url` instead of `project_v2_title`.
