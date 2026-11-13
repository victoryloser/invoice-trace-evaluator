"""Packaged program contracts, without trace records or reference labels."""
from pathlib import Path

RESOURCE_ROOT = Path(__file__).with_name("resources")
DEFAULT_CONFIG = RESOURCE_ROOT / "default.json"
OUTPUT_SCHEMA = RESOURCE_ROOT / "output.schema.json"
WORKFLOW = RESOURCE_ROOT / "workflow.json"
RULES = RESOURCE_ROOT / "rules.md"
SPLIT = RESOURCE_ROOT / "split.json"
