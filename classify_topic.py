#!/usr/bin/env python3
"""Sort the archive into a topic subfolder using the enrichment summaries.

Keyword matching is not good enough for this. "Amazon" appears in dozens of
transcripts, but most are someone mentioning a purchase — the word says nothing
about what the recording is *about*. This classifies from the generated
title/topic/summary instead, which describe subject matter rather than
vocabulary, and it is cheap because those are two sentences rather than a
40,000-character transcript.

Usage:
    python classify_topic.py --topic amazon --folder amazon
    python classify_topic.py --topic amazon --folder amazon --move
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import anthropic

DEFAULT_DATA = Path(r"C:\Users\ezras\memo-machine-data")
MODEL = "claude-opus-5"
CHUNK = 40

TOPICS = {
    "amazon": {
        "include": (
            "The recording belongs to the owner's working life at Amazon. That "
            "covers the AHT / average handle time project, complexity bands, MFI "
            "cases, pilot versus production-floor data, Concurrency One, "
            "associates and queues, AIS, Paragon, RMS, QuickSight, CTI and FBA "
            "work; org, headcount and layoff matters; promotion, peer-review and "
            "career-track (TPM/TFC) discussion and mentorship for it; interviews "
            "for internal Amazon roles; internal Amazon or AWS training courses "
            "and workshops on any subject; and one-to-one or team meetings with "
            "Amazon colleagues about work, even when the specific subject is "
            "unfamiliar. When a recording is a work conversation with a recurring "
            "Amazon colleague, include it."
        ),
        "exclude": (
            "Someone mentioning buying something on Amazon, Amazon as a retailer, "
            "or AWS used as infrastructure for the owner's own startup projects "
            "(Hard Shell, Stream Kinetics, Seattle Unity) rather than for his "
            "Amazon employment."
        ),
    },
    "venture": {
        "include": (
            "The recording concerns the owner's own venture business rather than "
            "his employer. That covers QED (the company being incorporated) and "
            "its C-corp formation, operating agreements, cap table, equity splits "
            "and legal setup; the products — Hard Shell / hardshell.app, Stream "
            "Kinetics, Scout, Todd Toolkit, Omega, synapse patterns and the "
            "marketplace plugin work; client and prospect engagements run through "
            "that business, including DADS, United Way, Seattle Unity, Endo DNA, "
            "Empty Throne Games, church software and nonprofit discovery calls; "
            "Techline Ventures; investor, funding, runway and Mercury banking "
            "conversations; board meetings, board demos and pitch preparation; "
            "and product, dev-environment, sprint and go-to-market working "
            "sessions with the venture collaborators — Josh, Aaron, Paul and "
            "others in that group."
        ),
        "exclude": (
            "Work belonging to the owner's Amazon employment (AHT, MFI, AIS, "
            "Paragon, associates, promotion and org matters), interviews for "
            "jobs at other companies, and purely personal or social conversations "
            "with no business content. Also exclude the separate Techland "
            "Ventures / Geode Solutions business run with Charlie."
        ),
    },
    "techland": {
        "include": (
            "The recording concerns Techland Ventures or Geode Solutions — the "
            "advisory and PMO business the owner runs with Charlie. Transcription "
            "renders the name inconsistently as Techland, TechLine, Tech Land or "
            "Tech Line; treat them all as the same company. The subject matter "
            "includes PMO syncs and triage, the deal pipeline and prospecting, "
            "JLL, DCC and data-centre cluster deals, geothermal and ag-tech "
            "verticals, a manufacturing OS, Shreveport, Vietnam, Japan/JETRO and "
            "Netherlands business travel, entity structuring for Techland or "
            "Geode, Asana build-out for that PMO, pitch packages and capital "
            "channels, the black-box AI tool, Geode platform and operating-model "
            "work, and weekly 1:1s or strategy sessions with Charlie about any of "
            "the above."
        ),
        "exclude": (
            "Charlie in his Amazon capacity — AHT, RPH, MFI, AIS, Paragon, "
            "concurrency, associates, queues, Amazon layoffs and promotion talk. "
            "Also exclude the owner's separate QED venture with Josh and Aaron "
            "(Hard Shell, Stream Kinetics, Scout, Seattle Unity, United Way, "
            "DADS, church software), and purely personal conversations."
        ),
    },
}

SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "match": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string", "description": "Under 15 words."},
                },
                "required": ["id", "match", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["classifications"],
    "additionalProperties": False,
}


def load(data: Path) -> list:
    rows = []
    for path in sorted((data / "enriched").glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def classify(client, rows: list, topic: dict) -> dict:
    system = (
        "You sort voice-memo summaries into one topic bucket.\n\n"
        f"INCLUDE when: {topic['include']}\n\n"
        f"EXCLUDE when: {topic['exclude']}\n\n"
        "Judge what the recording is about, not which words appear in it. A "
        "passing mention is not a match. Return one entry per numbered item, "
        "using the same id. Use confidence 'low' when the summary is too thin "
        "to tell."
    )
    verdicts = {}
    for start in range(0, len(rows), CHUNK):
        block = rows[start:start + CHUNK]
        listing = "\n\n".join(
            f"[{start + i}] title: {r['title']}\n"
            f"     topic: {r['topic']}\n"
            f"     summary: {r['summary'][:400]}"
            for i, r in enumerate(block)
        )
        message = client.messages.create(
            model=MODEL, max_tokens=8000,
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            system=system,
            messages=[{"role": "user", "content": listing}],
        )
        if message.stop_reason == "refusal":
            print(f"  chunk at {start} declined")
            continue
        for block_out in message.content:
            if block_out.type == "text":
                for c in json.loads(block_out.text)["classifications"]:
                    verdicts[c["id"]] = c
        print(f"  classified {min(start + CHUNK, len(rows))}/{len(rows)}", flush=True)
    return verdicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--topic", required=True, choices=sorted(TOPICS))
    ap.add_argument("--folder", required=True, help="subfolder name under library/")
    ap.add_argument("--move", action="store_true", help="actually move the files")
    ap.add_argument("--reclaim", nargs="*", default=[], metavar="FOLDER",
                    help="topic folders this run may take files back out of, when "
                         "an earlier, broader topic claimed them first")
    ap.add_argument("--min-confidence", choices=["high", "medium", "low"], default="low",
                    help="skip matches below this confidence (default: keep all)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in this process.")
        return 2

    data, rows = args.data, load(args.data)
    print(f"{len(rows)} enriched recordings")

    cache = data / f"classification_{args.topic}.json"
    if cache.exists():
        verdicts = {int(k): v for k, v in
                    json.loads(cache.read_text(encoding="utf-8")).items()}
        print(f"using cached classification ({cache.name})")
    else:
        verdicts = classify(anthropic.Anthropic(), rows, TOPICS[args.topic])
        cache.write_text(json.dumps(verdicts, indent=2), encoding="utf-8")

    # Map each row to its current on-disk filename via index.csv.
    import csv as _csv
    names = {}
    with open(data / "index.csv", encoding="utf-8-sig") as fh:
        for r in _csv.DictReader(fh):
            names[r["file_hash"]] = r["archive_filename"]

    hits = [(i, rows[i], verdicts[i]) for i in sorted(verdicts) if verdicts[i]["match"]]
    print(f"\nmatched {len(hits)} of {len(rows)}")
    for conf in ("high", "medium", "low"):
        group = [h for h in hits if h[2]["confidence"] == conf]
        if not group:
            continue
        print(f"\n--- {conf} confidence ({len(group)}) ---")
        for i, row, v in group:
            print(f"  {names.get(row['_hash'], row['_stem'])[:70]}")
            print(f"      {v['reason']}")

    # A weak match can be wrong in a way that matters: the recording may already
    # sit in another topic folder that scored it highly, and moving it on a low
    # signal downgrades a confident placement to an unconfident one.
    rank = {"high": 3, "medium": 2, "low": 1}
    floor = rank[args.min_confidence]
    if floor > 1:
        skipped = [h for h in hits if rank[h[2]["confidence"]] < floor]
        hits = [h for h in hits if rank[h[2]["confidence"]] >= floor]
        if skipped:
            print(f"\nbelow --min-confidence {args.min_confidence}, left in place "
                  f"({len(skipped)}):")
            for i, row, v in skipped:
                print(f"  {names.get(row['_hash'], row['_stem'])[:66]}")

    dest = data / "library" / args.folder
    if not args.move:
        print(f"\ndry run — nothing moved. Add --move to move {len(hits)} "
              f"recordings into library/{args.folder}/")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    library = data / "library"
    moved, reclaimed, elsewhere, missing = 0, [], [], []
    for _, row, _v in hits:
        name = names.get(row["_hash"])
        if not name:
            continue

        # A recording can belong to more than one topic. By default whichever
        # folder claimed it first keeps it, and we report rather than silently
        # counting it as moved. --reclaim names the folders a narrower topic is
        # allowed to take files back out of.
        source = library if (library / name).exists() else None
        if source is None:
            found = next((p for p in library.glob(f"*/{name}")), None)
            if found is None:
                missing.append(name)
                continue
            if found.parent.name not in args.reclaim:
                elsewhere.append(f"{name}  (in {found.parent.name}/)")
                continue
            source = found.parent
            reclaimed.append(f"{name}  (from {found.parent.name}/)")

        for candidate in (name, Path(name).stem + ".txt"):
            src = source / candidate
            if src.exists():
                shutil.move(str(src), str(dest / candidate))
        moved += 1

    print(f"\nmoved {moved} recordings (audio + transcript) into library/{args.folder}/")
    if reclaimed:
        print(f"reclaimed from another topic folder ({len(reclaimed)}):")
        for n in reclaimed:
            print(f"  {n}")
    if elsewhere:
        print(f"already filed under another topic, left in place ({len(elsewhere)}):")
        for n in elsewhere:
            print(f"  {n}")
    if missing:
        print(f"NOT FOUND ({len(missing)}):")
        for n in missing:
            print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
