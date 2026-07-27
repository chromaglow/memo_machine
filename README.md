# Memo Machine

A pipeline that turns a phone full of iPhone voice memos into a durable, searchable archive — so the phone can be wiped clean.

## What it produces

1. **`archive/`** — renamed copies of every recording, named so the filename says what it is:
   `2026-03-14_1430_meeting_roadmap-sync_josh-aaron.m4a`
2. **`index.csv` / `index.xlsx`** — one row per recording: date, duration, category, title, topic, summary, participants, action items. The spreadsheet is the real product; the renamed files exist so it can point at something findable.

## How it works

The architectural unlock: **Apple's on-device transcription embeds the transcript inside the `.m4a` file itself**, in an MPEG-4 atom named `tsrp`. No Whisper, no transcription API, no audio ever uploaded — transcription is a file read. A SQLite database (`CloudRecordings.db`) that ships alongside the recordings supplies true creation dates, titles, and durations.

Pipeline stages, each with intermediate artifacts on disk so any stage re-runs independently:

| Phase | What | Output |
|---|---|---|
| 0 | Verify the `tsrp` atom survives the transfer off the phone | go / no-go |
| 1 | Extract files from a device backup via libimobiledevice | `originals/` + `CloudRecordings.db` |
| 2 | Read metadata from `CloudRecordings.db` | dates, titles, durations |
| 3 | Pull transcripts from the `tsrp` atoms | `transcripts/<hash>.txt` |
| 4 | Enrich each transcript with one Claude API call | `enriched/<hash>.json` |
| 5 | Write the spreadsheet, then rename copies into `archive/` | `index.csv`, `index.xlsx`, `archive/` |

## Ground rules

- **Originals are immutable** — renaming produces copies, never touches `originals/`
- **Idempotent** — everything keyed on file hash; re-runs never duplicate work or re-spend API calls
- **Fail loud, continue anyway** — one corrupt file never kills a run
- **Inferred fields carry confidence markers** — DB dates are facts, guessed participants are not, and the spreadsheet shows the difference
- **Nothing is ever deleted from the phone** — wiping is a separate, manual, deliberate act after the archive is verified

## Status

Phase 0. See [voice-memo-archive-spec.md](voice-memo-archive-spec.md) for the full build spec.

### Decisions (locked 2026-07-27)

- **Mix:** mostly meetings/calls → category field stays; enrichment emphasizes participants and action items
- **Extraction:** libimobiledevice only (scriptable from day one, no iMazing)
- **Naming:** participants segment included in filenames (first two names, then `-etal`; omitted for notes-to-self)
- **Spreadsheet:** CSV as source of truth, XLSX generated from it
- **Participant inference:** conservative — a name goes in only on direct transcript evidence (spoken address, self-intro, sign-off); otherwise `none`, never a plausible guess

## Phase 0 — verify the transcript survives

```
python phase0_check_tsrp.py path\to\memo.m4a
```

Walks the MPEG-4 atom tree, reports whether a `tsrp` atom is present, and prints the first 200 characters of extractable transcript text.

- **Pass:** transcript comes out → proceed to Phase 1.
- **Fail:** the transfer re-encoded the file and stripped the atom → change the transfer method, not the architecture. Backup-based extraction copies byte-for-byte and is most likely to preserve it. (A missing atom on a pre-iOS 18 recording, or one never opened in the Voice Memos app, is a per-file issue, not an architecture failure.)

## Requirements

- Windows (primary); pipeline portable to Linux
- Python 3.11+
- [libimobiledevice](https://libimobiledevice.org/) (`idevicebackup2`) for extraction
- `anthropic` SDK for enrichment, `openpyxl` for the XLSX view
