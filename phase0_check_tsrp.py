#!/usr/bin/env python3
"""Phase 0 verification gate: does this .m4a carry an Apple `tsrp` transcript atom?

Usage:
    python phase0_check_tsrp.py path/to/memo.m4a

Walks the MPEG-4 box tree looking for a `tsrp` atom, then heuristically pulls
readable text out of its payload and prints the first 200 characters.

Exit codes: 0 = atom found with extractable text, 1 = atom found but no text,
2 = no atom, 3 = usage/IO error.
"""

import json
import sys
from pathlib import Path

# Container boxes whose payload is itself a sequence of boxes.
CONTAINERS = {b"moov", b"udta", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"mvex"}

# `meta` is the awkward one. In ISO-BMFF (.m4a, brand "M4A ") it is a *full* box:
# 4 bytes of version/flags precede its children. In QuickTime (.qta, brand
# "qt  ") it is a plain container with children starting immediately. Guessing
# wrong desyncs the parse and the tsrp atom below it becomes invisible, so the
# variant is detected per box rather than assumed from the file's brand.
FULLBOX_CONTAINERS = {b"meta"}


def _looks_like_box_type(raw: bytes) -> bool:
    return len(raw) == 4 and all(0x20 <= b < 0x7F for b in raw)


def meta_children_offset(data: bytes, payload_start: int, payload_end: int) -> int:
    """Return where `meta`'s child boxes begin: +0 (QuickTime) or +4 (ISO-BMFF)."""
    if _looks_like_box_type(data[payload_start + 4 : payload_start + 8]):
        return payload_start  # plain container — a box header sits at offset 0
    if _looks_like_box_type(data[payload_start + 8 : payload_start + 12]):
        return payload_start + 4  # full box — version/flags then a box header
    return payload_start + 4  # ambiguous; ISO-BMFF is the safer default


def walk_boxes(data: bytes, start: int, end: int, depth: int = 0):
    """Yield (type, payload_start, payload_end, depth) for every box in [start, end)."""
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos : pos + 4], "big")
        box_type = data[pos + 4 : pos + 8]
        header = 8
        if size == 1:  # 64-bit largesize
            if pos + 16 > end:
                break
            size = int.from_bytes(data[pos + 8 : pos + 16], "big")
            header = 16
        elif size == 0:  # box extends to end of enclosing scope
            size = end - pos
        if size < header or pos + size > end:
            break  # malformed; stop walking this scope rather than looping
        payload_start = pos + header
        yield box_type, payload_start, pos + size, depth
        if box_type in CONTAINERS:
            yield from walk_boxes(data, payload_start, pos + size, depth + 1)
        elif box_type in FULLBOX_CONTAINERS:
            child_start = meta_children_offset(data, payload_start, pos + size)
            yield from walk_boxes(data, child_start, pos + size, depth + 1)
        pos += size


def _qt_keys(data: bytes, start: int, end: int):
    """Parse a `keys` box into an ordered list of metadata key names.

    Layout: version/flags (4) | entry_count (4) | entries, each
    size (4) | namespace (4, e.g. "mdta") | name (size - 8).
    """
    names, pos = [], start + 8
    while pos + 8 <= end:
        size = int.from_bytes(data[pos : pos + 4], "big")
        if size < 8 or pos + size > end:
            break
        names.append(data[pos + 8 : pos + size].decode("utf-8", errors="replace"))
        pos += size
    return names


def _qt_ilst(data: bytes, start: int, end: int):
    """Parse an `ilst` box into {key_index: payload_bytes}.

    Layout: items, each size (4) | index (4) | `data` box, where the data box is
    size (4) | "data" (4) | type (4) | locale (4) | payload.
    """
    items, pos = {}, start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos : pos + 4], "big")
        index = int.from_bytes(data[pos + 4 : pos + 8], "big")
        if size < 8 or pos + size > end:
            break
        inner = pos + 8
        while inner + 8 <= pos + size:
            isize = int.from_bytes(data[inner : inner + 4], "big")
            if isize < 16 or inner + isize > pos + size:
                break
            if data[inner + 4 : inner + 8] == b"data":
                items[index] = data[inner + 16 : inner + isize]
                break
            inner += isize
        pos += size
    return items


