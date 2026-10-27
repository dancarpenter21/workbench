"""Extract missing Debian build libraries into an unprivileged local prefix.

Uses apt's authenticated package indexes and download verification. It does not
install packages, run maintainer scripts, or modify the host package database.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PREFIX = ROOT / ".runtime/ros/system-prefix"
ARCHIVES = ROOT / ".runtime/ros/debs"
PACKAGES = [
    "libcli11-dev",
    "libexpected-dev",
    "libcap-dev",
    "libomp-dev",
    "libgtest-dev",
    "libgmock-dev",
    "libxml2-utils",
    "libdw-dev",
    "libacl1-dev",
    "nlohmann-json3-dev",
    "libbenchmark-dev",
    "libcurl4-openssl-dev",
    "libqhull-dev",
    "libyaml-cpp-dev",
    "libfreetype-dev",
    "libxaw7-dev",
    "libxrandr-dev",
    "libglew-dev",
]


def command(args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def main(*, metadata_only=False):
    ARCHIVES.mkdir(parents=True, exist_ok=True)
    simulation = command(["apt-get", "-s", "--no-install-recommends", "install", *PACKAGES])
    requested = re.findall(r"^Inst (\S+)(?: \[[^]]+\])? \((\S+)", simulation, re.MULTILINE)

    metadata = []
    for package, version in requested:
        fields = shlex.split(
            command(["apt-get", "--print-uris", "download", f"{package}={version}"])
        )
        if len(fields) != 4 or not fields[3].startswith("SHA256:"):
            raise RuntimeError(f"No SHA256 package metadata for {package}")
        metadata.append(
            {
                "package": package,
                "version": version,
                "url": fields[0],
                "archive": fields[1],
                "size": int(fields[2]),
                "sha256": fields[3][7:],
            }
        )
    (ARCHIVES / "expected.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if metadata_only:
        print(f"Recorded {len(metadata)} package hashes in {ARCHIVES / 'expected.json'}")
        return 0

    def verified(entry):
        archive = ARCHIVES / entry["archive"]
        return (
            archive.is_file()
            and archive.stat().st_size == entry["size"]
            and hashlib.sha256(archive.read_bytes()).hexdigest() == entry["sha256"]
        )

    def download(entry):
        if verified(entry):
            return
        archive = ARCHIVES / entry["archive"]
        if archive.exists():
            archive.rename(archive.with_name(archive.name + f".incomplete-{uuid4().hex}"))
        command(
            [
                "apt-get",
                "-o",
                "Acquire::Retries=2",
                "-o",
                "Acquire::http::Timeout=30",
                "download",
                f"{entry['package']}={entry['version']}",
            ],
            cwd=ARCHIVES,
        )
        if not verified(entry):
            raise RuntimeError(f"Package checksum/size mismatch: {entry['archive']}")

    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(download, entry): entry for entry in metadata}
        for future in concurrent.futures.as_completed(pending):
            try:
                future.result()
            except RuntimeError as exc:
                errors.append({"package": pending[future]["package"], "error": str(exc)})
    if errors:
        for error in errors:
            print(f"{error['package']}: {error['error']}", flush=True)
        return 1
    staging = PREFIX.with_name(f"system-prefix-staging-{uuid4().hex}")
    staging.mkdir()
    for entry in metadata:
        if not verified(entry):
            raise RuntimeError("Archive changed after verification; refusing extraction")
        command(["dpkg-deb", "--extract", str(ARCHIVES / entry["archive"]), str(staging)])
    # Relocate only the extracted pkg-config metadata, leaving system metadata intact.
    for pc in staging.rglob("*.pc"):
        text = pc.read_text()
        if str(PREFIX) not in text:
            pc.write_text(text.replace("/usr", str(PREFIX / "usr")))
    # Development packages may link to runtime libraries already installed on the host.
    # Preserve those host dependencies rather than copying libc or replacing system libraries.
    for link in staging.rglob("*"):
        if link.is_symlink() and not link.exists():
            target = os.path.normpath(
                str(Path("/") / link.parent.relative_to(staging) / os.readlink(link))
            )
            if Path(target).exists():
                link.unlink()
                link.symlink_to(target)
    (staging / ".workbench-system-prefix.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if PREFIX.exists():
        PREFIX.rename(PREFIX.with_name(f"system-prefix-previous-{uuid4().hex}"))
    staging.rename(PREFIX)
    output = ROOT / ".runtime/ros/system-prefix-manifest.json"
    output.write_text(
        json.dumps(
            {
                "packages": metadata,
                "prefix": str(PREFIX),
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"Extracted {len(metadata)} verified Debian packages into {PREFIX}; host packages unchanged"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-only", action="store_true")
    raise SystemExit(main(metadata_only=parser.parse_args().metadata_only))
