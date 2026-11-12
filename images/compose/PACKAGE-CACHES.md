# Local package caches

This stack exposes Pulp through nginx at `http://10.22.247.22:8080`.
The local `.env` advertises that address; localhost remains usable from the Docker host.
If the Docker host address changes, update the configured origin and client URLs.
Set `PULP_CONTENT_ORIGIN=http://your-docker-host:8080` in `images/compose/.env`
and run `docker compose -f images/compose/compose.yml up -d` so generated
package download URLs use that same reachable address.

## How Compose is configured

- PostgreSQL 16 stores metadata (`pg_datadev`); its host port is 5432.
- Redis stores cache state (`redis_datadev`); it is internal to Compose.
- One API replica handles management and Python index requests.
- One content replica serves package files; one worker runs asynchronous tasks.
- The original two API/two content replica defaults exceeded the local 2 GB VM
  memory during sync. Set `PULP_API_REPLICAS` / `PULP_CONTENT_REPLICAS` to scale up
  when more memory is available. `PULP_WORKER_REPLICAS`, `PULP_API_WORKERS`, and
  `PULP_CONTENT_WORKERS` also default to one for this small VM.
- nginx exposes port 8080 and routes `/pulp/api/v3/` and `/pypi/` to API,
  `/pulp/content/` to content, and other routes (including NuGet publishing) to API.
- Initialization jobs migrate the database, initialize the admin password, and
  initialize the container signing service. Password initialization waits for migrations.
- The Pulp processes share `pulpdev` at `/var/lib/pulp` and the same settings/key mounts.
- A pinned image extends the running pulpcore 3.123.2 image with NuGet at source
  commit `4997894367095817417cc639fb3e7ce3d420238a`. All Pulp processes and jobs
  use this identical plugin set. Existing installed dependency versions are constrained
  during build. The web image stays separate.

The original stack already included npm 0.10.2, Python 3.38.0, and Debian 3.11.1,
but had no package repositories and advertised the Docker-only `pulp_content` hostname.
The public content origin is now configurable and defaults to `http://localhost:8080`.

This remains the existing development deployment, using HTTP and the
example credentials/settings already defined in Compose and settings.py.
Package feeds below permit anonymous downloads; Python uploads are disabled.

## npm: transparent npmjs cache

```sh
npm install --registry http://10.22.247.22:8080/pulp/content/npm/ PACKAGE
```

The `npm-upstream` remote points at `https://registry.npmjs.org/` with
`policy=on_demand`. The `npm` distribution is remote-backed: no package allowlist
or initial registry-wide sync is needed. Tarballs are stored after first use.

Installed pulp-npm 0.10.2 passes through upstream metadata tarball URLs unchanged.
The dedicated nginx `/pulp/content/npm/` location rewrites npmjs URLs in JSON
responses to this distribution so npm/yarn downloads also pass through Pulp.
It disables upstream compression for those responses; package binaries are not rewritten.

## Python: transparent PyPI cache

```sh
pip install --index-url http://10.22.247.22:8080/pypi/python/simple/ \
  --trusted-host 10.22.247.22 PACKAGE
```

The `python-upstream` remote points at `https://pypi.org/` with `on_demand`.
The remote-backed `python` distribution resolves projects on first request and
caches downloaded wheels/sdists. No PyPI-wide sync is needed.
With HTTPS, omit `--trusted-host`.

## NuGet: local pull-through extension

