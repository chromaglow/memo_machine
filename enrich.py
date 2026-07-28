#!/usr/bin/env python3
"""Module 6 — turn each transcript into structured metadata via the Claude API.

Three modes:
    --sample N    enrich N recordings synchronously and print them (quality check)
    --submit      submit every un-enriched recording as one Batch API job (50% off)
    --collect ID  fetch a finished batch and write results to enriched/

Results are cached per recording in `enriched/<sha256>.json`, keyed by the audio
file's hash from inventory.csv. Re-running never re-spends on work already done.

Usage:
    python enrich.py --sample 3
    python enrich.py --submit
    python enrich.py --collect msgbatch_...
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import anthropic

DEFAULT_DATA = Path(r"C:\Users\ezras\memo-machine-data")
MODEL = "claude-opus-5"
MAX_TOKENS = 2000

# The archive's owner is in every recording, so naming him carries no
# information. The prompt says not to list him, but people address him by name
# constantly, which means the quote check passes and the rule slips through.
# Enforced here instead, where it is deterministic.
OWNER_NAMES = {"ezra"}

SYSTEM_PROMPT = """\
You extract structured metadata from voice memo transcripts for a personal archive.

The transcripts come from Apple's on-device speech recognition or local Whisper. \
They contain no speaker labels, and they contain transcription errors: misheard \
words, dropped words, and mangled proper nouns. Read through those errors where \
the intent is clear, and do not treat a garbled passage as meaningful content.

Rules that matter more than completeness:

PARTICIPANTS. Transcription gives words, not identities. This is the field most \
likely to be wrong, so it is governed by one mechanical test:

  For every name you list, quote the exact words from the transcript that
  establish it. If you cannot copy a verbatim quote, do not list the name.

The quote must show the person is *present in this recording*: a name spoken in \
address ("Thanks, Charlie"), a self-introduction ("this is Dana"), or a sign-off. \
A name merely discussed is not a participant — someone can be talked about for an \
hour without being on the call. Do not infer identity from the recording's title, \
the topic, the company, or who usually attends. Do not repair a garbled name into \
a plausible one: if the transcript says "Eugen", you may not list "Eugene" or \
"Eric". Never list Ezra — he is the archive's owner and is in every recording.

An empty list is the correct and expected answer for most recordings. A blank the \
owner fills in himself is far more useful than a wrong name he has to catch. When \
the list is empty, set participants_confidence to "none". Names you noticed but \
could not evidence belong in flags, not here.

ACTION ITEMS. Only commitments actually stated in the recording. Do not invent \
next steps that seem sensible, and do not convert topics of discussion into tasks.

SPARSE RECORDINGS. Some recordings are mostly silence or ambient noise and yield \
very little text. Summarize only what is there. Never pad a summary to make a \
thin recording sound substantial — say plainly that the recording is brief or \
largely inaudible, and flag it.

Write the summary in plain, factual language. No preamble, no editorializing."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Short human-readable title, at most 60 characters.",
        },
        "slug": {
            "type": "string",
            "description": (
                "Lowercase hyphenated filename-safe identifier, at most 40 "
                "characters. Letters, digits and hyphens only."
            ),
        },
        "category": {
            "type": "string",
            "enum": ["meeting", "call", "note-to-self", "idea", "other"],
        },
        "topic": {"type": "string", "description": "One sentence on what this is about."},
        "summary": {"type": "string", "description": "Two to four sentences."},
        # Each name must carry the transcript line that proves it. Requiring the
        # quote is what stops plausible-but-unevidenced names from being listed:
        # the model cannot fabricate the field without fabricating a quote too,
        # and a fabricated quote is checkable against the transcript.
        "participants": {
            "type": "array",
            "description": "People evidenced as present. Empty for most recordings.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "evidence": {
                        "type": "string",
                        "description": (
                            "Verbatim quote from the transcript showing this person "
                            "is present — addressed by name, self-introducing, or "
                            "signing off. Copied exactly, not paraphrased."
                        ),
                    },
                },
                "required": ["name", "evidence"],
                "additionalProperties": False,
            },
        },
        "participants_confidence": {
            "type": "string",
            "enum": ["high", "medium", "low", "none"],
        },
        "action_items": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Commitments explicitly stated. Empty if none.",
        },
        "flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything odd a human should look at. Empty if nothing.",
        },
    },
    "required": [
        "title", "slug", "category", "topic", "summary",
        "participants", "participants_confidence", "action_items", "flags",
    ],
    "additionalProperties": False,
}


