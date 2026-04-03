FROM alpine:3.20

RUN mkdir /config
RUN mkdir /server

COPY alexa.py /server/alexa.py
COPY server.py /server/server.py
COPY requirements.txt /server/requirements.txt

ENV ASL_CONFIG_PATH="/config/"

RUN apk add --no-cache python3 py3-pip tini

RUN pip3 install --break-system-packages -r /server/requirements.txt
RUN rm /server/requirements.txt

EXPOSE 4000 4000
ENTRYPOINT ["/sbin/tini", "-s", "--", "python3","/server/server.py"]
