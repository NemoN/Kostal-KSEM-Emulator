FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 ksem \
    && useradd --uid 10001 --gid 10001 --no-create-home ksem \
    && mkdir /data && chown 10001:10001 /data
COPY ksem_emulator/ ./ksem_emulator/
COPY ksemServer.py shellyPoller.py LICENSE ./
USER 10001:10001
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-m", "ksem_emulator.healthcheck"]
ENTRYPOINT ["python", "ksemServer.py"]