def load_work(data: Path):
    """Pair each transcript with its audio metadata. Returns a list of jobs."""
    inv = {}
    with open(data / "inventory.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("sha256"):
                inv[Path(row["filename"]).stem] = row

    jobs = []
    for txt in sorted((data / "transcripts").glob("*.txt")):
        meta = inv.get(txt.stem)
        if not meta:
            continue
        text = txt.read_text(encoding="utf-8")
        if not text.strip():
            continue
        jobs.append({
            "stem": txt.stem,
            "hash": meta["sha256"],
            "title": meta.get("title", ""),
            "recorded": meta.get("recorded", ""),
            "duration_s": meta.get("duration_s", ""),
            "text": text,
        })
    return jobs


def user_content(job: dict) -> str:
    minutes = int(float(job["duration_s"] or 0)) // 60
    return (
        f"Recording date: {job['recorded'] or 'unknown'}\n"
        f"Duration: {minutes} minutes\n"
        f"Title the owner gave it: {job['title'] or '(none)'}\n\n"
        f"Transcript:\n{job['text']}"
    )


def request_params(job: dict) -> dict:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        # Straightforward extraction against an explicit rubric; low effort keeps
        # thinking tokens (billed as output) down. Thinking stays enabled —
        # disabling it on this model risks internal tags leaking into the text.
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content(job)}],
    }


def verify_participants(job: dict, payload: dict) -> dict:
    """Drop any participant whose evidence quote isn't actually in the transcript.

    The prompt asks for a verbatim quote per name; this checks it. A name whose
    quote cannot be found was inferred rather than heard, so it is demoted to a
    flag instead of reaching the spreadsheet as fact. Comparison is whitespace-
    and case-insensitive — the model reflows quotes, but it cannot invent words
    that aren't there.
    """
    haystack = " ".join(job["text"].lower().split())
    kept, rejected = [], []
    for person in payload.get("participants") or []:
        name = (person.get("name") or "").strip()
        quote = " ".join((person.get("evidence") or "").lower().split())
        if not name:
            continue
        if name.lower().split()[0] in OWNER_NAMES:
            continue  # the owner, not a participant — drop silently
        if quote and quote in haystack:
            kept.append(person)
        else:
            rejected.append(name)

    payload["participants"] = kept
    if rejected:
        payload.setdefault("flags", []).append(
            "Unverified name(s) removed from participants — quoted evidence was "
            "not found in the transcript: " + ", ".join(rejected)
        )
    if not kept:
        payload["participants_confidence"] = "none"
    payload["_participants_rejected"] = rejected
    return payload


