import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_submission_package import create_package


def test_package_is_deterministic_and_contains_only_runtime_sources(tmp_path):
    one, two = tmp_path / "one.zip", tmp_path / "two.zip"
    first = create_package(one)
    assert create_package(two) == first
    assert one.read_bytes() == two.read_bytes()
    assert first["zip_bytes"] < 100_000_000
    with zipfile.ZipFile(one) as archive:
        names = archive.namelist()
        assert "Dockerfile" in names and "run.py" in names and "src/plan_schema.json" in names
        assert all(name.startswith("src/") or name in {"Dockerfile", "requirements.txt", "run.py", "SOURCE_MANIFEST.json", "README_SUBMISSION.md"} for name in names)
        assert not any(name.endswith((".xlsx", ".csv", ".env", ".pyc")) for name in names)
        manifest = json.loads(archive.read("SOURCE_MANIFEST.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
