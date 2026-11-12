# Compose

For this local deployment, see [package cache configuration and client setup](PACKAGE-CACHES.md).

Everything under the assets directory will be mounted into the container.
Please modify the files as needed.

[podman-compose installation docs](https://github.com/containers/podman-compose#installation).

## Running with podman

```shell
pip install podman-compose
git clone git@github.com:pulp/pulp-oci-images.git
cd images/compose
podman-compose up
```

## Running with docker and scaling

```shell
pip install docker-compose
git clone git@github.com:pulp/pulp-oci-images.git
cd images/compose
docker-compose up
docker-compose scale pulp_api=4 pulp_content=4
```

## Running with podman and using existing directories for data
```shell
pip install podman-compose
git clone git@github.com:pulp/pulp-oci-images.git
cd images/compose
mkdir -p ../../pgsql/data ../../pulp_storage
podman unshare chown 700:700 ../../pulp_storage
podman-compose -f compose.folders.yml up
```

## Running with docker and using existing directories for data
```shell
pip install podman-compose
git clone git@github.com:pulp/pulp-oci-images.git
cd images/compose
mkdir -p ../../pgsql/data ../../pulp_storage
sudo chown 700:700 ../../pulp_storage
podman-compose -f compose.folders.yml up
```


## RustFS content storage

The main Compose stack stores Pulp content in a private `pulp` bucket on a pinned
RustFS 1.0.5 service. RustFS data and logs have named volumes. The existing `pulp`
volume remains mounted for working files and retained filesystem content.

Set `RUSTFS_ACCESS_KEY` and `RUSTFS_SECRET_KEY` in the ignored `.env` file before
starting Compose. This local deployment uses generated credentials in that file.
The console is [http://localhost:9001/rustfs/console/](http://localhost:9001/rustfs/console/), and the S3 API is
[http://localhost:9000](http://localhost:9000). Both host ports bind to loopback.
Inside Compose, Pulp uses `http://rustfs:9000` with S3 v4/path-style requests.
Downloads remain proxied through Pulp (`REDIRECT_TO_OBJECT_STORAGE=False`), so
clients and the UI do not need to resolve the internal RustFS hostname.

`rustfs_volume_service` prepares volume ownership, and `object_storage_service`
creates the bucket idempotently before the Pulp services start. Database metadata
stays in PostgreSQL. Container token keys remain in `assets/certs`.

### Migrating an existing filesystem cache

Freeze API/content/worker writers and take a PostgreSQL backup first. Copy and
verify every relative media path before changing `PULP_STORAGE_BACKEND` from
`filesystem` to `rustfs`:

```shell
docker compose -f compose.yml up -d rustfs
docker compose -f compose.yml run --rm --no-deps object_storage_service
docker compose -f compose.yml stop pulp_api pulp_content pulp_worker
docker compose -f compose.yml run --rm --no-deps storage_migration_service
# Set PULP_STORAGE_BACKEND=rustfs in .env after the verified copy completes.
docker compose -f compose.yml run --rm --no-deps migration_service
docker compose -f compose.yml up -d --no-build --no-deps --force-recreate --wait pulp_api pulp_content pulp_worker
```

The copy is resumable, rejects existing objects with different bytes, verifies
SHA256 via complete reads, and never removes local files. `--verify-only` checks
an already copied bucket, for example:

```shell
docker compose -f compose.yml run --rm --no-deps storage_migration_service python3 /usr/bin/migrate_object_storage.py --source /var/lib/pulp/media --verify-only
```

Running Pulp's migrations also synchronizes the default
domain's storage class and redirect setting. No artifact filenames or repository
records need rewriting. Include the local NuGet override in each command if that
is how the deployment was started.

The local migration retains the original media tree for rollback. To return to
that snapshot, stop writers, set `PULP_STORAGE_BACKEND=filesystem`, run the
migration service and recreate the Pulp processes. Content added only to RustFS
after the switch must be copied back before a later rollback. Never remove the
old volume or use `docker compose down -v` during migration or rollback.

## Container registry authentication

`container_auth_key_service` initializes an ES256 token key pair in the shared
`assets/certs` directory before API, content, and worker services start. Existing
matching keys are reused; a missing public key is recovered from the private key.
A mismatched pair or a public key without its private key stops initialization
instead of silently rotating credentials. Keep the private key secure; generated
key files and the initializer lock are excluded from Git.

The token issuer defaults to `PULP_CONTENT_ORIGIN` plus `/token/`, so registry
clients receive a reachable authentication URL. Set `PULP_TOKEN_SERVER` in the
Pulp process environment only when using a different token gateway. The browser
UI must proxy `/token/` through the same gateway as `/v2/`. A development
gateway on another port must also rebase Pulp `/pulp/container/` and
`/pulp/content/` redirects to its own origin; the local pulp-ui proxy handles this.

After updating an existing deployment, initialize the keys and restart the Pulp
processes so they load the corrected key paths and token issuer:

```shell
docker compose -f compose.yml run --rm --no-deps container_auth_key_service
docker compose -f compose.yml up -d --no-build --force-recreate pulp_api pulp_content pulp_worker
```

Include `-f compose.local-nuget.yml` in both commands for the local NuGet image.
These commands retain the existing repositories, signing services and volumes.

## Building a local NuGet plugin checkout

The optional override builds the local source, including uncommitted changes, into
an image shared by API, content, workers, and initialization jobs. The normal
`compose.yml` continues to build the pinned upstream plugin.

Clone beside `pulp-oci-images` (the default source location), or set
`NUGET_SOURCE` to an absolute checkout path. The portable build helper stages a
temporary context containing the local source, excluding Git metadata, virtual
environments, caches, build outputs, and `.env` files; it removes that context
after `docker build` finishes. It works without buildx named build contexts:


```shell
git clone https://github.com/PhillipDiCarlo/pulp_nuget.git ../../../pulp-nuget
export NUGET_SOURCE=/absolute/path/to/pulp-nuget

docker compose -f compose.yml -f compose.local-nuget.yml config --quiet
python3 build-local-nuget.py
# Optional validation without building: python3 build-local-nuget.py --dry-run
docker compose -f compose.yml -f compose.local-nuget.yml up -d --no-build postgres redis
docker compose -f compose.yml -f compose.local-nuget.yml run --rm --no-deps migration_service
docker compose -f compose.yml -f compose.local-nuget.yml up -d --no-build
```

With Docker buildx and named build-context support (`additional_contexts`),
`docker compose -f compose.yml -f compose.local-nuget.yml build pulp_api` is an
alternative to the helper. Both build the same local image. The helper also accepts
`--source /path/to/checkout` and `--tag <image>` (a custom tag requires matching the
Compose override).

The local image retains the pinned pulpcore image and constrains its existing
Python packages while installing the plugin. Rebuild and recreate services after
source changes; this is a source build rather than a live bind mount. All existing
named data volumes are retained. Apply database migrations before starting the
updated services. A rollback to an older plugin must account for any migrations.

After deploying the extended plugin, enable lazy NuGet pull-through discovery:

```shell
NUGET_MODE=pull_through python3 bootstrap-nuget-proxy.py
```

This reuses the `nuget` repository and distribution, attaches the dedicated
`nuget-cache-upstream` remote, and performs no metadata sync. The existing
`nuget-upstream` selected-package remote and repository content are preserved.
`NUGET_MODE=selected` (the default) keeps the prior allowlist-and-sync workflow.
`NUGET_URL` and the existing `PULP_*` connection variables apply in either mode.
