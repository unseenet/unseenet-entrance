# A web entrance (UN-RFC 0043) in a container: the entrance, Tor and certbot on the
# node image (core, chiavdf, Tor, tini). The web client, Tor's onion keys and the
# certificates are kept in /data.
#
#   docker run -d --name entrance -v entrance:/data -p 80:8080 -p 443:8443 \
#       -e TLS=auto ghcr.io/unseenet/entrance
#
# See the docs ("In Docker") and compose.yaml.
ARG NODE_IMAGE=ghcr.io/unseenet/node:latest

FROM ${NODE_IMAGE} AS build
USER root
RUN apk add --no-cache gcc musl-dev python3-dev libffi-dev
COPY pyproject.toml README.md LICENSE /src/
COPY src /src/src
RUN /opt/unseenet/bin/pip install --no-cache-dir /src \
 && python3 -m venv /opt/certbot && /opt/certbot/bin/pip install --no-cache-dir "certbot>=5.6" \
 && find /opt/unseenet /opt/certbot -name __pycache__ -type d -prune -exec rm -rf {} +

FROM ${NODE_IMAGE}
USER root
COPY --from=build /opt/unseenet /opt/unseenet
COPY --from=build /opt/certbot /opt/certbot
COPY docker/entrypoint.sh /usr/local/bin/unseenet-entrance-entrypoint
RUN chmod 755 /usr/local/bin/unseenet-entrance-entrypoint
ENV UNSEENET_DATA=/data
VOLUME /data
EXPOSE 8080 8443
HEALTHCHECK --interval=60s --timeout=10s --start-period=900s \
  CMD python3 -c "import urllib.request, ssl; p = open('/data/.port').read().strip(); \
c = ssl._create_unverified_context(); \
urllib.request.urlopen(('https' if p == '8443' else 'http') + '://127.0.0.1:' + p + '/', timeout=5, context=c)"
LABEL org.opencontainers.image.title="Unseenet web entrance" \
      org.opencontainers.image.description="A web entrance to Unseenet (UN-RFC 0043): the graphical client in a browser, over Tor" \
      org.opencontainers.image.source="https://github.com/unseenet/unseenet-entrance" \
      org.opencontainers.image.url="https://unseenet.org/entrance/" \
      org.opencontainers.image.licenses="Apache-2.0"
ENTRYPOINT ["/sbin/tini", "--", "unseenet-entrance-entrypoint"]
CMD []
