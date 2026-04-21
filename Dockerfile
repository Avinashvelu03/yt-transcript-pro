FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build \
 && python -m build --wheel

FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Avinashvelu03/yt-transcript-pro"
LABEL org.opencontainers.image.description="Production-grade YouTube transcript extractor"
LABEL org.opencontainers.image.licenses="MIT"

# Non-root user
RUN useradd --create-home --shell /bin/bash yttp
WORKDIR /app
RUN chown yttp:yttp /app

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

USER yttp
ENTRYPOINT ["yttp"]
CMD ["--help"]
