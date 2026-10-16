"""Hash-pinned, authenticated transport objects; no solver or answer scoring."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

MAX_ARCHIVE = 200_000_000


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): digest(p.read_bytes())
            for p in sorted(Path(folder).rglob("*")) if p.is_file()}


def input_digest(files):
    # Matches the already frozen benchmark and campaign ledger convention.
    return digest(json.dumps(files, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode())


def encrypt(data, key, metadata, kind="RESULT"):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    header = ("STAI-" + kind + "-1").encode()
    nonce = os.urandom(12)
    combined = AESGCM(key).encrypt(nonce, data, header + b"\n" + canonical(metadata))
    return header + nonce + combined[-16:] + combined[:-16]


def decrypt(data, key, metadata, kind="SHARD"):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    header = ("STAI-" + kind + "-1").encode()
    if not data.startswith(header) or len(data) < len(header) + 28:
        raise ValueError("invalid encrypted object")
    tail = data[len(header):]
    return AESGCM(key).decrypt(tail[:12], tail[28:] + tail[12:28],
                               header + b"\n" + canonical(metadata))


def unpack(data, destination, expected=None):
    """Reject traversal, symlinks, duplicates, bombs and unmanifested members."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError("refusing to overwrite an extracted object")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [x.filename for x in entries if not x.is_dir()]
        if len(names) != len(set(names)) or sum(x.file_size for x in entries) > MAX_ARCHIVE:
            raise ValueError("invalid archive inventory")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (entry.orig_filename != entry.filename or path.is_absolute() or any(x in ("", ".", "..") for x in path.parts)
                    or "\\" in entry.filename or ":" in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("unsafe archive path")
        if expected is not None and set(names) != set(expected):
            raise ValueError("archive inventory differs from its manifest")
        destination.mkdir(parents=True, mode=0o700)
        for entry in entries:
            if entry.is_dir():
                continue
            data = archive.read(entry)
            if expected is not None and digest(data) != expected[entry.filename]:
                raise ValueError("archive content differs from its manifest")
            target = destination / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def pack(folder):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(Path(folder).rglob("*")):
            if p.is_file():
                archive.write(p, p.relative_to(folder).as_posix())
    return result.getvalue()


def validate_manifest(manifest):
    if manifest.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("harness_sha", "")):
        raise ValueError("a frozen harness is required")
    sources = manifest["sources"]
    batches = manifest["batches"]
    for source in sources.values():
        if not re.fullmatch(r"[0-9a-f]{40}", source["sha"]):
            raise ValueError("source must be a commit hash")
    for batch in batches:
        expected = {"queries.json"} | {n for n in batch["files"] if re.fullmatch(r"xlsx_files/[^/\\:]+\.xlsx", n, re.I)}
        if set(batch["files"]) != expected or input_digest(batch["files"]) != batch["input_sha256"]:
            raise ValueError("input inventory must contain only queries and workbooks")
        if not 1 <= len(batch["ids"]) <= 20 or len(set(batch["ids"])) != len(batch["ids"]):
            raise ValueError("invalid batch IDs")
        keys=batch.get('stable_case_keys')
        if keys is not None and (not isinstance(keys,dict) or set(keys)!=set(batch['ids']) or
                                 any(not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{64}',v) for v in keys.values())):
            raise ValueError('invalid stable case keys')
    for number, run in enumerate(manifest["runs"]):
        if run["number"] != number or run["label"] not in sources or run["batch_index"] not in range(len(batches)):
            raise ValueError("invalid ordered run")
        config = run['configuration']
        core = {k:v for k,v in config.items() if k not in {'solver_profile','model'}}
        if core not in [
            {"policy":p, "output_tokens":t, "api_enabled":a}
            for p in ("fallback", "selective") for t in (600,1200) for a in (True,False)
        ]:
            raise ValueError("unapproved configuration")
        if config.get('solver_profile','legacy') not in {'legacy','direct','raw','dual'}:
            raise ValueError('unapproved solver profile')
        if config['api_enabled'] and 'stable_case_keys' not in batches[run['batch_index']]:
            raise ValueError('paid transport runs require stable case identities')
        if config.get('model','qwen/qwen3.7-flash') not in {'qwen/qwen3.7-flash','google/gemini-2.5-flash-lite'}:
            raise ValueError('unapproved model')


def result_metadata(manifest_sha, run, batch, source, image):
    return {"schema_version":1, "manifest_sha256":manifest_sha,
            "number":run["number"], "invocation":run["invocation"],
            "source_sha":source["sha"], "input_sha256":batch["input_sha256"],
            "configuration":run["configuration"], "image_id":image}
