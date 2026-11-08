"""code_lockin.stale_verify: CODER_VERIFY_DIR fits the code it was generated from and that
code's descendants; anything else fails closed with "regenerate" (PR #38 review, finding 4)."""

import json

import pytest

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.services import registry
from romulus_cloud.services.code_lockin import VERIFY_MANIFEST, stale_verify

KW = {"actor": "human:test", "actor_class": "human"}


def _manifest(rev):
    return {VERIFY_MANIFEST: json.dumps({"generated_from": {"code_rev": rev}}).encode()}


@pytest.fixture
def lineage(db_session, settings):
    """dev0000 → dev0d1p → coder01."""
    store = build_blobstore(settings.blob_dir)
    rows = {}
    parent = None
    for i, rev in enumerate(("dev0000", "dev0d1p", "coder01")):
        data = registry.build_tarball({"device/x.py": f"{i}\n".encode()})
        rows[rev], _ = registry.create_code(
            db_session, store, data, rev=rev, parent_rev=parent, **KW
        )
        parent = rev
    db_session.flush()
    return rows


@pytest.mark.parametrize("base", ["dev0000", "dev0d1p", "coder01"])
def test_verify_fits_its_code_and_every_descendant(db_session, lineage, base):
    assert stale_verify(db_session, _manifest("dev0000"), lineage[base]) is None


def test_verify_from_a_descendant_does_not_fit_an_ancestor(db_session, lineage):
    why = stale_verify(db_session, _manifest("coder01"), lineage["dev0d1p"])
    assert why.startswith("stale verify: CODER_VERIFY_DIR was generated from code coder01")
    assert "make device-verify" in why


@pytest.mark.parametrize("raw", [b"not json", b"{}", json.dumps({"generated_from": {}}).encode()])
def test_an_unreadable_manifest_fails_closed(db_session, lineage, raw):
    why = stale_verify(db_session, {VERIFY_MANIFEST: raw}, lineage["dev0000"])
    assert "names no code_rev" in why


def test_a_verify_dir_without_a_manifest_is_not_tied_to_a_version(db_session, lineage):
    assert stale_verify(db_session, {"verify/test_x.py": b"x"}, lineage["dev0000"]) is None
