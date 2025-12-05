# Install uv package manager
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Change the working directory to the `sentinel` directory
WORKDIR /sentinel

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --link-mode=copy

# Copy the project into the image
ADD . /sentinel

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Expose port 5000
EXPOSE 5000

# Run webhook receiver
CMD ["uv", "run", "src/webhook_receiver.py"]