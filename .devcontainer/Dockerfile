FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

# Keep uv consistent across developer machines.
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /usr/local/bin/

# Store Python, packages, and the virtual environment on the Linux filesystem.
ENV UV_PROJECT_ENVIRONMENT=/home/vscode/.venvs/bowling-email-subscriber \
    UV_CACHE_DIR=/home/vscode/.cache/uv \
    UV_PYTHON_INSTALL_DIR=/home/vscode/.local/share/uv/python \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    WATCHFILES_FORCE_POLLING=true
