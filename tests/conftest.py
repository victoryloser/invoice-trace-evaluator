import json
import pytest
from invoice_eval.loader import load_cases
from invoice_eval.pipeline import ROOT
from invoice_eval.resources import DEFAULT_CONFIG

@pytest.fixture
def cases():
    path = next((ROOT / "material").glob("02*.jsonl"), None)
    if path is None:
        pytest.skip("Historical regression requires local traces, which are not distributed")
    return load_cases(path)


@pytest.fixture
def local_material():
    if not all(next((ROOT/"material").glob(pattern), None) for pattern in ("02*.jsonl", "03*.jsonl", "04*.jsonl", "06*.json")):
        pytest.skip("Historical regression requires local material files, which are not distributed")

@pytest.fixture
def config():
    return json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
