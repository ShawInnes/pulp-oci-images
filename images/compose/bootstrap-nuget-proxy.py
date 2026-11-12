#!/usr/bin/env python3
"""Configure selected NuGet packages and on-demand package downloads.

NUGET_MODE=selected (default) retains the metadata-sync workflow.
NUGET_MODE=pull_through uses lazy discovery with a remote-backed distribution;
requires the locally extended plugin. Existing selected-package remotes stay unchanged.
NUGET_PACKAGES is a comma-separated package list (default: Serilog) in selected mode.
Set NUGET_URL to use another upstream v3 index; PULP_* variables also apply.
"""
import importlib.util
import os
from pathlib import Path
import sys


def configure_pull_through(pulp, upstream):
    """Attach a dedicated cache remote without rewriting the selected-sync remote."""
    remote = pulp.ensure("remotes/nuget/nuget", {
        "name": "nuget-cache-upstream", "url": upstream,
        "policy": "on_demand", "includes": [], "excludes": [],
    })
    # Reuse repository membership, including selected-package metadata and cached files.
    # The repository's sync remote can remain the existing allowlist remote.
    repository = pulp.ensure("repositories/nuget/nuget", {"name": "nuget"})
    path = "/pulp/api/v3/distributions/nuget/nuget/"
    result = pulp.request("GET", path + "?name=nuget")
    if not result["count"]:
        pulp.ensure("distributions/nuget/nuget", {
            "name": "nuget", "base_path": "nuget", "repository": repository,
            "repository_version": None, "remote": remote,
        })
    elif result["count"] == 1:
        distribution = result["results"][0]
        expected = {"base_path": "nuget", "repository": repository, "repository_version": None}
        if any(distribution.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Existing NuGet distribution differs; review its repository/path before enabling caching")
        if "remote" not in distribution:
            raise RuntimeError("Installed NuGet backend does not support distribution remotes; build and deploy the local plugin first")
        if distribution["remote"] not in (None, remote):
            raise RuntimeError("Existing NuGet distribution uses another remote; review it before enabling caching")
        if distribution["remote"] != remote:
            pulp.wait(pulp.request("PATCH", distribution["pulp_href"], {"remote": remote}))
    else:
        raise RuntimeError("Expected exactly one NuGet distribution")
    print(f"NuGet pull-through feed: {pulp.url}/pulp/content/nuget/v3/index.json", flush=True)


def main():
    spec = importlib.util.spec_from_file_location(
        "package_proxies", Path(__file__).with_name("bootstrap-package-proxies.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pulp = module.Pulp()
    mode = os.getenv("NUGET_MODE", "selected")
    upstream = os.getenv("NUGET_URL", "https://api.nuget.org/v3/index.json")
    if mode == "pull_through":
        configure_pull_through(pulp, upstream)
        return
    if mode != "selected":
        raise ValueError("NUGET_MODE must be selected or pull_through")
    packages = [item.strip() for item in os.getenv("NUGET_PACKAGES", "Serilog").split(",") if item.strip()]
    if not packages or "*" in packages:
        raise ValueError("NUGET_PACKAGES must contain explicit package IDs")
    existing = pulp.request("GET", "/pulp/api/v3/remotes/nuget/nuget/?name=nuget-upstream")
    if existing["count"] == 1 and existing["results"][0]["includes"] != packages:
        href = pulp.ensure("remotes/nuget/nuget", {
            "name": "nuget-upstream", "url": upstream, "policy": "on_demand", "excludes": [],
        })
        pulp.wait(pulp.request("PATCH", href, {"includes": packages}))
    remote = pulp.ensure("remotes/nuget/nuget", {
        "name": "nuget-upstream", "url": upstream,
        "policy": "on_demand", "includes": packages, "excludes": [],
    })
    repository = pulp.ensure("repositories/nuget/nuget", {"name": "nuget", "remote": remote})
    pulp.ensure("distributions/nuget/nuget", {
        "name": "nuget", "base_path": "nuget", "repository": repository,
        "repository_version": None,
    })
    print(f"NuGet feed: {pulp.url}/pulp/content/nuget/v3/index.json", flush=True)
    pulp.sync(repository, remote)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        sys.exit(str(error))
