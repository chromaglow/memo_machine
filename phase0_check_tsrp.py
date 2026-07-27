#!/usr/bin/env python3
"""Phase 0 verification gate: does this .m4a carry an Apple `tsrp` transcript atom?

Usage:
    python phase0_check_tsrp.py path/to/memo.m4a

Walks the MPEG-4 box tree looking for a `tsrp` atom, then heuristically pulls
readable text out of its payload and prints the first 200 characters.

Exit codes: 0 = atom found with extractable text, 1 = atom found but no text,
2 = no atom, 3 = usage/IO error.
"""

import sys
from pathlib import Path

# Container boxes whose payload is itself a sequence of boxes. `meta` is a
# full box: 4 bytes of version/flags precede its children.
CONTAINERS = {b"moov", b"udta", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"mvex"}
FULLBOX_CONTAINERS = {b"meta"}


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
            yield from walk_boxes(data, payload_start + 4, pos + size, depth + 1)
        pos += size


def extract_text(payload: bytes) -> str:
    """Pull printable UTF-8 runs out of an unknown binary payload."""
    runs, current = [], bytearray()
    text = payload.decode("utf-8", errors="replace")
    for ch in text:
        if ch.isprintable() or ch in "\n ":
            current += ch.encode("utf-8")
        else:
            if len(current) >= 4:
                runs.append(current.decode("utf-8"))
            current = bytearray()
    if len(current) >= 4:
        runs.append(current.decode("utf-8"))
    # The transcript is by far the longest readable run; join the substantial ones.
    return " ".join(r.strip() for r in runs if len(r) >= 12)


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

    tsrp_spans = []
    for box_type, p_start, p_end, depth in walk_boxes(data, 0, len(data)):
        label = box_type.decode("ascii", errors="replace")
        print(f"{'  ' * depth}{label}  ({p_end - p_start:,} bytes)")
        if box_type == b"tsrp":
            tsrp_spans.append((p_start, p_end))

    # Fallback: a tsrp atom hiding somewhere the walker didn't reach.
    if not tsrp_spans:
        idx = data.find(b"tsrp")
        if idx >= 4:
            size = int.from_bytes(data[idx - 4 : idx], "big")
            if 8 <= size <= len(data) - (idx - 4):
                print("note: tsrp found by raw scan outside the walked tree")
                tsrp_spans.append((idx + 4, idx - 4 + size))

    if not tsrp_spans:
        print("\nRESULT: FAIL — no tsrp atom in this file.")
        print("Either the transfer stripped it, the recording predates iOS 18,")
        print("or it was never opened in the Voice Memos app.")
        return 2

    start, endpos = tsrp_spans[0]
    print(f"\ntsrp atom present: {endpos - start:,} byte payload")
    text = extract_text(data[start:endpos])
    if not text:
        print("RESULT: PARTIAL — atom present but no readable text extracted.")
        return 1

    print(f"Extracted ~{len(text):,} chars of text. First 200:\n")
    print(text[:200])
    print("\nRESULT: PASS — transcript survived the transfer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
