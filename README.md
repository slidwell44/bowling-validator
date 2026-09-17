# bowling-validator
A simple application that automatically subscribes to new email events from my Gmail account and automatically accepts the substitute bowler request

## Development container

1. Install Docker Desktop and the VS Code **Dev Containers** extension
   (`ms-vscode-remote.remote-containers`). Start Docker Desktop with Linux
   containers enabled (the WSL 2 backend is recommended on Windows).
2. Open this repository's root folder in VS Code, then run
   **Dev Containers: Reopen in Container** from the command palette.
3. Wait for setup to finish. `uv sync --locked` installs the Python version from
   `bowling-email-subscriber/.python-version` and dependencies from `uv.lock`.
4. In a new VS Code terminal, run:

   ```sh
   uv run --locked uvicorn main:create_app --factory --host 0.0.0.0 --port 8000 --reload
   ```

   Terminals start in `bowling-email-subscriber`. Open
   <http://localhost:8000/docs> to view the FastAPI API documentation. The app
   currently has no custom routes, so `/` returns 404.

The container includes uv 0.12.15, Python editor support, Pyrefly, and Ruff.
Pyrefly language services and type diagnostics are enabled, using the Pyrefly
version installed in the container's Python environment. Run `uv run pyrefly check`
from `bowling-email-subscriber` for command-line type checking.
The virtual
environment, downloaded Python, and uv cache live under `/home/vscode`, outside
the Windows checkout. This uses uv's
[project environment configuration](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path)
to avoid reusing a local Windows `.venv`. File polling enables auto-reload for
changes made through the Windows bind mount.

Run `uv sync --locked` in the terminal after pulling dependency changes. Use
`uv add <package>` to add a dependency, and commit both `pyproject.toml` and
`uv.lock`. Run **Dev Containers: Rebuild Container** after changing the container
configuration; rebuilding recreates its Python environment automatically.

If VS Code previously saved a host Python interpreter, run **Python: Select
Interpreter** and choose `/home/vscode/.venvs/bowling-email-subscriber/bin/python`.
If Docker reports that `dockerDesktopLinuxEngine` cannot be found, start Docker
Desktop and wait for its engine to become ready before reopening the container.

## Useful Commands

### Bypassing uv cached files in OneDrive

```cli
uv add "fastapi[standard]" --no-cache --link-mode copy
```

`--no-cache` uses a fresh temporary cache, avoiding the existing files linked in OneDrive.

`uv` defaults to hardlinking cached packages on Windows OS. So `--link-mode copy` copies the package files instead of hardlinking.
