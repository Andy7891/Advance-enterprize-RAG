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

# --- Stage 2: runtime ---------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages (not build tools) from the builder stage.
COPY --from=builder /install /usr/local
COPY src ./src

# Run as a non-root user (basic container hardening).
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
