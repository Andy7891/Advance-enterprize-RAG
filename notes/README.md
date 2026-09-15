# Notes Index

Per-step build notes, split out of the original monolithic `NOTES.md` for easier
navigation. Each file is self-contained: what was built, why, key concepts, how it connects
to the pipeline, and a "how to do this yourself" section. Chronological order below.

1. [Step 1.1 — pyproject.toml with pinned dependencies](step-1.1-pyproject.md)
2. [Step 1.1 (cont'd) — Dockerfile + docker-compose](step-1.1-docker.md)
3. [Step 1.2 — CI pipeline (.github/workflows/ci.yml)](step-1.2-ci-pipeline.md)
4. [Step 2.1 — DocumentChunk data contract (schemas.py)](step-2.1-document-chunk-schema.md)
5. [Step 2.2 — Document loader (loader.py)](step-2.2-document-loader.md)
6. [Fix — ModuleNotFoundError in Jupyter (kernel registration)](fix-modulenotfound-jupyter-kernel.md)
7. [Fix #2 — ModuleNotFoundError persisted (packaging bug)](fix-modulenotfound-packaging-bug.md)
8. [Step 2.2 (Microstep 2) — Semantic chunker (chunker.py)](step-2.2-semantic-chunker.md)

See also [../decision.md](../decision.md) (scannable decision log) and
[../LEARNING_LOG.md](../LEARNING_LOG.md) (date / concept / resources / open questions table).