def save(data: Path, job: dict, payload: dict) -> None:
    out = data / "enriched"
    out.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["_stem"] = job["stem"]
    payload["_hash"] = job["hash"]
    (out / f"{job['hash']}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def pending(data: Path, jobs: list) -> list:
    done = {p.stem for p in (data / "enriched").glob("*.json")}
    return [j for j in jobs if j["hash"] not in done]


def extract(message) -> dict | None:
    """Pull the JSON object out of a response, or None if the model declined."""
    if message.stop_reason == "refusal":  # check before touching content
        return None
    for block in message.content:
        if block.type == "text":
            return json.loads(block.text)
    return None


def cmd_sample(client, data: Path, jobs: list, count: int) -> int:
    """Enrich a spread of recordings synchronously and print them for review."""
    picks = [jobs[int(i * (len(jobs) - 1) / max(count - 1, 1))] for i in range(count)]
    for job in picks:
        print(f"\n{'='*72}\n{job['stem']}  ({len(job['text']):,} chars, "
              f"titled {job['title']!r})\n{'='*72}")
        try:
            message = client.messages.create(**request_params(job))
        except Exception as exc:
            print(f"  FAILED: {exc}")
            continue
        result = extract(message)
        if result is None:
            print(f"  declined by the model (stop_reason={message.stop_reason})")
            continue
        result = verify_participants(job, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        u = message.usage
        print(f"  [in {u.input_tokens:,} / out {u.output_tokens:,} tokens]")
    return 0


def cmd_submit(client, data: Path, jobs: list) -> int:
    todo = pending(data, jobs)
    if not todo:
        print("nothing to do — every recording is already enriched")
        return 0
    print(f"submitting {len(todo)} of {len(jobs)} recordings "
          f"({len(jobs) - len(todo)} already cached)")

    batch = client.messages.batches.create(requests=[
        {"custom_id": job["hash"], "params": request_params(job)} for job in todo
    ])
    (data / "last_batch_id.txt").write_text(batch.id, encoding="utf-8")
    print(f"batch id: {batch.id}   (saved to last_batch_id.txt)")
    print(f"status  : {batch.processing_status}")
    print(f"\ncollect with:  python enrich.py --collect {batch.id}")
    return 0


def cmd_collect(client, data: Path, jobs: list, batch_id: str) -> int:
    by_hash = {j["hash"]: j for j in jobs}
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        print(f"  {batch.processing_status}: {counts.succeeded} done, "
              f"{counts.processing} processing, {counts.errored} errored", flush=True)
        time.sleep(60)

    ok = declined = bad = errored = 0
    for result in client.messages.batches.results(batch_id):
        job = by_hash.get(result.custom_id)
        if job is None:
            continue
        if result.result.type != "succeeded":
            errored += 1
            print(f"  {result.result.type}: {job['stem']}")
            continue
        payload = extract(result.result.message)
        if payload is None:
            declined += 1
            print(f"  declined: {job['stem']}")
            continue
        try:
            save(data, job, verify_participants(job, payload))
            ok += 1
        except Exception as exc:
            bad += 1
            print(f"  unparseable: {job['stem']}: {exc}")

    print(f"\n--- collected ---\n  written {ok}\n  declined {declined}"
          f"\n  unparseable {bad}\n  errored {errored}")
    return 0


def cmd_reverify(data: Path, jobs: list) -> int:
    """Re-apply participant checks to already-collected results, no API calls.

    Lets a tightened rule be enforced against the whole corpus without paying to
    re-enrich it. Only the participant fields are touched.
    """
    by_hash = {j["hash"]: j for j in jobs}
    changed = 0
    for path in sorted((data / "enriched").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        job = by_hash.get(payload.get("_hash"))
        if job is None:
            continue
        before = [p.get("name") for p in payload.get("participants") or []]
        payload = verify_participants(job, payload)
        after = [p.get("name") for p in payload.get("participants") or []]
        if before != after:
            changed += 1
            dropped = [n for n in before if n not in after]
            print(f"  {payload['_stem']}: dropped {dropped}")
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nre-verified {len(list((data / 'enriched').glob('*.json')))} records, "
          f"{changed} changed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--sample", type=int, metavar="N")
    group.add_argument("--submit", action="store_true")
    group.add_argument("--collect", metavar="BATCH_ID")
    group.add_argument("--reverify", action="store_true",
                       help="re-run participant checks on cached results (no API calls)")
    args = ap.parse_args()

    jobs = load_work(args.data)
    print(f"{len(jobs)} recordings with transcripts")

    if args.reverify:
        return cmd_reverify(args.data, jobs)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in this process.")
        return 2

    client = anthropic.Anthropic()

    if args.sample:
        return cmd_sample(client, args.data, jobs, args.sample)
    if args.submit:
        return cmd_submit(client, args.data, jobs)
    return cmd_collect(client, args.data, jobs, args.collect)


if __name__ == "__main__":
    sys.exit(main())
