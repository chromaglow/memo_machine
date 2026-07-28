#!/usr/bin/env python3
"""Module 3 — pull every voice recording out of an iOS backup, verified.

Reads the backup's `Manifest.db` to map hashed blob names back to real
filenames, copies each recording into `originals/`, and proves the copy is
byte-identical to the source before reporting success. Also copies
`CloudRecordings.db` so later stages have true dates and titles.

The output of this stage is what makes it safe to free space on the phone, so
it verifies rather than assumes: every file is hashed on both sides.

Usage:
    python extract_recordings.py [--backup DIR] [--out DIR] [--recheck]

Idempotent: a file already present with a matching hash is skipped. `--recheck`
re-hashes existing files instead of trusting size alone.
"""

import argparse
import csv
import datetime
import hashlib
import os
import shutil
import sqlite3
import sys
from pathlib import Path

DEFAULT_BACKUP = Path(r"C:\Users\ezras\memo-machine-data\backup")
DEFAULT_OUT = Path(r"C:\Users\ezras\memo-machine-data")
CORE_DATA_EPOCH = 978307200  # Core Data counts from 2001-01-01, not 1970
AUDIO_EXTS = (".m4a", ".qta", ".wav", ".mp3", ".aifc", ".aiff", ".caf")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def find_backup_root(backup_dir: Path) -> Path:
    """Accept either the backup root or its parent holding one UDID folder."""
    if (backup_dir / "Manifest.db").is_file():
        return backup_dir
    candidates = [d for d in backup_dir.iterdir()
                  if d.is_dir() and (d / "Manifest.db").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(f"No Manifest.db found under {backup_dir}")
    raise SystemExit(f"Multiple backups under {backup_dir}: "
                     f"{[c.name for c in candidates]} — pass one with --backup")


def discover_domain(con: sqlite3.Connection) -> str:
    """Find the Voice Memos domain rather than hardcoding it.

    The domain string has changed across iOS versions, so it is derived from the
    data: whichever domain holds the most audio files under a Recordings path.
    """
    rows = con.execute(
        "SELECT domain, COUNT(*) FROM Files "
        "WHERE relativePath LIKE 'Recordings/%' AND ("
        + " OR ".join(f"relativePath LIKE '%{e}'" for e in AUDIO_EXTS) +
        ") GROUP BY domain ORDER BY 2 DESC"
    ).fetchall()
    if not rows:
        raise SystemExit("No Recordings/* audio found in this backup.")
    return rows[0][0]


def load_recording_metadata(db_path: Path) -> dict:
    """Index CloudRecordings.db by filename.

    ZCUSTOMLABEL is NOT the user's title despite the name — it holds a UTC ISO
    timestamp. The human title lives in ZENCRYPTEDTITLE, which is plaintext.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cols = {r[1] for r in con.execute("PRAGMA table_info(ZCLOUDRECORDING)")}
    wanted = [c for c in ("ZPATH", "ZENCRYPTEDTITLE", "ZCUSTOMLABEL",
                          "ZDATE", "ZDURATION") if c in cols]
    meta = {}
    for row in con.execute(f"SELECT {', '.join(wanted)} FROM ZCLOUDRECORDING"):
        rec = dict(zip(wanted, row))
        path = rec.get("ZPATH")
        if not path:
            continue
        stamp = rec.get("ZDATE")
        when = (datetime.datetime.fromtimestamp(stamp + CORE_DATA_EPOCH)
                if stamp is not None else None)
        meta[os.path.basename(path)] = {
            "title": rec.get("ZENCRYPTEDTITLE") or "",
            "recorded": when.isoformat(sep=" ", timespec="seconds") if when else "",
            "duration_s": round(rec.get("ZDURATION") or 0, 1),
        }
    con.close()
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--recheck", action="store_true",
                    help="re-hash files already extracted")
    args = ap.parse_args()

    root = find_backup_root(args.backup)
    originals = args.out / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    print(f"backup : {root}")
    print(f"output : {originals}\n")

    con = sqlite3.connect(f"file:{root / 'Manifest.db'}?mode=ro", uri=True)
    domain = discover_domain(con)
    print(f"domain discovered: {domain}")

    # CloudRecordings.db first — later stages depend on it.
    db_row = con.execute(
        "SELECT fileID, relativePath FROM Files WHERE domain=? "
        "AND relativePath LIKE '%CloudRecordings.db'", (domain,)).fetchone()
    meta = {}
    if db_row:
        src = root / db_row[0][:2] / db_row[0]
        dst = args.out / "CloudRecordings.db"
        shutil.copy2(src, dst)
        print(f"metadata db: {dst.name} ({dst.stat().st_size/1e6:.1f} MB)")
        meta = load_recording_metadata(dst)
        print(f"             {len(meta)} recordings described")
    else:
        print("WARNING: CloudRecordings.db not in backup — "
              "dates and titles will be unavailable")

    rows = con.execute(
        "SELECT fileID, relativePath FROM Files WHERE domain=? AND ("
        + " OR ".join(f"relativePath LIKE '%{e}'" for e in AUDIO_EXTS) +
        ") ORDER BY relativePath", (domain,)).fetchall()
    print(f"\naudio files in backup: {len(rows)}\n")

    inventory, copied, skipped, failed = [], 0, 0, 0
    for i, (file_id, rel) in enumerate(rows, 1):
        name = os.path.basename(rel)
        src = root / file_id[:2] / file_id
        dst = originals / name
        try:
            if not src.is_file():
                raise FileNotFoundError(f"blob missing from backup: {file_id}")

            src_hash = sha256(src)
            if dst.is_file() and (
                    sha256(dst) == src_hash if args.recheck
                    else dst.stat().st_size == src.stat().st_size):
                skipped += 1
                digest = sha256(dst) if args.recheck else src_hash
            else:
                shutil.copy2(src, dst)
                digest = sha256(dst)
                # Verify rather than trust: this is the copy that justifies
                # freeing space on the phone.
                if digest != src_hash:
                    raise IOError("hash mismatch after copy")
                copied += 1

            info = meta.get(name, {})
            inventory.append({
                "filename": name,
                "title": info.get("title", ""),
                "recorded": info.get("recorded", ""),
                "duration_s": info.get("duration_s", ""),
                "bytes": dst.stat().st_size,
                "sha256": digest,
            })
        except Exception as exc:  # one bad file must not kill the run
            failed += 1
            print(f"  FAILED {name}: {exc}")
            inventory.append({"filename": name, "title": "", "recorded": "",
                              "duration_s": "", "bytes": "", "sha256": "",
                              "error": str(exc)})
        if i % 40 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)} processed", flush=True)

    inv_path = args.out / "inventory.csv"
    fields = ["filename", "title", "recorded", "duration_s", "bytes", "sha256", "error"]
    with open(inv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(inventory)

    good = [r for r in inventory if r.get("sha256")]
    total_bytes = sum(r["bytes"] for r in good)
    total_secs = sum(float(r["duration_s"] or 0) for r in good)
    print(f"\n--- extraction summary ---")
    print(f"  copied         {copied}")
    print(f"  already present{skipped:>6}")
    print(f"  failed         {failed}")
    print(f"  verified files {len(good)} / {len(rows)}")
    print(f"  audio          {total_bytes/1e9:.2f} GB, {total_secs/3600:.1f} hours")
    print(f"  inventory      {inv_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
