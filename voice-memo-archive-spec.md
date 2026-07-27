# Voice Memo Archive — Build Spec

**Owner:** Ezra
**Target platform:** Windows (primary), with the pipeline portable to Linux
**Status:** Ready to build, pending Phase 0 verification
**Last updated:** 2026-07-27

---

## How to use this document

This spec is written for Claude Code. Read it end to end, then **start a conversation before writing code**. Section 3 lists open decisions that need Ezra's input. Resolve those first, confirm the direction, then execute Phase 0 as the gate before building anything else.

Do not skip Phase 0. The entire architecture rests on one unverified assumption, and Phase 0 costs five minutes to prove or kill it.

---

## 1. What this is

A pile of voice recordings currently lives on an iPhone. They need to come off the phone, get organized, and become a durable searchable archive so the phone can be wiped and start clean.

Two deliverables:

1. **A folder of renamed audio files** — names that actually say what the recording is
2. **A spreadsheet index** — one row per recording: date, participants, topic, summary, action items

The spreadsheet is the real product. The renamed files exist so that when the spreadsheet points at something, it can be found.

This is a one-time bulk migration that should also work as a repeatable quarterly habit.

---

## 2. The architectural unlock

**Apple's on-device transcription writes the transcript into the `.m4a` file itself, in an MPEG-4 container atom named `tsrp`.**

This is the load-bearing fact. It means:

- No Whisper, no transcription API, no per-minute cost
- No audio ever gets processed or uploaded
- Transcription time is effectively zero — it's a file read
- Transcripts are already speaker-punctuated and reasonably clean

There is also a SQLite database, `CloudRecordings.db`, that ships alongside the recordings and carries true creation dates, any custom titles, durations, folder assignments, and a signal for which recordings have an embedded transcript.

### Prior art to lean on

Do not write the `tsrp` parser from scratch. These exist and are permissively licensed:

- `uasi/extract-apple-voice-memos-transcript` — the original extractor (0BSD)
- `jessedc/apple-voice-memos` — packaged version with metadata querying (0BSD)
- `fdelorenzi/voice-memo-forensics` — useful reference for `CloudRecordings.db` schema and m4a/db cross-validation
- Thomas Countz, "Unlocking Apple Voice Memo Transcripts" (2025-06-08) — background on the atom format

Read these first. Vendor or adapt rather than reimplement.

---

## 3. Open decisions — resolve these in conversation first

**3.1 — Volume and mix.** Unanswered. How many files, roughly how long, and what's the split between meetings/calls and solo notes-to-self? This determines whether the `category` field earns its place and how the summarizer gets prompted. Ask before building the enrichment step.

**3.2 — Extraction method.** Two viable paths, described in Phase 1. Recommendation is iMazing for the first bulk dump and libimobiledevice for the recurring version. Confirm before building.

**3.3 — Naming convention.** A proposal is in Section 6 with one alternative. Get an explicit yes on the exact format string before renaming anything.

**3.4 — Spreadsheet format.** CSV or XLSX. CSV is simpler and diffable; XLSX supports column widths, frozen headers, and data validation on the confidence fields. Recommendation is to write both — CSV as the source of truth, XLSX generated from it for reading.

**3.5 — Participant inference aggressiveness.** How hard should the model guess at names from transcript content? See Section 7.

---

## 4. Non-negotiables

These are design constraints, not preferences.

- **Originals are immutable.** Extracted files land in `originals/` and are never renamed, moved, or modified. Renaming produces *copies* in `archive/`. Disk space is cheap; a mangled one-of-one archive is not.
- **Idempotent and resumable.** Re-running the pipeline on the same input must not duplicate rows, re-process completed files, or re-spend API calls. Key everything on a stable file hash.
- **Fail loud, continue anyway.** One corrupt file must not kill a 200-file run. Log the failure, mark the row, keep going.
- **Every inferred field carries a confidence marker.** Dates from the database are facts. Participants guessed from transcript content are not. The spreadsheet must make that distinction visible so review effort goes where it's needed.
- **Nothing gets deleted from the phone by this tool.** Wiping the phone is a separate, manual, deliberate act taken after the archive is verified.

---

## 5. Pipeline

### Phase 0 — Verification gate (do this first)

**Goal:** prove the `tsrp` atom survives the transfer path.

1. Ezra moves **one** voice memo off the phone by whatever means is easiest
2. Write a minimal script that opens the `.m4a`, walks the MPEG-4 atom tree, and reports whether a `tsrp` atom is present and whether text can be extracted from it
3. Print the first 200 characters of the extracted transcript

