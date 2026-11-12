#!/usr/bin/env python3
"""Build local NuGet plugin edits without requiring buildx named build contexts."""

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def main():
    compose_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("NUGET_SOURCE", compose_dir / "../../../pulp-nuget")),
        help="Local pulp_nuget checkout (defaults to NUGET_SOURCE or sibling checkout)",
    )
    parser.add_argument("--tag", default="local/pulp-minimal-nuget:local")
    parser.add_argument("--dry-run", action="store_true", help="Validate/stage source without building")
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "pyproject.toml").is_file() or not (source / "pulp_nuget").is_dir():
        parser.error(f"Not a pulp_nuget source checkout: {source}")

    dockerfile = (compose_dir / "Containerfile.nuget-local").read_text()
    named_copy = "COPY --from=nuget-source . /tmp/pulp-nuget"
    if named_copy not in dockerfile:
        parser.error("Containerfile.nuget-local no longer has the expected local source COPY")
    with tempfile.TemporaryDirectory(prefix="pulp-nuget-build-") as temporary:
        context = Path(temporary)
        shutil.copytree(
            source,
            context / "pulp-nuget",
            symlinks=True,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
                ".mypy_cache", "node_modules", "build", "dist", "*.egg-info", ".DS_Store",
                ".env", ".env.*",
            ),
        )
        (context / "Containerfile").write_text(
            dockerfile.replace(named_copy, "COPY pulp-nuget /tmp/pulp-nuget")
        )
        command = ["docker", "build", "--tag", args.tag, "--file", str(context / "Containerfile"), str(context)]
        print(f"Building NuGet source: {source}", flush=True)
        print(shlex.join(command), flush=True)
        if args.dry_run:
            print("Source staging and Containerfile validation passed; no image built.")
        else:
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
