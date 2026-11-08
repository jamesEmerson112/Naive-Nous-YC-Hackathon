"""`services.datasets.tar_bytes`: identical files always give an identical archive (F8)."""

import io
import tarfile

from romulus_cloud.domain import datasets as dd
from romulus_cloud.services.datasets import tar_bytes


def test_tar_bytes_is_deterministic_and_readable():
    files = {"root/b.json": b"{}", "root/a.jsonl": dd.jsonl([{"b": 1, "a": 2}])}
    first = tar_bytes(files)
    assert first == tar_bytes(dict(reversed(list(files.items()))))
    with tarfile.open(fileobj=io.BytesIO(first)) as tar:
        assert tar.getnames() == ["root/a.jsonl", "root/b.json"]
        assert tar.extractfile("root/a.jsonl").read() == b'{"a":2,"b":1}\n'
        assert all(m.mtime == 0 for m in tar.getmembers())