The locally extended plugin adds lazy lookup and caching of previously unknown
package IDs, matching the npm/Python workflow. See [local checkout builds](README.md#building-a-local-nuget-plugin-checkout)
for the opt-in build and migration commands, then run
`NUGET_MODE=pull_through python3 images/compose/bootstrap-nuget-proxy.py` from the
repository root. This keeps existing NuGet content and the selected-sync remote,
while linking a separate upstream cache remote to the distribution. No feed-wide
sync or preselection of dependency IDs is needed. The following section describes
the pinned upstream plugin and the still-supported `NUGET_MODE=selected` workflow.

## NuGet: selected package metadata, on-demand binaries

The initial selection is **Serilog**. The v3 service index is:

`http://10.22.247.22:8080/pulp/content/nuget/v3/index.json`

Recent .NET SDKs require HTTP sources to explicitly allow insecure connections:

```xml
<configuration>
  <packageSources>
    <clear />
    <add key="pulp" value="http://10.22.247.22:8080/pulp/content/nuget/v3/index.json"
         allowInsecureConnections="true" />
  </packageSources>
</configuration>
```

Use a client-reachable hostname instead of localhost on remote machines.
Sync discovers versions/dependencies metadata for the selected IDs; `.nupkg` bytes
are fetched and cached on first download. Dependency IDs are not recursively added:
include them explicitly when selecting packages that depend on other NuGet packages.

The plugin supports `includes=["*"]`, but that enumerates upstream search results
and imports registration/version metadata up front. It is not lazy discovery of
unknown packages, and nuget.org caps search pagination. This deployment uses an
explicit selection as requested. The development plugin has no released stable version.

## Ubuntu 24.04 / APT

The machine's `24.04.4` release uses the **noble** suites, architecture **amd64**.
Two remotes mirror all four components (`main restricted universe multiverse`):

| Distribution | Upstream | Suites |
| --- | --- | --- |
| `/pulp/content/ubuntu/` | `https://au.archive.ubuntu.com/ubuntu/` | noble, noble-updates, noble-backports |
| `/pulp/content/ubuntu-security/` | `https://security.ubuntu.com/ubuntu/` | noble-security |

APT metadata is synchronized first. Package binaries use `on_demand`, so the mirror
does not download every `.deb`. Sources, udebs, and installer files are excluded.
The bootstrap verifies upstream metadata with Ubuntu's archive keys and creates
**verbatim publications** that preserve Ubuntu's original signatures. It disables
structured autopublishing because regenerated metadata would require a separate
Pulp signing key.

On the Ubuntu client, replace the corresponding Ubuntu entries in
`/etc/apt/sources.list.d/ubuntu.sources` with the following (substitute the Docker
host's address). Retain the Ubuntu archive keyring already installed on that client.

```deb822
Types: deb
URIs: http://10.22.247.22:8080/pulp/content/ubuntu/
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Architectures: amd64
Targets: Packages
By-Hash: no
PDiffs: no
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://10.22.247.22:8080/pulp/content/ubuntu-security/
Suites: noble-security
Components: main restricted universe multiverse
Architectures: amd64
Targets: Packages
By-Hash: no
PDiffs: no
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
```

The VM was increased from 2 GB to 8 GB for the initial metadata import, after
the 2 GB allocation caused a sync-task out-of-memory failure.
The target options request only mirrored package indexes and disable unsynced
index diffs/by-hash paths; signature and checksum checks stay enabled.
Then run `sudo apt update`. APT is a synchronized metadata mirror with lazy binary
caching. Refresh the mirrors to receive newly published packages/security updates.
No recurring schedule has been created.

## Rebuild and reproduce

From the repository root:

```sh
docker compose -f images/compose/compose.yml build migration_service
docker compose -f images/compose/compose.yml up -d --no-build --wait
python3 images/compose/bootstrap-package-proxies.py
python3 images/compose/bootstrap-nuget-proxy.py
UBUNTU_GPGKEY_FILE=images/compose/assets/certs/ubuntu-archive.asc \
  python3 images/compose/bootstrap-ubuntu-mirror.py
```

Bootstrap scripts reuse matching objects, wait for Pulp tasks, and report failures.
`PULP_URL`, `PULP_USERNAME`, and `PULP_PASSWORD` configure their management connection.
`PULP_TASK_TIMEOUT` bounds task waits (Ubuntu defaults to 3600 seconds).
Re-running the Ubuntu script refreshes metadata/publications. Re-running the NuGet
script refreshes Serilog metadata. To add package IDs, for example:

```sh
NUGET_PACKAGES='Serilog,Newtonsoft.Json' python3 images/compose/bootstrap-nuget-proxy.py
```

This updates the remote selection and performs an additive sync. Removing an ID
from the list does not delete previously synchronized content.

Ubuntu keys were fetched via HTTPS from
<https://archive.ubuntu.com/ubuntu/project/ubuntu-archive-keyring.gpg> and exported
as ASCII-armored public keys. Clients continue to trust their own Ubuntu keyring.

## Verified deployment

Verified against the public address after the VM was increased to 8 GB:

- npm `is-number@7.0.0` and scoped `@types/is-number@7.0.5` downloads.
- Python `six==1.17.0` installation.
- NuGet Serilog metadata (602 versions), first-pull storage of 4.4.0, and
  repeated identical downloads from the persisted artifact.
- Signed Ubuntu archive/security `apt update` from a disposable Ubuntu 24.04
  client configured for amd64, then `hello` and security `openssl` downloads.
- Compose configuration, nginx configuration, and all running service health checks.

## UI support

The current local UI has management sections for Ansible, Debian, File, and RPM.
Its Status page lists all installed backend plugins, but npm/Python/NuGet do not
have menu entries or management screens in this UI version. Their feeds work
independently and can be managed through the API and these bootstrap scripts.

## References

- [npm pull-through caching](https://pulpproject.org/pulp_npm/docs/user/guides/pull-through_cache/)
- [Python index setup](https://pulpproject.org/pulp_python/docs/user/guides/pypi/)
- [Debian verbatim publications](https://pulpproject.org/pulp_deb/docs/user/guides/publish/#verbatim-publications)
- [NuGet plugin](https://github.com/PhillipDiCarlo/pulp_nuget)
- [NuGet wildcard implementation](https://github.com/PhillipDiCarlo/pulp_nuget/blob/4997894367095817417cc639fb3e7ce3d420238a/pulp_nuget/app/tasks/synchronizing.py)
- [nuget.org search limits](https://learn.microsoft.com/en-us/nuget/api/search-query-service-resource#request-parameters)