def find_tsrp_payload(data: bytes):
    """Locate the transcript payload, whichever container variant is in use.

    Two known layouts:
      .m4a (ISO-BMFF) — moov > trak > udta > tsrp, holding raw JSON
      .qta (QuickTime) — moov > trak > meta > keys/ilst, key
                         "com.apple.VoiceMemos.tsrp" indexing into ilst

    A file carries more than one `meta` box (the transcript's under `trak`, plus
    a small one under `udta`), so keys/ilst must be paired *within the same*
    meta — matching them globally lets the trailing udta ilst win and the
    transcript disappears.

    Returns (payload, source_label) or (None, None).
    """
    meta_spans = []
    for box_type, p_start, p_end, _ in walk_boxes(data, 0, len(data)):
        if box_type == b"tsrp":
            return data[p_start:p_end], "udta/tsrp"
        if box_type == b"meta":
            meta_spans.append((p_start, p_end))

    for m_start, m_end in meta_spans:
        keys_span = ilst_span = None
        child_start = meta_children_offset(data, m_start, m_end)
        for box_type, p_start, p_end, depth in walk_boxes(data, child_start, m_end):
            if depth != 0:
                continue
            if box_type == b"keys":
                keys_span = (p_start, p_end)
            elif box_type == b"ilst":
                ilst_span = (p_start, p_end)
        if not (keys_span and ilst_span):
            continue
        names = _qt_keys(data, *keys_span)
        items = _qt_ilst(data, *ilst_span)
        for i, name in enumerate(names, start=1):  # ilst indices are 1-based
            if name.endswith(".tsrp") and i in items:
                return items[i], f"meta/ilst[{name}]"
    return None, None


def load_metadata_region(path) -> bytes:
    """Read just the `moov` box from a file, skipping the multi-hundred-MB `mdat`.

    Voice memo audio dwarfs its metadata, so slurping whole files to reach a ~2 MB
    box means reading tens of GB per pass. Top-level headers are walked by seek and
    only `moov` is pulled into memory. Falls back to the whole file if `moov` is not
    found at the top level.
    """
    with open(path, "rb") as fh:
        pos = 0
        while True:
            fh.seek(pos)
            header = fh.read(16)
            if len(header) < 8:
                break
            size = int.from_bytes(header[:4], "big")
            box_type = header[4:8]
            if size == 1:
                size = int.from_bytes(header[8:16], "big")
            elif size == 0:
                fh.seek(0, 2)
                size = fh.tell() - pos
            if size < 8:
                break
            if box_type == b"moov":
                fh.seek(pos)
                return fh.read(size)
            pos += size
        fh.seek(0)
        return fh.read()


def parse_tsrp(payload: bytes):
    """Decode a tsrp payload into (text, word_timings).

    The payload is JSON:
        {"locale": {...},
         "attributedString": {"runs": [tok, idx, tok, idx, ...],
                              "attributeTable": [{"timeRange": [start, end]}, ...]}}

    `runs` alternates a text token with an index into `attributeTable`, so the
    transcript carries per-word timings. An untranscribed recording still gets a
    tsrp atom, but with `attributedString` as an empty string — atom presence
    alone is NOT evidence of a transcript.
    """
    obj = json.loads(payload.decode("utf-8", errors="replace"))
    attributed = obj.get("attributedString")

    if isinstance(attributed, str):  # empty / untranscribed
        return attributed, []
    if not isinstance(attributed, dict):
        return "", []

    runs = attributed.get("runs", [])
    table = attributed.get("attributeTable", [])
    tokens = [x for x in runs if isinstance(x, str)]

    timings = []
    for i in range(0, len(runs) - 1, 2):
        tok, idx = runs[i], runs[i + 1]
        if isinstance(tok, str) and isinstance(idx, int) and 0 <= idx < len(table):
            span = table[idx].get("timeRange")
            if span:
                timings.append((tok, span[0], span[1]))

    return "".join(tokens), timings


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 3
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"ERROR: not a file: {path}")
        return 3

    data = path.read_bytes()
    print(f"File: {path.name} ({len(data):,} bytes)")

    for box_type, p_start, p_end, depth in walk_boxes(data, 0, len(data)):
        label = box_type.decode("ascii", errors="replace")
        print(f"{'  ' * depth}{label}  ({p_end - p_start:,} bytes)")

    payload, source = find_tsrp_payload(data)
    if payload is None:
        print("\nRESULT: FAIL — no transcript payload in this file.")
        print("Either the transfer stripped it, the recording predates iOS 18,")
        print("or it was never opened in the Voice Memos app.")
        return 2

    print(f"\ntranscript payload found via {source}: {len(payload):,} bytes")
    try:
        text, timings = parse_tsrp(payload)
    except Exception as exc:
        print(f"RESULT: PARTIAL — payload present but did not parse: {exc}")
        return 1

    if not text.strip():
        print("RESULT: PARTIAL — payload present but transcript is empty.")
        print("The recording was never transcribed on-device. Remedy: open it once")
        print("in the Voice Memos app to trigger transcription, then re-export.")
        return 1

    print(f"Extracted {len(text):,} chars, {len(timings):,} word timings. First 200:\n")
    print(text[:200])
    print("\nRESULT: PASS — transcript survived the transfer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
