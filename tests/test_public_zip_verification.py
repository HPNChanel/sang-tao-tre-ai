import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ci_public_data2 import unpack_verified


def archive(name="queries.json"):
    content = b"[]"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(name, content)
    raw = stream.getvalue()
    return raw, {"zip_bytes": len(raw), "zip_sha256": hashlib.sha256(raw).hexdigest(),
                 "files": [{"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]}


def test_public_transfer_requires_pinned_hash_and_member_set(tmp_path):
    raw, manifest = archive()
    unpack_verified(raw, manifest, tmp_path / "valid")
    assert (tmp_path / "valid/queries.json").read_bytes() == b"[]"
    with pytest.raises(ValueError, match="differs"):
        unpack_verified(raw + b"x", manifest, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_even_hash_verified_paths_cannot_escape_destination(tmp_path):
    raw, manifest = archive("../escape.txt")
    with pytest.raises(ValueError, match="unsafe"):
        unpack_verified(raw, manifest, tmp_path / "input")
    assert not (tmp_path / "escape.txt").exists()
