"""Loads repo config files that domain functions take as data (kept out of `domain/`)."""

from pathlib import Path

import yaml

from romulus_cloud.domain.triage import PartsCatalog, parse_catalog

PARTS_PATH = Path(__file__).resolve().parents[2] / "config" / "parts.yaml"
"""`cloud/config/parts.yaml`; the Dockerfile copies `cloud/config` next to `cloud/src`."""


def load_parts(path: Path | str = PARTS_PATH) -> PartsCatalog:
    """Read and validate the parts catalogue (raises `CatalogError` if it is malformed)."""
    with open(path, encoding="utf-8") as f:
        return parse_catalog(yaml.safe_load(f))
