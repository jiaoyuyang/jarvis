#!/usr/bin/env python3
"""Generate/check the deterministic Ops package manifest; no network access."""
import argparse
import hashlib
import json
from pathlib import Path

FILES = ("network_engine.py", "mcp_extension.py", "install.py")


def manifest(folder):
    hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in FILES}
    package = hashlib.sha256("".join(name + "\0" + hashes[name] + "\n" for name in FILES).encode()).hexdigest()
    return {"version": "1.1.2", "package_sha256": package, "sha256": hashes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    folder = Path(__file__).parent
    value = manifest(folder)
    target = folder / "release.json"
    if args.check:
        if not target.is_file() or json.loads(target.read_text()) != value:
            raise SystemExit("Ops release manifest is stale; run python3 ops/build_release.py")
    else:
        target.write_text(json.dumps(value, indent=2) + "\n")
    print(value["package_sha256"])
