# bowling-validator

A Python/FastAPI project intended to watch Gmail for substitute bowler requests
and automatically accept them. The email subscription and acceptance workflow is
still under development.

## Current functionality

- `GET /` redirects to the interactive API documentation at `/docs`.
- `GET /hello` returns `{"message": "Hello, World!"}`.
- Application name and version are loaded from environment settings.
- Gmail integration has a service skeleton and dependency provider. Its endpoint
  module is empty, and no Gmail router is registered with the application.
- Gmail label fetching, OAuth authorization, email subscriptions, and automatic
  request acceptance are not implemented. OpenAI has an API key setting but no
  integration yet.

## Project structure

```text
.
├── .devcontainer/                 # Development image and VS Code container setup
├── .vscode/                       # Editor settings and FastAPI debug configuration
├── .env                           # Local environment variables (not committed)
└── bowling-email-subscriber/
    ├── .python-version            # Python 3.14.7
    ├── pyproject.toml             # Dependencies and Ruff/Pyrefly configuration
    ├── uv.lock                    # Locked dependencies
    ├── main.py                    # FastAPI application factory and basic routes
    ├── config.py                  # Application, Google OAuth, and OpenAI settings
    ├── common/
    │   └── services.py            # BaseService skeleton
    └── gmail_subscriber/
        ├── dependencies.py        # GmailSubscriberService provider
        ├── endpoints.py           # Placeholder for Gmail routes
        └── services.py            # Gmail credentials and label-fetching skeleton
```

## Configuration

Create `.env` at the repository root with all five settings below. All are
required by `config.py`, including the Google and OpenAI settings, even when
only using `/hello`.

```dotenv
APPLICATION_NAME=Bowling Validator
APPLICATION_VERSION=0.1.0
GOOGLE_OAUTH_CLIENT_ID=replace-with-your-client-id
GOOGLE_OAUTH_CLIENT_SECRET=replace-with-your-client-secret
OPENAI_API_KEY=replace-with-your-api-key
```

Placeholder values are sufficient to start the current basic routes; they do
not enable Gmail or OpenAI functionality. Keep real credentials out of version
control.

The settings classes look for `.env` relative to the working directory. The
terminal command below explicitly loads the repository-root file into the
process environment with `--env-file ../.env`. The VS Code **FastAPI: Debug**
configuration also loads the root `.env`.

## Run locally

Install uv and use Python 3.14 or newer (the project pins 3.14.7). From the
repository root, after creating `.env`:

```sh
cd bowling-email-subscriber
uv sync --locked
uv run --locked uvicorn main:create_app --factory --host 0.0.0.0 --port 8000 --reload --env-file ../.env
```

Open <http://localhost:8000/docs> to explore the API, or
<http://localhost:8000/hello> to check the response. `main.py` exposes an
application factory, so the launch command uses `main:create_app --factory`.

## Development container

1. Install Docker Desktop and the VS Code **Dev Containers** extension
   (`ms-vscode-remote.remote-containers`). Start Docker Desktop with Linux
   containers enabled (the WSL 2 backend is recommended on Windows).
2. Open this repository's root folder in VS Code, then run
   **Dev Containers: Reopen in Container** from the command palette.
3. Wait for setup to finish. The container runs
   `uv sync --locked --project bowling-email-subscriber` to install the pinned
   Python version and dependencies.
4. Configure the root `.env` as described above. New terminals start in
   `bowling-email-subscriber`, so run:

   ```sh
   uv run --locked uvicorn main:create_app --factory --host 0.0.0.0 --port 8000 --reload --env-file ../.env
   ```

Port 8000 is forwarded by the container. Alternatively, select **FastAPI: Debug**
in VS Code's Run and Debug panel.

The container includes uv 0.12.15, Python editor support, Pyrefly, and Ruff. Its
virtual environment, downloaded Python, and uv cache live under `/home/vscode`,
outside the checkout, to avoid reusing a local Windows `.venv`. File polling
enables auto-reload for changes made through the Windows bind mount.

Run **Dev Containers: Rebuild Container** after changing the container
configuration. If VS Code previously saved a host Python interpreter, select
`/home/vscode/.venvs/bowling-email-subscriber/bin/python` with **Python: Select
Interpreter**. If Docker reports that `dockerDesktopLinuxEngine` cannot be
found, start Docker Desktop and wait for its engine before reopening the
container.

## Development commands

Run these from `bowling-email-subscriber`:

```sh
uv sync --locked          # Install dependencies after pulling changes
uv run pyrefly check      # Type checking
uv run ruff check .       # Linting, including import ordering
uv run ruff format .      # Formatting
uv add <package>          # Add a dependency; commit pyproject.toml and uv.lock
```

There is currently no automated test suite in the repository.

For uv cache or hardlink issues in a Windows OneDrive checkout, use a temporary
cache and copy package files when adding dependencies, for example:

```sh
uv add "fastapi[standard]" --no-cache --link-mode copy
```

The development container already sets `UV_LINK_MODE=copy`.
