# Memo Machine — Build Plan

**Confirmed:** 2026-07-27 · **Detail level:** medium (default; not specified)
**Spec:** [voice-memo-archive-spec.md](voice-memo-archive-spec.md)

## Locked decisions

- Mix: mostly meetings/calls → category field stays; enrichment emphasizes participants + action items
- Extraction: libimobiledevice only
- Naming: participants segment in filenames (first two, then `-etal`; omitted for note-to-self)
- Spreadsheet: CSV source of truth + generated XLSX
- Participant inference: direct transcript evidence only, else `none`

## Modules

| # | Module | Description | Depends on | Status |
|---|--------|-------------|------------|--------|
| 1 | Toolchain setup | Install libimobiledevice on Windows; verify device pairing (`ideviceinfo`). Manual/one-time, no code. | — | **done** |
| 2 | Phase 0 gate | Pull one memo via backup, run `phase0_check_tsrp.py`. Go/no-go for the architecture. | 1 | **done — PASS** |
| 3 | Backup extractor | `idevicebackup2` wrapper + `Manifest.db` query → `originals/` + `CloudRecordings.db`, real filenames restored. Domain discovered at runtime; prompt on encrypted backup. | 2 | — |
| 4 | Metadata reader | `CloudRecordings.db` reader: `PRAGMA` introspection, Core Data epoch (+978307200), `date_source` fallback chain (db → m4a → mtime → unknown). | 3* | — |
| 5 | Transcript extractor | Atom walk per file → `transcripts/<hash>.txt`; no-payload files → `needs_attention`, never halts. Reuses Phase 0 parser (handles both `.m4a` and `.qta`). Also emit word timings as JSON for v2 search. | 3* | — |
| 6 | Enricher | One Claude call per transcript → `enriched/<hash>.json`; hash-keyed cache, chunk-with-overlap for long transcripts, retry-once on malformed JSON. | 5 | — |
| 7 | Output writer | Metadata + enrichment → `index.csv` → `index.xlsx`; then renamed copies into `archive/` (100-char cap, collision suffixes). Rename only rows where enrichment succeeded. | 4, 6 | — |
| 8 | Orchestrator CLI | Single entrypoint for stages 3–7; resumable hash-keyed state, fail-loud-continue logging. The quarterly re-run command. | 3–7 | — |

\* Modules 4–7 are testable in isolation on sample artifacts (one hand-exported memo + DB); they don't hard-block on device setup.

## Working agreement (per operating rules)

- One module at a time: header → implementation → checkpoint → stop for instruction
- Each module independently testable; no hidden state between modules
- Checkpoints report: independently functional? / limitations & TODOs / ready for integration?
- Originals immutable; everything hash-keyed and idempotent; fail loud, continue anyway

## Phase 0 results (2026-07-27) — PASS

Backup: 29,094 files / 64.99 GB. Voice Memos domain **discovered at runtime**:
`AppDomainGroup-group.com.apple.VoiceMemos.shared`. `ZCLOUDRECORDING` holds **243 rows**
matching the 242 the app displays.

### Corpus: 243 recordings, 131.7 hours, 32.34 GB audio

| Format | Count | Transcript | Empty | No payload |
|---|---|---|---|---|
| `.qta` (QuickTime) | 211 | **188** | 17 | 6 |
| `.m4a` (ISO-BMFF) | 32 | **23** | 6 | 3 |
| **Total** | **243** | **211 (87%)** | 23 | 9 |

**5,780,814 chars of transcript (~1.03M words) and 1,090,289 word timings — already on disk, zero transcription cost.**

### Two container formats, two transcript locations

iOS 26 records to `.qta` (QuickTime, brand `qt  `); older recordings are `.m4a`. They store
the transcript in completely different places:

```
.m4a   moov > trak > udta > tsrp                     → raw JSON payload
.qta   moov > trak > meta > keys  → mdta "com.apple.VoiceMemos.tsrp" (key #N)
                          > ilst  → item #N > data   → same JSON payload
```

Payload schema (both formats):

```json
{"locale": {...},
 "attributedString": {"runs": ["And", 0, " probably", 1, ...],
                      "attributeTable": [{"timeRange": [0, 1.98]}, ...]}}
```

`runs` alternates text token / index into `attributeTable` → **word-level timestamps**.
Richer than the spec assumed; enables jump-to-position in a v2 search.

### Three traps this cost us — all now handled in `phase0_check_tsrp.py`

1. **Filtering on `.m4a` finds 13% of the corpus.** The 211 `.qta` files are the bulk of it. Match on both extensions, or better, drive off `ZCLOUDRECORDING.ZPATH`.
2. **QuickTime `meta` is a plain container; ISO-BMFF `meta` is a full box** with 4 leading version/flags bytes. Skipping those 4 bytes unconditionally desyncs the parse on every `.qta` and the transcript becomes invisible. `meta_children_offset()` detects the variant per box.
3. **A file has more than one `meta` box.** The transcript's lives under `trak`; a small decorative one lives under `udta`. Collecting `keys`/`ilst` globally lets the trailing `udta` ilst win and yields nothing — they must be paired within the same `meta`.

Plus: an untranscribed recording still carries a `tsrp` payload with `attributedString: ""`.
Presence is *not* evidence of a transcript — test for non-empty text.

**Performance:** metadata lives in a ~2 MB `moov` box; audio is up to 276 MB per file.
`load_metadata_region()` seeks to `moov` instead of slurping the file — full 243-file scan
runs in **0.9 s** rather than reading 32 GB.

### Untranscribed gap: 32 files / 4.32 hours — but only 4 matter

24 of the 32 are under 60 seconds (mostly zero-byte or aborted taps). The real gap is four recordings:

| File | Duration |
|---|---|
| `20251115 173129.m4a` | 87m 22s |
| `20260112 074811-74C137FF.qta` | 86m 06s |
| `20260617 120121.m4a` | 41m 13s |
| `20260114 183724-4EBA4DD8.qta` | 33m 32s |

Remedy: open each in the Voice Memos app to trigger on-device transcription, then re-run the
backup. Local Whisper on just these four is the fallback.

### Bearing on Module 6

5.78M chars ≈ 1.5M input tokens for enrichment across 211 recordings — a real cost, not the
rounding error a 32-file corpus implied. Chunking strategy and model choice both matter; longest
single transcript is ~49k chars.

## Environment

- libimobiledevice v1.2.1-r1122 (win-x64) → `C:\Users\ezras\tools\libimobiledevice`, on user PATH
- Apple Mobile Device Service: running
- Python 3.13.3

## Non-negotiables (from spec §4)

Originals never modified · idempotent re-runs · one bad file never kills a run · confidence markers on inferred fields · nothing deleted from the phone by this tool.
