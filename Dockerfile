# SPDX-License-Identifier: MIT
#
# Mirrors CI (STEP-08 D4): same Python minor version CI pins, same install
# source, same offline default. An image that installed differently from the
# environment the tests ran in would be a second, unverified build.
#
# Two stages so the shipped image carries no git, no compiler and no uv. The
# builder needs git because `analystkit` is a git dependency by necessity; the
# runtime does not, and leaving it in would widen the attack surface of an
# image whose whole job is running a CLI offline.

# Explicit distro rather than bare `3.12-slim`, so a Debian release change is a
# visible edit rather than something that happens on a Tuesday. Dependabot
# watches this line (see .github/dependabot.yml).
FROM python:3.12-slim-bookworm AS builder

# uv, for the same reason QUICKSTART names it as the reproducible path: it
# installs from uv.lock, which pins every version with hashes and pins the
# analystkit git dependency to an exact commit.
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    # Build the environment at the absolute path it will live at in the runtime
    # stage, not at ./.venv. A virtualenv is not relocatable: pip and uv write
    # the interpreter's absolute path into every console script's shebang, so a
    # venv created at /build/.venv and copied to /opt/ts-sentry/.venv produces
    # `#!/build/.venv/bin/python`, which does not exist at runtime.
    #
    # Found by running the image rather than by reading the Dockerfile. The
    # failure is worth naming because it reads as something else entirely:
    # `exec /opt/ts-sentry/.venv/bin/ts-sentry: no such file or directory`,
    # where the file that is missing is the *interpreter* named in the shebang
    # and not the script the message points at.
    UV_PROJECT_ENVIRONMENT=/opt/ts-sentry/.venv

# Lockfile and metadata first, so a change to source does not invalidate the
# dependency layer.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# --frozen: install exactly what the lockfile says and never re-resolve.
# --no-dev: the image runs the CLI, it does not run the test suite.
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Trust & Safety Sentry" \
      org.opencontainers.image.description="A governed agentic workbench for Trust & Safety scaled-abuse analysis" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/MohdSaifHussain/ts-sentry"

# Non-root. Nothing this image does needs root, and the CLI writes only into
# the working directory it is given.
RUN useradd --create-home --uid 10001 analyst

COPY --from=builder --chown=analyst:analyst /opt/ts-sentry/.venv /opt/ts-sentry/.venv

# The corpus, the prompt registry and the eval set are data the CLI resolves at
# runtime by relative path, so they ship with the image rather than being
# fetched. `fetch-policies` is the only verb that would use the network and it
# is never needed to run a session.
COPY --chown=analyst:analyst policies/ /opt/ts-sentry/policies/
COPY --chown=analyst:analyst prompts/ /opt/ts-sentry/prompts/
COPY --chown=analyst:analyst evals/ /opt/ts-sentry/evals/

ENV PATH="/opt/ts-sentry/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # The quality gate shells out to AnalystKit, whose progress-bar characters
    # raise UnicodeEncodeError under a non-UTF-8 console codec. Set here for the
    # same reason ts_sentry.data.quality sets it per subprocess.
    PYTHONIOENCODING=utf-8

USER analyst
WORKDIR /work

# No HEALTHCHECK, deliberately. This is a CLI that runs and exits, not a
# service, and a healthcheck on a container with no long-running process would
# report something meaningless.
ENTRYPOINT ["ts-sentry"]
CMD ["--help"]
