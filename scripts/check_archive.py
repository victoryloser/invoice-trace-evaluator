"""Verify that source archives contain code and contracts, without trace data."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
from build_release import ROOT, DATA_SUFFIXES


def main():
    target = ROOT/"dist/invoice-trace-evaluator.zip"
    with ZipFile(target) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert set(archive.namelist()) == set(manifest["files"]) | {"MANIFEST.json"}
        assert {"README.md", "README.zh-CN.md", "LICENSE", ".github/workflows/ci.yml", "docs/data-format.md", "src/invoice_eval/resources/output.schema.json"}.issubset(manifest["files"])
        for name, item in manifest["files"].items():
            path = Path(name)
            assert not path.is_absolute() and ".." not in path.parts
            assert not set(path.parts) & {"material", "examples", "data", "runs", ".venv", ".build"}, name
            assert path.suffix.lower() not in DATA_SUFFIXES, name
            assert path.name not in {".env", "secrets.toml"}, name
            assert not path.name.startswith(".env.") or path.name == ".env.example", name
            payload = archive.read(name)
            assert hashlib.sha256(payload).hexdigest() == item["sha256"], name
            assert len(payload) == item["bytes"], name
    print(f"Source archive isolation and checksums passed: {len(manifest['files'])} files")


if __name__ == "__main__":
    main()
