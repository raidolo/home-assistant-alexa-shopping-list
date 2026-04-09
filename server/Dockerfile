FROM python:3.12-slim

RUN mkdir -p /config /server

ENV DEBIAN_FRONTEND=noninteractive
ENV ASL_CONFIG_PATH="/config/"
ENV PLAYWRIGHT_BROWSERS_PATH="/ms-playwright"

COPY requirements.txt /server/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /server/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm /server/requirements.txt

COPY alexa.py /server/alexa.py
COPY browser_backend.py /server/browser_backend.py
COPY server.py /server/server.py

EXPOSE 4000 4000
ENTRYPOINT ["/usr/bin/tini", "-s", "--", "python","/server/server.py"]
