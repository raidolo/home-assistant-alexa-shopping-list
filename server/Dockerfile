FROM python:3.12-slim

RUN mkdir -p /config /server

COPY alexa.py /server/alexa.py
COPY browser_backend.py /server/browser_backend.py
COPY server.py /server/server.py
COPY requirements.txt /server/requirements.txt

ENV ASL_CONFIG_PATH="/config/"
ENV PLAYWRIGHT_BROWSERS_PATH="/ms-playwright"

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r /server/requirements.txt
RUN python -m playwright install --with-deps --only-shell chromium
RUN rm /server/requirements.txt

EXPOSE 4000 4000
ENTRYPOINT ["/usr/bin/tini", "-s", "--", "python","/server/server.py"]
