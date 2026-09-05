# bottled-lemmy

[Lemmy](https://join-lemmy.org/) is a federated link aggregator and discussion
platform. This repository packages it as a Cloud in a Bottle app with automatic
owner sign-in.

## What you get

- A federated Lemmy instance at `https://lemmy.<zone>/`.
- The Cloud in a Bottle owner signed in automatically as a Lemmy admin.
- Public, read-only browsing for guests and ActivityPub peers.
- Native local registration disabled; the owner account is created through SSO.
- Persistent posts, communities, subscriptions, and account state.
- Matching Lemmy backend and UI versions, currently `1.0.0-beta.1`.

## Usage

Open `https://lemmy.<zone>/`. The first visit briefly redirects through Cloud in
a Bottle SSO, creates a Lemmy account matching the owner's zone username, and
promotes it to admin, then closes local registration. Later visits reuse the
Lemmy session.

To follow a remote community, search for its federated name, such as
`!linux@lemmy.ml`, open it, and select Subscribe. New activities then arrive
through ActivityPub federation.

Guests can browse public communities, posts, comments, and profiles. Lemmy's own
permissions prevent guests from posting or voting without an account, and nginx
rejects native signup requests. Remote users participate from accounts on their
own federated instances.

## Deploying

Deploy from the Cloud in a Bottle catalog, dashboard, or CLI:

```bash
bottle app deploy https://github.com/cloud-in-a-bottle/bottled-lemmy --wait
```

The app becomes available at `https://lemmy.<zone>/`.

## Data

All persistent state lives under `$BOTTLE_APP_DATA_DIR/postgres/` as a
PostgreSQL 16 cluster. It contains Lemmy accounts, posts, comments, communities,
subscriptions, moderation state, and the configured OAuth provider.

No usable password, token, OIDC signing key, or OIDC client secret is written as
a standalone persistent file:

- PostgreSQL accepts only container-local connections and does not need a
  database password.
- The internal provisioning-admin password is random, exists only for the
  container boot, and is replaced on every restart.
- The OIDC signing key and client secret live under `/run/lemmy`, rotate on every
  restart, and are reconciled before the app becomes ready.
- Legacy `admin-password.txt`, `postgres-password.txt`,
  `oidc-client-secret.txt`, and persisted `config.hjson` files are removed on
  startup.

## Backup

Do not copy a running PostgreSQL data directory file by file. Use one of these
methods:

1. Stop the Lemmy app, then back up `$BOTTLE_APP_DATA_DIR/postgres/`.
2. While the app is running, execute `pg_dump -h 127.0.0.1 -U lemmy -d lemmy`
   and back up the resulting logical dump.

Restore into the same public hostname. A Lemmy hostname is part of every
ActivityPub actor and object identity; changing it breaks existing federation
identities and links.

## Upgrades

The backend and UI image tags must always move together. Lemmy runs database
migrations when the new backend starts, so take a database backup before changing
versions. Test owner SSO, public browsing, and a remote subscription before
publishing an upgrade.

This package uses Lemmy 1.0 because native OAuth/OIDC support is required for
owner SSO. `1.0.0-beta.1` is the most stable release with that feature and also
contains the compatibility fix for `Accept` activities sent by Lemmy 0.19
instances.

## Architecture

One container runs:

| Service | Listen address | Purpose |
| --- | --- | --- |
| nginx | `0.0.0.0:8080` | Public routing and registration guard |
| lemmy_server | `127.0.0.1:8536` | API and ActivityPub backend |
| lemmy-ui | `127.0.0.1:1234` | Web interface and SSR |
| PostgreSQL 16 | `127.0.0.1:5432` | Persistent metadata |
| OIDC bridge | `127.0.0.1:7000` | Owner SSO provider |
| SSO bouncer | `127.0.0.1:7100` | Starts lemmy-ui's OAuth flow |

`start.sh` starts PostgreSQL and Lemmy, waits for migrations, reconciles the OIDC
provider, and only then exposes nginx. PostgreSQL and all serving processes are
supervised; an unexpected exit stops the container so Cloud in a Bottle can
restart it.

The OIDC provider uses a public authorization endpoint and loopback token and
userinfo endpoints. The browser reaches
`https://lemmy.<zone>/_oidc/authorize`, while Lemmy exchanges the code at
`http://127.0.0.1:7000`. This avoids cloud-provider NAT hairpinning.

The manifest makes `/` public because ActivityPub inboxes, WebFinger, NodeInfo,
and public Lemmy pages must be internet-reachable. The Cloud in a Bottle router
still authenticates owner requests and stamps `X-OpenHost-Is-Owner: true`; only
that trusted header can start the owner OIDC flow.

## Resources

The manifest requests 1 GiB RAM and 1 CPU core at runtime, with 2 GiB available
during image builds. The complete stack used about 304 MB during catalog smoke
testing, leaving headroom for migrations and federation bursts. Larger instances
need limits based on their database size and federation traffic.

## Troubleshooting

Check status and logs with:

```bash
bottle app status lemmy
bottle app logs lemmy
```

Useful checks:

```bash
curl -fsS https://lemmy.<zone>/api/v3/site
curl -fsS https://lemmy.<zone>/.well-known/nodeinfo
curl -H 'Accept: application/activity+json' https://lemmy.<zone>/u/<username>
```

If a remote subscription remains pending, confirm the remote instance can reach
`/inbox` and that both instances have accurate clocks. After an upgrade that
fixes federation compatibility, unsubscribe and subscribe again to generate a
fresh Follow activity.

## Caveats

- **No pict-rs:** image uploads, avatars, thumbnails, and proxied remote images
  are unavailable. Text posts, links, comments, votes, and federation work.
- **Single owner:** Cloud in a Bottle SSO maps to one local Lemmy admin. Other
  people participate through accounts on federated instances.
- **No outbound email:** confirmation and password-reset email are unavailable.
- **Bundled PostgreSQL:** the database runs in the same container rather than as
  an external managed service.

## License

Lemmy, lemmy-ui, and this packaging repository are licensed under the GNU Affero
General Public License v3.0 or later. See `LICENSE` and `NOTICE`.
