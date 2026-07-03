"""Tests for the object-store abstraction (local backend + URI routing)."""

import pytest

from track_me.storage import LocalStore, S3Store, from_uri


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.txt").write_bytes(b"hello world")
    (tmp_path / "a" / "two.json").write_text('{"x": 1}')
    (tmp_path / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0binary")
    return tmp_path


def test_local_list_keys_and_sizes(tree):
    store = LocalStore(tree)
    got = {o.key: o.size for o in store.list()}
    assert got == {"a/one.txt": 11, "a/two.json": 8, "b.jpg": 10}


def test_local_list_with_prefix(tree):
    store = LocalStore(tree)
    keys = sorted(o.key for o in store.list("a"))
    assert keys == ["a/one.txt", "a/two.json"]


def test_local_read_and_range(tree):
    store = LocalStore(tree)
    assert store.read("a/one.txt") == b"hello world"
    assert store.read_range("a/one.txt", 0, 5) == b"hello"
    assert store.read_range("a/one.txt", 6, 5) == b"world"


def test_from_uri_local(tree):
    store, prefix = from_uri(str(tree))
    assert isinstance(store, LocalStore)
    assert prefix == ""
    assert any(o.key == "b.jpg" for o in store.list())


def test_from_uri_s3():
    store, prefix = from_uri("s3://my-bucket/Takeout/2019")
    assert isinstance(store, S3Store)
    assert store.bucket == "my-bucket"
    assert prefix == "Takeout/2019"


def test_from_uri_gcs_not_yet():
    with pytest.raises(NotImplementedError):
        from_uri("gs://bucket/x")


def test_s3_store_uses_injected_client():
    class FakeS3:
        def get_object(self, Bucket, Key, Range=None):
            body = b"0123456789"
            if Range:
                lo, hi = Range.removeprefix("bytes=").split("-")
                body = body[int(lo) : int(hi) + 1]

            class B:
                def read(self_inner):
                    return body

            return {"Body": B()}

    s = S3Store("bkt", client=FakeS3())
    assert s.read("k") == b"0123456789"
    assert s.read_range("k", 2, 3) == b"234"
