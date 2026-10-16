"""Build a deterministic source ZIP from a committed, allowlisted revision."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"Dockerfile", "requirements.txt", "run.py"}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def create_package(output, ref="HEAD"):
    commit = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    tracked = git("ls-tree", "-r", "--name-only", commit).decode().splitlines()
    names = sorted(name for name in tracked if name in ROOT_FILES or (
        name.startswith("src/") and PurePosixPath(name).suffix in {".py", ".json", ".txt"}
    ))
    if not ROOT_FILES <= set(names) or "src/plan_schema.json" not in names:
        raise ValueError("revision lacks required runtime files")
    payload = {name: git("show", commit + ":" + name) for name in names}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    manifest = {"schema_version": 1, "source_commit": commit, "files": hashes,
                "excluded": ["datasets", "answers", "credentials", "tests", "git metadata", "local caches"]}
    payload["SOURCE_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    payload["README_SUBMISSION.md"] = (
        "# PDU Team source submission\n\n"
        "Build: `docker build --platform=linux/amd64 -t pdu-team .`\n\n"
        "ENTRYPOINT: `python run.py`. Supply the six contest arguments:\n"
        "`--xlsx-dir`, `--queries`, `--output`, `--openrouter-key`, "
        "`--openrouter-model`, and `--openrouter-base-url`.\n\n"
        "The optional fallback uses `qwen/qwen3.7-flash`. Supply the key at runtime; "
        "an empty key runs the deterministic solver without network access. "
        "Do not place keys or datasets inside this source package.\n\n"
        "Output is UTF-8 CSV with `id,answer`; diagnostics go to stderr. "
        "Unresolved answers stay blank. Mount inputs read-only and the output directory writable.\n\n"
        "SOURCE_MANIFEST.json records the exact committed runtime files and SHA-256 hashes.\n"
    ).encode()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stored members avoid platform-dependent zlib output; source is far below 100 MB.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    raw = output.read_bytes()
    if len(raw) > 100_000_000:
        raise ValueError("submission exceeds the 100 MB limit")
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == set(payload)
        assert all(archive.read(name) == content for name, content in payload.items())
    return {"source_commit": commit, "zip_sha256": hashlib.sha256(raw).hexdigest(),
            "zip_bytes": len(raw), "runtime_files": len(names), "members": len(payload)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    print("SUBMISSION_PACKAGE=" + json.dumps(create_package(args.output, args.ref), sort_keys=True))


if __name__ == "__main__":
    main()
