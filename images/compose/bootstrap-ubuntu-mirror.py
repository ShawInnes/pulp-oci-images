#!/usr/bin/env python3
"""Mirror Ubuntu 24.04 amd64 metadata, preserving Ubuntu signatures.

Requires UBUNTU_GPGKEY_FILE: ASCII-armored Ubuntu archive public keys exported
from a trusted Ubuntu keyring. Run again to refresh metadata and publications.
Package binaries are cached only when requested. Requires the sibling
bootstrap-package-proxies.py script. PULP_* connection variables apply.
"""

import importlib.util
import os
from pathlib import Path
import sys
from urllib.parse import urlencode


def publication(pulp, repository):
    version = pulp.request("GET", repository)["latest_version_href"]
    collection = "/pulp/api/v3/publications/deb/verbatim/"
    query = collection + "?" + urlencode({"repository_version": version})
    results = pulp.request("GET", query)
    if not results["count"]:
        pulp.wait(pulp.request("POST", collection, {"repository_version": version}))
        results = pulp.request("GET", query)
    if not results["results"]:
        raise RuntimeError("Verbatim publication was not created for " + version)
    return results["results"][0]["pulp_href"]


def distribute(pulp, name, base_path, repository, published):
    collection = "/pulp/api/v3/distributions/deb/apt/"
    results = pulp.request("GET", collection + "?" + urlencode({"name": name}))
    fields = {"name": name, "base_path": base_path, "publication": published,
              "repository": None, "repository_version": None}
    if not results["count"]:
        pulp.ensure("distributions/deb/apt", fields)
        return
    if results["count"] != 1:
        raise RuntimeError("Ambiguous distribution: " + name)
    existing = results["results"][0]
    if any(existing.get(key) != fields[key] for key in
           ("base_path", "repository", "repository_version")):
        raise RuntimeError("Conflicting distribution configuration: " + name)
    old_publication = existing.get("publication")
    if old_publication == published:
        return
    if old_publication:
        previous = pulp.request("GET", old_publication)
        if not previous["repository_version"].startswith(repository + "versions/"):
            raise RuntimeError("Distribution serves another repository: " + name)
    pulp.wait(pulp.request("PATCH", existing["pulp_href"], {"publication": published}))
    print("Updated Ubuntu verbatim distribution: " + name)


def main():
    key_path = os.getenv("UBUNTU_GPGKEY_FILE")
    if not key_path:
        raise ValueError("Set UBUNTU_GPGKEY_FILE to trusted ASCII-armored Ubuntu archive public keys")
    key = Path(key_path).read_text().strip()
    if "-----BEGIN PGP PUBLIC KEY BLOCK-----" not in key:
        raise ValueError("UBUNTU_GPGKEY_FILE must contain ASCII-armored public keys")
    spec = importlib.util.spec_from_file_location(
        "package_proxies", Path(__file__).with_name("bootstrap-package-proxies.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    os.environ.setdefault("PULP_TASK_TIMEOUT", "3600")
    pulp = module.Pulp()
    for suffix, upstream, suites, base_path in (
        ("archive", os.getenv("UBUNTU_ARCHIVE_URL", "https://au.archive.ubuntu.com/ubuntu/"),
         "noble noble-updates noble-backports", "ubuntu"),
        ("security", "https://security.ubuntu.com/ubuntu/",
         "noble-security", "ubuntu-security"),
    ):
        name = "ubuntu-noble-amd64-" + suffix
        remote = pulp.ensure("remotes/deb/apt", {
            "name": name, "url": upstream, "distributions": suites,
            "components": "main restricted universe multiverse", "architectures": "amd64",
            "policy": "on_demand", "gpgkey": key,
            "sync_sources": False, "sync_udebs": False, "sync_installer": False,
        })
        repository = pulp.ensure("repositories/deb/apt", {
            "name": name, "remote": remote, "autopublish": False,
        })
        print("Syncing Ubuntu metadata: " + name, flush=True)
        pulp.wait(pulp.request("POST", repository + "sync/", {"remote": remote, "mirror": True}))
        distribute(pulp, name, base_path, repository, publication(pulp, repository))
        print(f"Ubuntu {suffix}: {pulp.url}/pulp/content/{base_path}/ ({suites})")
    print("Clients: Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg; Architectures: amd64")
    print("Refresh these mirrors regularly to receive new Ubuntu and security updates.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        sys.exit(str(error))
