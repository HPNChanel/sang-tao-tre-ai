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
        assert {"Dockerfile", "run.py", "src/plan_schema.json", "README.md"} <= set(names)
        assert all(name.startswith("src/") or name in {"Dockerfile", "requirements.txt", "run.py", "SOURCE_MANIFEST.json", "README.md"} for name in names)
        readme = archive.read("README.md").decode("utf-8")
        assert "docker run" in readme and "pip install -r requirements.txt" in readme
        for argument in ("--xlsx-dir", "--queries", "--output", "--openrouter-key",
                         "--openrouter-model", "--openrouter-base-url"):
            assert argument in readme
        assert not any(name.endswith((".xlsx", ".csv", ".env", ".pyc")) for name in names)
        manifest = json.loads(archive.read("SOURCE_MANIFEST.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest


def test_readme_states_the_packaged_profile_and_leads_the_repository_readme():
    from build_submission_package import submission_readme
    root = Path(__file__).resolve().parents[1]
    readme = submission_readme({"Dockerfile": (root / "Dockerfile").read_bytes()}).decode("utf-8")
    assert (root / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n").startswith(readme)
    hybrid = submission_readme({"Dockerfile": b"ENV DS2_SOLVER_PROFILE=hybrid QWEN_REVIEW_POLICY=selective "
                                              b"QWEN_MAX_OUTPUT_TOKENS=1200\n"}).decode("utf-8")
    assert "export DS2_SOLVER_PROFILE=hybrid" in hybrid and "export QWEN_MAX_OUTPUT_TOKENS=1200" in hybrid
    assert "solver profile is `hybrid`" in hybrid and "CellPlanV2" in hybrid
    legacy = submission_readme({"Dockerfile": b"ENV QWEN_REVIEW_POLICY=selective QWEN_MAX_OUTPUT_TOKENS=600\n"}).decode("utf-8")
    assert "export DS2_SOLVER_PROFILE=legacy" in legacy and "CellPlanV2" not in legacy


def test_dockerfile_ships_the_frozen_candidate_configuration():
    docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV DS2_SOLVER_PROFILE=hybrid QWEN_REVIEW_POLICY=selective QWEN_MAX_OUTPUT_TOKENS=1200" in docker