**Pass:** transcript comes out. Proceed to Phase 1.

**Fail:** the transfer method re-encoded the file and stripped the atom. Do not redesign the architecture — change the transfer method and retest. Backup-based extraction (Phase 1) copies files byte-for-byte and is the method most likely to preserve the atom. If the atom is absent because the recording predates iOS 18 or was never opened in the app, that's a per-file issue, not an architecture issue — see Section 8.

### Phase 1 — Get files off the phone

Voice Memos are not exposed over USB the way photos are. Since iOS 15 they are reachable only through a device backup. Both paths below produce the same artifacts: a set of `.m4a` files plus `CloudRecordings.db`.

**Path A — iMazing (recommended for the first bulk dump)**

Commercial Windows app. Connect device, choose Data Access Only backup, open the Voice Memos dataset, select all, export to a folder. Manual, ~20 minutes, no code required. Verify that `CloudRecordings.db` comes along; if iMazing exports only audio, the database can be retrieved separately via Path B or the pipeline can fall back to embedded m4a metadata.

**Path B — libimobiledevice (recommended for the recurring version)**

Free, scriptable, runs on Windows.

1. `idevicebackup2 backup --full <dest>` to produce a local backup
2. Open `Manifest.db` in the backup root — it maps hashed filenames to real domains and paths
3. Query for the Voice Memos domain. **Do not hardcode the domain string** — discover it at runtime by searching `Manifest.db` for paths matching `Recordings` and file extensions `.m4a`, and log what domain they turn up under
4. Copy matched files out to `originals/`, restoring their real filenames from the manifest
5. Copy `CloudRecordings.db` out alongside them

Encrypted backups require the password to decrypt. If Ezra has backup encryption enabled, prompt for it rather than silently failing.

### Phase 2 — Read metadata

Open `CloudRecordings.db` read-only. Primary table is `ZCLOUDRECORDING`.

Fields of interest:

| Column | Meaning |
|---|---|
| `ZPATH` | Filename on disk — join key to the `.m4a` files |
| `ZCUSTOMLABEL` | Title, if one was ever set |
| `ZENCRYPTEDTITLE` | Alternate title field on newer iOS versions |
| `ZDATE` | Creation timestamp |
| `ZDURATION` | Length in seconds |

**Critical:** `ZDATE` is a Core Data timestamp — seconds since 2001-01-01 00:00:00 UTC, not Unix epoch. Add 978307200 to convert. Getting this wrong shifts every date in the archive by 31 years, and it will not be obvious from a spot check if you only look at the time portion.

Schema varies across iOS versions. Introspect the table with `PRAGMA table_info` before selecting columns, and degrade gracefully when a column is missing rather than crashing.

**Date resolution order:** `ZDATE` from the database → embedded m4a creation atom → filesystem mtime → unknown. Record which source was used in the `date_source` column, because cloud and backup transfers routinely destroy mtime and that field is how you'll know which dates to trust.

### Phase 3 — Extract transcripts

For each `.m4a`, walk the atom tree and pull `tsrp`. Emit plain text plus timestamps where available.

Write each transcript to `transcripts/<hash>.txt`. This is deliberate: transcripts are the expensive-to-regenerate artifact and should exist on disk independently of the spreadsheet, so re-running enrichment never requires touching the audio again.

Files with no `tsrp` atom go to a `needs_attention` list and continue through the pipeline with empty transcript fields. Do not halt.

### Phase 4 — Enrich

For each transcript, one Claude API call returning structured JSON. Prompt must specify JSON-only output, no preamble, no markdown fences, and must be parsed defensively.

Requested fields:

```json
{
  "title": "short human-readable title, max 60 chars",
  "slug": "lowercase-hyphenated-filename-safe, max 40 chars",
  "category": "meeting | call | note-to-self | idea | other",
  "topic": "one sentence on what this is about",
  "summary": "2-4 sentences",
  "participants": ["names heard or inferred"],
  "participants_confidence": "high | medium | low | none",
  "action_items": ["..."],
  "flags": ["anything odd worth a human look"]
}
```

Batch this. Cache by file hash so a re-run costs nothing for already-enriched files.

Long transcripts may exceed a comfortable single-call size. Chunk with overlap and ask for a consolidated pass over the chunk summaries rather than truncating.

### Phase 5 — Output

Write `archive/` containing renamed copies, and `index.csv` (+ `index.xlsx`) at the root.

