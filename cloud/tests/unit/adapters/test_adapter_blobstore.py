import hashlib
import io

import pytest

from romulus_cloud.adapters.blobstore import (
    BlobNotFound,
    BlobTooLarge,
    FsBlobStore,
    HashMismatch,
    InvalidHash,
    build_blobstore,
)

DATA = b"recording bytes " * 1000
SHA = hashlib.sha256(DATA).hexdigest()


def test_put_get_round_trip_at_content_address(tmp_path):
    store = FsBlobStore(tmp_path)
    assert not store.exists(SHA)
    assert store.put(SHA, io.BytesIO(DATA)) == len(DATA)
    assert store.exists(SHA)
    assert store.size(SHA) == len(DATA)
    assert store.path_for(SHA) == tmp_path / "sha256" / SHA[:2] / SHA[2:4] / SHA
    with store.get(SHA) as f:
        assert f.read() == DATA


def test_put_streams_in_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr("romulus_cloud.adapters.blobstore.CHUNK_SIZE", 7)
    store = FsBlobStore(tmp_path)
    store.put(SHA, io.BytesIO(DATA))
    with store.get(SHA) as f:
        assert f.read() == DATA


def test_re_put_is_idempotent(tmp_path):
    store = FsBlobStore(tmp_path)
    store.put(SHA, io.BytesIO(DATA))
    assert store.put(SHA, io.BytesIO(DATA)) == len(DATA)
    with store.get(SHA) as f:
        assert f.read() == DATA


def test_mismatch_is_refused_and_leaves_nothing(tmp_path):
    store = FsBlobStore(tmp_path)
    with pytest.raises(HashMismatch) as exc:
        store.put(SHA, io.BytesIO(DATA + b"corrupt"))
    assert exc.value.expected == SHA
    assert not store.exists(SHA)
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == []


def test_invalid_hash_is_refused(tmp_path):
    store = FsBlobStore(tmp_path)
    for bad in ["abc", SHA.upper(), "../" + SHA[3:], SHA + "0"]:
        with pytest.raises(InvalidHash):
            store.exists(bad)


def test_missing_blob(tmp_path):
    store = FsBlobStore(tmp_path)
    with pytest.raises(BlobNotFound):
        store.get(SHA)
    with pytest.raises(BlobNotFound):
        store.size(SHA)
    assert store.delete(SHA) is False


def test_delete(tmp_path):
    store = build_blobstore(tmp_path)
    store.put(SHA, io.BytesIO(DATA))
    assert store.delete(SHA) is True
    assert not store.exists(SHA)


def test_empty_blob(tmp_path):
    empty_sha = hashlib.sha256(b"").hexdigest()
    store = FsBlobStore(tmp_path)
    assert store.put(empty_sha, io.BytesIO(b"")) == 0
    assert store.exists(empty_sha)


def test_writer_streams_into_the_store_and_commits_once(tmp_path):
    store = FsBlobStore(tmp_path)
    with store.writer() as w:
        w.write(DATA[:5])
        w.write(DATA[5:])
        assert w.sha256 == SHA and w.size == len(DATA)
        assert w.commit(SHA) == len(DATA)
    with store.get(SHA) as f:
        assert f.read() == DATA
    assert list((tmp_path / "incoming").iterdir()) == []  # renamed into place, not copied


def test_writer_into_an_existing_blob_drops_its_temp_file(tmp_path):
    store = FsBlobStore(tmp_path)
    store.put(SHA, io.BytesIO(DATA))
    with store.writer() as w:
        w.write(DATA)
        assert w.commit(SHA) == len(DATA)
    assert list((tmp_path / "incoming").iterdir()) == []


def test_writer_cap_mismatch_and_abandon_leave_nothing(tmp_path):
    store = FsBlobStore(tmp_path)
    with store.writer(max_bytes=3) as w, pytest.raises(BlobTooLarge):
        w.write(b"four")
    with store.writer() as w, pytest.raises(HashMismatch):
        w.write(DATA + b"x")
        w.commit(SHA)
    with store.writer() as w:
        w.write(DATA)  # never committed
    assert not store.exists(SHA)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []
