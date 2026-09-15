# --- Stage 1: builder -------------------------------------------------
# Installs deps into an isolated location so build tools / caches never
# reach the final image (keeps runtime image smaller and more secure).
FROM python:3.11-slim AS builder

WORKDIR /build

# Copy only dependency metadata first so Docker can cache this layer
# and skip reinstalling deps when only application code changes.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

# tiktoken downloads its encoding on first use. Left to runtime that becomes a
# network call inside a container that may have no outbound access, turning a
# token count into a hard failure. Fetching it now — while the build still has
# network — bakes it into the image instead. The explicit cache dir matters:
# tiktoken's default is a temp directory, which would not survive into the
# runtime stage (or a container restart).
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && PYTHONPATH=/install/lib/python3.11/site-packages \
       python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# --- Stage 2: runtime ---------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages (not build tools) from the builder stage.
COPY --from=builder /install /usr/local
COPY src ./src

# The warmed cache has to be carried across explicitly — multi-stage builds
# share nothing by default — and the env var re-declared, because tiktoken
# looks the location up at runtime, not at build time.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
COPY --from=builder /opt/tiktoken_cache /opt/tiktoken_cache

# Run as a non-root user (basic container hardening).
RUN useradd --create-home appuser \
    && chown -R appuser:appuser /opt/tiktoken_cache
USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