Renaming happens **after** the spreadsheet is written, and only for rows where enrichment succeeded. Rows that failed keep their original filenames and are flagged in the sheet for manual handling.

---

## 6. Naming convention

**Proposed:**

```
YYYY-MM-DD_HHMM_category_slug_participants.m4a
2026-03-14_1430_qed_roadmap-sync_josh-aaron.m4a
```

Rules:

- Date first so the folder sorts chronologically with no tooling
- Time included so same-day recordings never collide
- Everything after the date is lowercase with hyphens — no case-sensitivity surprises if this archive ever moves to the Linux machine
- Participants segment is omitted entirely when the category is `note-to-self`
- Participants truncated to the first two names, then `-etal`
- **Total filename capped at 100 characters.** Windows path limits will bite otherwise, especially once this sits inside a nested cloud-sync folder
- Collisions get `_2`, `_3` suffixes appended before the extension

**Alternative under consideration:** drop the participants segment entirely and let the spreadsheet carry it. Cleaner names, but loses the ability to eyeball "everything with Josh in it" in a file listing. Get an explicit decision before implementing.

---

## 7. Spreadsheet schema

One row per recording. Suggested column order:

| Column | Source | Notes |
|---|---|---|
| `date` | DB | ISO 8601 |
| `time` | DB | 24h |
| `date_source` | derived | `db` / `m4a` / `mtime` / `unknown` |
| `duration` | DB | mm:ss |
| `category` | Claude | |
| `title` | Claude | |
| `topic` | Claude | one sentence |
| `summary` | Claude | 2-4 sentences |
| `participants` | Claude | comma-separated |
| `participants_confidence` | Claude | `high` / `medium` / `low` / `none` |
| `action_items` | Claude | newline-separated |
| `original_filename` | filesystem | |
| `archive_filename` | derived | |
| `has_transcript` | derived | boolean |
| `flags` | Claude + pipeline | anything needing a human |
| `file_hash` | derived | idempotency key |

Sort the default view by date descending. Freeze the header row in the XLSX.

### On participants specifically

Transcription gives words, not identities. Names only appear when someone says one out loud. The realistic accuracy ceiling here is "usually right on named participants, blank otherwise," and the design should be honest about that rather than manufacturing plausible-sounding guesses.

Instruct the model to name someone **only** when the transcript contains direct evidence — a name spoken in address, a self-introduction, a sign-off. Everything else returns `none`, not a guess. A blank cell Ezra fills in himself is more useful than a confident wrong name he has to catch.

---

## 8. Failure modes and handling

| Failure | Handling |
|---|---|
| No `tsrp` atom (pre-iOS 18, never opened in app) | Flag in `needs_attention`. Two remedies: open the recording once on the phone to trigger on-device transcription and re-export, or run local Whisper on that subset only. Do not build Whisper into the main path — it's a fallback for a minority. |
| No speech in recording (ambient audio, music idea) | Category `other`, empty transcript, flagged. Expected and fine. |
| Corrupt or truncated m4a | Log, flag, skip, continue |
| `CloudRecordings.db` missing entirely | Degrade to m4a-embedded metadata and mtime. Pipeline still runs, `date_source` reflects the downgrade. |
| Claude returns malformed JSON | Retry once with a stricter reprompt, then flag and continue with empty enrichment fields |
| Filename collision after renaming | Numeric suffix |
| Backup is encrypted | Prompt for password rather than failing silently |

---

## 9. Tech stack

- **Python 3.11+**, standard library where possible
- `sqlite3` (stdlib) for `CloudRecordings.db` and `Manifest.db`
- Custom or vendored atom parser for `tsrp` — no external dependency needed
- `anthropic` SDK for enrichment
- `openpyxl` for the XLSX view
- Optional: `ffprobe` for embedded m4a metadata fallback

Deliberately no GUI. A CSV opened in Excel or LibreOffice is a better review interface than anything worth building here, and it's where Ezra will be correcting participant names anyway.

Structure as discrete stages with intermediate artifacts on disk (`originals/`, `transcripts/`, `enriched/`, `archive/`) so any stage can be re-run independently.

---

## 10. Out of scope for v1

- Speaker diarization
- Semantic search across transcripts
- Any phone-side deletion or cleanup
- Continuous or automatic sync
- Web UI

If the archive proves useful, semantic search over `transcripts/` is the obvious v2 and the directory structure above is already set up for it.

---

## 11. First action

Phase 0. One file, one script, one question answered: does the transcript survive the trip off the phone.

Everything downstream is straightforward once that's green.
