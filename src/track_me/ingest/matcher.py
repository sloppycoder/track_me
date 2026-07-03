"""Match an image file to its Takeout sidecar JSON.

Google Takeout's sidecar naming has drifted over ~20 years and is genuinely
messy: ``.json`` vs ``.supplemental-metadata.json``, whole-filename truncation to
~51 chars, duplicate ``(1)`` counters that move to a different position, and
localized ``-edited`` suffixes that share the base photo's sidecar.

Rather than one clever regex, we try a cascade of strategies from most to least
reliable and stop at the first confident hit. The matcher refuses to guess when
a directory is ambiguous (returns ``None``) so the caller can flag for review
instead of attaching the wrong location.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

# Trailing "(12)" duplicate counter on a stem.
_COUNTER_RE = re.compile(r"^(?P<core>.*?)(?:\((?P<n>\d+)\))?$")

# Localized "edited" markers Google appends to derivative images. The edited
# copy reuses the *original* photo's sidecar, so we strip these and retry.
_EDITED_MARKERS = (
    "-edited",
    "-edit",
    "-bearbeitet",  # de
    "-modifié",  # fr
    "-modifie",
    "-ha editado",  # es
    "-editado",
    "-bewerkt",  # nl
    "-edytowane",  # pl
    "-redigerad",  # sv
    "-muokattu",  # fi
    "-editat",
    "-편집",  # ko
    "-編集済み",  # ja
)


def _split_counter(stem: str) -> tuple[str, int | None]:
    """'foo(1)' -> ('foo', 1); 'foo' -> ('foo', None)."""
    m = _COUNTER_RE.match(stem)
    assert m is not None  # the regex matches any string
    n = m.group("n")
    return m.group("core"), (int(n) if n is not None else None)


def _strip_edited(image_name: str) -> str | None:
    """Return the base image name with an '-edited' style suffix removed, or None."""
    stem, ext = os.path.splitext(image_name)
    low = stem.lower()
    for marker in _EDITED_MARKERS:
        if low.endswith(marker):
            return stem[: len(stem) - len(marker)] + ext
    return None


def _exact_candidates(image_name: str) -> list[str]:
    """Likely exact sidecar filenames for an image, most specific first."""
    stem, ext = os.path.splitext(image_name)
    core, n = _split_counter(stem)
    base = core + ext  # de-countered image name, e.g. "foo.jpg"

    cands: list[str] = []
    if n is not None:
        # Counter migrates to *after* the suffix on the sidecar side.
        cands += [
            f"{base}.supplemental-metadata({n}).json",
            f"{base}({n}).json",
        ]
    cands += [
        f"{image_name}.supplemental-metadata.json",
        f"{image_name}.json",
        f"{base}.supplemental-metadata.json",
        f"{base}.json",
    ]
    # De-duplicate preserving order.
    seen: set[str] = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def _implied_image_key(json_name: str) -> tuple[str, int | None]:
    """From a sidecar filename, recover (lowercased implied image name, counter).

    Strips '.json', a trailing counter, and any '.supplemental-*' suffix (even a
    truncated one). The result is a *prefix of* the real image name when the
    sidecar filename was truncated.
    """
    stem = json_name[:-5] if json_name.lower().endswith(".json") else json_name
    stem, n = _split_counter(stem)
    idx = stem.lower().find(".supplemental")
    if idx != -1:
        stem = stem[:idx]
    return stem.lower(), n


class _DirIndex:
    """Cached view of one album directory's sidecar files, built from the object
    listing + sidecar content read through the store (via ``matcher``)."""

    def __init__(self, directory: str, names: list[str], matcher: SidecarMatcher):
        self.directory = directory
        self.matcher = matcher
        # lowercased json filename -> object key
        self.by_name: dict[str, str] = {}
        # normalized sidecar 'title' -> list of (counter, key)
        self.by_title: dict[str, list[tuple[int, str]]] = {}
        # (implied image key, counter, key) for prefix fallback
        self.implied: list[tuple[str, int | None, str]] = []
        # lowercased names of non-json (image/video) siblings, for uniqueness
        self.media_names: list[str] = []
        self._build(names)

    def _key(self, name: str) -> str:
        return f"{self.directory}/{name}" if self.directory else name

    def _build(self, names: list[str]) -> None:
        for name in names:
            if not name.lower().endswith(".json"):
                self.media_names.append(name.lower())
                continue
            key = self._key(name)
            self.by_name[name.lower()] = key
            ikey, counter = _implied_image_key(name)
            self.implied.append((ikey, counter, key))
            data = self.matcher.read_json(key)
            title = data.get("title") if isinstance(data, dict) else None
            if isinstance(title, str) and title:
                _, jcounter = _split_counter(os.path.splitext(name[:-5])[0])
                tkey = title.lower()
                self.by_title.setdefault(tkey, []).append((jcounter or 0, key))
                stem_key = os.path.splitext(title)[0].lower()
                if stem_key != tkey:
                    self.by_title.setdefault(stem_key, []).append((jcounter or 0, key))
        for entries_list in self.by_title.values():
            entries_list.sort(key=lambda t: t[0])

    # --- matching tiers --------------------------------------------------
    def match(self, image_name: str) -> str | None:
        # Tier A: exact candidate filenames.
        for name in (image_name, _strip_edited(image_name)):
            if name is None:
                continue
            for cand in _exact_candidates(name):
                hit = self.by_name.get(cand.lower())
                if hit is not None:
                    return hit

        # Tier B: match on the sidecar's internal title (robust to filename
        # truncation -- the JSON *content* is never truncated).
        stem, ext = os.path.splitext(image_name)
        core, counter = _split_counter(stem)
        for base in (core + ext, core):
            hit = self._match_title(base.lower(), counter)
            if hit is not None:
                return hit
        de_edited = _strip_edited(image_name)
        if de_edited is not None:
            estem, eext = os.path.splitext(de_edited)
            ecore, _ = _split_counter(estem)
            for base in (ecore + eext, ecore):
                hit = self._match_title(base.lower(), counter)
                if hit is not None:
                    return hit

        # Tier C: truncation-tolerant prefix match (last resort, conservative).
        return self._match_prefix(image_name)

    def _match_title(self, key: str, counter: int | None) -> str | None:
        entries = self.by_title.get(key)
        if not entries:
            return None
        if counter is not None:
            for c, path in entries:
                if c == counter:
                    return path
        if len(entries) == 1:
            return entries[0][1]
        # Ambiguous duplicates with no usable counter -> don't guess.
        for c, path in entries:
            if c == 0:
                return path
        return None

    def _match_prefix(self, image_name: str) -> str | None:
        target = (_strip_edited(image_name) or image_name).lower()
        candidates: list[tuple[int, str]] = []
        for ikey, _counter, path in self.implied:
            if not ikey:
                continue
            # Sidecar filename truncated within the image name: its implied key
            # is a prefix of the full image name.
            if not target.startswith(ikey):
                continue
            if len(ikey) < max(6, len(target) // 2):
                continue
            # Only trust this if the prefix uniquely identifies one sibling image
            # -- otherwise a truncated, title-less sidecar is genuinely ambiguous.
            if sum(1 for m in self.media_names if m.startswith(ikey)) == 1:
                candidates.append((len(ikey), path))
        if not candidates:
            return None
        candidates.sort(key=lambda t: -t[0])
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return None
        return candidates[0][1]


class SidecarMatcher:
    """Finds the sidecar (object key) for an image key.

    Driven by an ``ObjectStore`` + a directory->filenames map from the listing.
    Per-directory indexes and parsed sidecars are built lazily and cached; a
    per-directory lock makes it safe to call ``find`` from many threads.
    """

    def __init__(self, store, files_by_dir: dict[str, list[str]]):
        self.store = store
        self.files_by_dir = files_by_dir
        self._indexes: dict[str, _DirIndex] = {}
        self._index_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._parsed: dict[str, dict | None] = {}
        self._parsed_lock = threading.Lock()

    @classmethod
    def for_local(cls, root: str | Path) -> SidecarMatcher:
        """Convenience: build a matcher over a local directory tree."""
        from track_me.storage import LocalStore

        store = LocalStore(root)
        files_by_dir: dict[str, list[str]] = defaultdict(list)
        for obj in store.list():
            directory, _, name = obj.key.rpartition("/")
            files_by_dir[directory].append(name)
        return cls(store, dict(files_by_dir))

    def read_json(self, key: str) -> dict | None:
        """Read + parse a sidecar's JSON, cached (each sidecar read once)."""
        with self._parsed_lock:
            if key in self._parsed:
                return self._parsed[key]
        try:
            raw = json.loads(self.store.read(key))
            data = raw if isinstance(raw, dict) else None
        except Exception:
            data = None
        with self._parsed_lock:
            self._parsed[key] = data
        return data

    def _index(self, directory: str) -> _DirIndex:
        index = self._indexes.get(directory)
        if index is not None:
            return index
        with self._index_locks[directory]:
            index = self._indexes.get(directory)
            if index is None:
                index = _DirIndex(directory, self.files_by_dir.get(directory, []), self)
                self._indexes[directory] = index
        return index

    def find(self, image_key: str) -> str | None:
        directory, _, name = image_key.rpartition("/")
        return self._index(directory).match(name)
