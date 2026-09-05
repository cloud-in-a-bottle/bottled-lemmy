# Lemmy + lemmy-ui + Postgres + an OIDC-bridge sidecar packaged
# as a single Cloud in a Bottle container.
#
# Layout:
#
#   browser → Cloud in a Bottle router (subdomain lemmy.<zone>; verifies
#                              owner zone_auth, stamps
#                              X-OpenHost-Is-Owner)
#          → container :8080  (nginx)
#                ├─ /_oidc/*  → OIDC bridge (Python on :7000)
#                ├─ /api/*    → Lemmy backend (:8536)
#                ├─ /pictrs/* → Lemmy backend (:8536)
#                ├─ /sso-bounce → OIDC-redirect HTML page (the
#                                bouncer; written by nginx itself
#                                as a tiny inline HTML doc)
#                └─ /         → bouncer logic (when owner +
#                              no Lemmy session) or lemmy-ui
#                              (:1234) for everything else.
#
# Internal services launched by start.sh (no s6, just bash with
# `wait -n` like the openhost-sftp/syncthing/joplin pattern):
#
#   * postgres (16) — Lemmy's metadata DB. Data dir under
#                     $BOTTLE_APP_DATA_DIR/postgres.
#   * lemmy_server  — the Rust API + ActivityPub backend.
#                     Reads $LEMMY_CONFIG_LOCATION.
#   * lemmy-ui      — Node SSR frontend (lemmy-ui/dist).
#   * oidc-bridge   — Python Starlette OIDC provider using the
#                     same shape as openhost-immich.
#   * nginx         — request router on :8080.

# Stage 1: lemmy-ui binaries.
# We pin Lemmy 1.0.0-beta.1 because OAuth/
# OIDC support — required for our SSO flow — landed only in the
# 1.0 series; 0.19.x does not expose the OAuth provider API
# endpoint we need.  See LemmyNet/lemmy#4881.
FROM dessalines/lemmy-ui:1.0.0-beta.1 AS ui-source

# Stage 2: lemmy backend.
FROM dessalines/lemmy:1.0.0-beta.1 AS backend-source

# Stage 3: pict-rs image service. The binary is statically linked;
# media tools come from the final Debian image.
FROM asonix/pictrs:0.5.24 AS pictrs-source

# Stage 4: ImageMagick 7. Debian Bookworm only packages ImageMagick 6,
# while pict-rs requires the ImageMagick 7 `magick` interface.
FROM debian:bookworm-slim AS imagemagick-source
ARG IMAGEMAGICK_VERSION=7.1.1-47
ARG IMAGEMAGICK_SHA256=818e21a248986f15a6ba0221ab3ccbaed3d3abee4a6feb4609c6f2432a30d7ed
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libheif-dev \
        libjpeg62-turbo-dev \
        libltdl-dev \
        libpng-dev \
        libwebp-dev \
        libxml2-dev \
        pkg-config \
 && curl -fsSL -o /tmp/imagemagick.tar.gz \
        "https://github.com/ImageMagick/ImageMagick/archive/refs/tags/${IMAGEMAGICK_VERSION}.tar.gz" \
 && printf '%s  %s\n' "$IMAGEMAGICK_SHA256" /tmp/imagemagick.tar.gz | sha256sum -c - \
 && tar -xzf /tmp/imagemagick.tar.gz -C /tmp \
 && cd "/tmp/ImageMagick-${IMAGEMAGICK_VERSION}" \
 && ./configure \
        --prefix=/opt/imagemagick \
        --disable-static \
        --enable-shared \
        --with-modules \
        --without-x \
 && make -j2 \
 && make install

# Stage 5: final image.  Debian bookworm matches the upstream
# lemmy_server build environment so libpq / glibc versions agree.
FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

# System deps:
#   * postgresql-16 (installed from pgdg below): bundled DB, runs
#     as the `postgres` user.
#   * nginx: front-door router (routes /_oidc, /api/v3, /pictrs,
#            /sso-bounce, /).
#   * python3 + venv + pip: OIDC bridge (Starlette + python-jose).
#     We install the python deps via apt where possible so we don't
#     do a `pip install` in the build (matches the openhost-minio
#     comment about portable build images).  python3-jwt and
#     python3-cryptography are in bookworm.
#   * curl: readiness probes from start.sh.
#   * tini: PID 1 zombie reaper / signal forwarder.
#   * gosu: drop privileges to postgres / lemmy users.
#   * Node.js: lemmy-ui is a Node SSR app. Node 20 is installed from
#     NodeSource below because the upstream UI image uses Alpine/musl.
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
        nginx \
        python3 \
        python3-starlette \
        python3-jwt \
        python3-cryptography \
        python3-bcrypt \
        python3-uvicorn \
        ffmpeg \
        libimage-exiftool-perl \
        libheif1 \
        libjpeg62-turbo \
        libltdl7 \
        libpng16-16 \
        libwebp7 \
        libxml2 \
        curl \
        tini \
        gosu \
        ca-certificates \
        procps \
        gnupg \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/nginx/sites-enabled/default

# PostgreSQL 16 from pgdg.postgresql.org.  Debian Bookworm's apt
# repo has only PG 15; Lemmy 1.0's migrations include
# Postgres-16-only SQL (lateral subqueries with required aliases)
# and outright fail on PG 15.
RUN curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg \
 && echo "deb http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update -qq \
 && apt-get install -y --no-install-recommends \
        postgresql-16 \
        postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*

# Node.js 20 from NodeSource — Debian Bookworm's apt repo only has
# Node 18, but lemmy-ui's bundled JS uses syntax/APIs that require
# Node 20+ (older builds crashed on Node 18 with a parse error in
# dist/js/server.js).
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# lemmy_server binary (statically-linked Rust, debian-bookworm
# build).  Drop into /usr/local/bin where it'll be on PATH.
COPY --from=backend-source /usr/local/bin/lemmy_server /usr/local/bin/lemmy_server
COPY --from=pictrs-source /usr/local/bin/pict-rs /usr/local/bin/pict-rs
COPY --from=imagemagick-source /opt/imagemagick /opt/imagemagick

# lemmy-ui: copy the compiled JS bundle.  We DON'T copy the
# upstream node binary — that image is Alpine (musl libc) and
# our base is Debian (glibc); a musl-linked binary fails to
# load with `libc.musl-x86_64.so.1: not found`.  Use Debian's
# nodejs apt package instead (installed above).
COPY --from=ui-source /app /opt/lemmy-ui

# Create the lemmy unprivileged user; UID 1500 to avoid clashing
# with the default `_apt`/`messagebus` etc. system users.
RUN useradd --system --uid 1500 --user-group --no-create-home --shell /usr/sbin/nologin lemmy

# Application files.
COPY nginx.conf            /etc/nginx/nginx.conf
COPY config.template.hjson /opt/openhost-lemmy/config.template.hjson
COPY oidc_bridge.py        /opt/openhost-lemmy/oidc_bridge.py
COPY bootstrap.py          /opt/openhost-lemmy/bootstrap.py
COPY sso_bounce.py         /opt/openhost-lemmy/sso_bounce.py
COPY start.sh              /opt/openhost-lemmy/start.sh

ENV PATH="/opt/imagemagick/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/imagemagick/lib"

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/openhost-lemmy/start.sh"]
