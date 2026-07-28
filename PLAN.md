# Memo Machine — Build Plan

**Confirmed:** 2026-07-27 · **Completed:** 2026-07-28 · **Detail level:** medium
**Spec:** [voice-memo-archive-spec.md](voice-memo-archive-spec.md)

> **All modules complete.** This document is kept as the original plan with
> outcomes recorded against it — including where the plan was wrong. For what
> was built and why, see [README.md](README.md); for the chronological record
> and the corrections, [CHANGELOG.md](CHANGELOG.md); for operating it,
> [HANDOFF.md](HANDOFF.md).
>
> Two planned modules were **not** built, deliberately:
> - **Module 4** was absorbed into Module 3 rather than written separately — the
>   metadata read is a dozen lines and splitting it would have been ceremony.
> - **Module 8** (orchestrator CLI) was dropped. Each stage is already idempotent
>   and hash-keyed, so the "orchestrator" is eight lines of shell in HANDOFF.md.
>   A wrapper would have added a layer without removing a decision.
>
> Three modules were added that the plan did not anticipate: the Whisper
> fallback, topic sorting (`classify_topic.py`), and the HTML browser
> (`build_browser.py`).

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
| 3 | Backup extractor | `idevicebackup2` wrapper + `Manifest.db` query → `originals/` + `CloudRecordings.db`, real filenames restored. Domain discovered at runtime; prompt on encrypted backup. | 2 | **done — 243/243 verified** |
| 4 | Metadata reader | `CloudRecordings.db` reader: `PRAGMA` introspection, Core Data epoch (+978307200), `date_source` fallback chain (db → m4a → mtime → unknown). | 3* | **folded into Module 3** |
| 5 | Transcript extractor | Atom walk per file → `transcripts/<stem>.txt` + `.words.json`; falls back to whisper output; no-payload files → `needs_attention`, never halts. | 3* | **done — 213 transcripts** |
| 6 | Enricher | One Claude call per transcript → `enriched/<hash>.json`; hash-keyed cache, chunk-with-overlap for long transcripts, retry-once on malformed JSON. | 5 | **done — 213/213, $7.55** |
| 7 | Output writer | Metadata + enrichment → `index.csv` → `index.xlsx`; then renamed copies into `archive/` (100-char cap, collision suffixes). Rename only rows where enrichment succeeded. | 4, 6 | **done — 243 rows, 213 renamed** |
| 8 | Orchestrator CLI | Single entrypoint for stages 3–7; resumable hash-keyed state, fail-loud-continue logging. The quarterly re-run command. | 3–7 | **dropped — see note above** |
| + | Whisper fallback | Local transcription for recordings Apple never processed. Not in the original plan. | 5 | **done — 2 recordings** |
| + | Topic sorting | `classify_topic.py` — sorts the archive into subfolders by subject. Not in the original plan. | 6 | **done — 3 folders** |
| + | HTML browser | `build_browser.py` — every recording and transcript in one searchable page. Not in the original plan. | 7 | **done — browse.html** |

\* Modules 4–7 are testable in isolation on sample artifacts (one hand-exported memo + DB); they don't hard-block on device setup.

### Where the plan was wrong

- **Chunking was specified and turned out to be unnecessary.** The longest transcript is ~24k tokens against a 1M-token context window. Dropping it removed the most complex part of Module 6 and the accuracy loss that comes with stitching chunk summaries.
- **The corpus was assumed to be `.m4a`.** 211 of 243 recordings are `.qta`, and the two formats store transcripts in entirely different places.
- **`ZCUSTOMLABEL` was assumed to be the title.** It holds a UTC ISO timestamp; the title is in `ZENCRYPTEDTITLE`.
- **A confidence marker was assumed to be enough for participants.** It was not — the rule needed a verbatim quote, a programmatic check of that quote, and a deterministic owner filter.

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

## Whisper fallback (2026-07-27) — done, 2 files

On-device transcription could not be triggered for these, so `whisper_fallback.py` ran
large-v3 locally on the RTX 4080 SUPER. Deliberately outside the main pipeline, per spec §8.
Everything needed was already installed (ffmpeg, faster-whisper, CUDA torch, cached model).

| Title | File | Audio | Result |
|---|---|---|---|
| Josh 2 | `20260617 120121.m4a` | 41m 13s | 31,967 chars / 6,095 words |
| N 73rd St 13 copy | `20260112 074811-74C137FF.qta` | 86m 06s | 11,822 chars / 2,427 words |

Yojin (87m) and China meetings (33m) dropped by Ezra's decision. **Final coverage: 213 / 243.**

### Tuning that mattered — all measured, not assumed

The 86-minute file is a room recording: **67% silence, -42.7 dB mean**. First pass produced
5,136 chars (~12 wpm) and 17% of it was fabricated. Three separate problems:

1. **VAD + raw levels lost real speech.** A/B on one 5-minute slice: VAD+raw 1,890 chars,
   no-VAD+raw 1,858, VAD+loudnorm 1,739, **no-VAD+loudnorm 2,576**. Only the combination
   helps — either change alone does nothing or hurts.
2. **`condition_on_previous_text` fed hallucinations back as context**, turning one invented
   "Thank you." into 101 of them, plus 15 of "Thank you for watching." (a training artifact
   never spoken in the recording).
3. **The standard confidence filter is aimed the wrong way.** Dumping 404 segments with their
   statistics showed:

   | statistic | hallucination median | real median | verdict |
   |---|---|---|---|
   | `compression_ratio` | 0.56 | 1.48 | strong discriminator |
   | `no_speech_prob` | 0.89 | 0.79 | weak; useful as a gate |
   | `avg_logprob` | -0.61 | -0.49 | useless, fully overlapping |

   These hallucinations are *short* stock phrases, so they compress poorly and score **low** —
   the usual `compression_ratio > 2.4` rule (aimed at long repetitive loops) catches none of
   them. Filtering `cr < 0.7 AND nsp > 0.8` removes 81% of hallucinated segments for a 3% cost
   in real ones. Filtering on `avg_logprob` was destroying genuine quiet speech: it cut output
   back to 5,269 chars.

Net: **5,136 → 11,822 chars, and hallucinated "Thank you." went from 101 to 3.**

Lesson for Module 6: quiet, sparse recordings exist in this corpus and their transcripts are
thin. Enrichment must tolerate a 12k-char transcript covering 86 minutes without inventing
substance to fill the summary.

### Untranscribed gap: 32 files / 4.32 hours — 2 recovered above, 2 dropped, rest are noise

24 of the 32 are under 60 seconds (mostly zero-byte or aborted taps). The real gap is four recordings:

24 of the 32 are under 60 seconds — aborted taps and zero-byte files, not worth recovering.
The four substantial ones:

| File | Title | Duration | Disposition |
|---|---|---|---|
| `20251115 173129.m4a` | Yojin | 87m 22s | dropped |
| `20260112 074811-74C137FF.qta` | N 73rd St 13 copy | 86m 06s | **Whisper** |
| `20260617 120121.m4a` | Josh 2 | 41m 13s | **Whisper** |
| `20260114 183724-4EBA4DD8.qta` | China meetings  copy | 33m 32s | dropped |

### Bearing on Module 6

5.78M chars ≈ 1.5M input tokens for enrichment across 211 recordings — a real cost, not the
rounding error a 32-file corpus implied. Chunking strategy and model choice both matter; longest
single transcript is ~49k chars.

## Modules 3 & 5 results (2026-07-27) — archive complete and verified

`extract_recordings.py` → `extract_transcripts.py` → `build_library.py`.

| Check | Result |
|---|---|
| Recordings in phone DB | 243 |
| Extracted and SHA-256 verified | **243 / 243**, 0 failures |
| Missing from archive | **none** |
| Decodable by ffprobe, duration matching DB | **243 / 243** |
| Independent re-hash from `library/` | 6 / 6 sampled OK |
| Transcripts | **213** (211 Apple-embedded, 2 Whisper) |
| Byte-identical duplicate recordings | 0 |

`library/` holds all 243 recordings hard-linked beside their 213 transcripts, plus
`_index.csv` and `_README.txt`. Hard links mean it occupies **no additional disk** —
`library/` and `originals/` are the same bytes under two names.

Without a transcript: 30 recordings, 131.7 min total — 24 are under 60 seconds
(aborted taps, near-empty files); the rest are Yojin (87m) and China meetings (33m),
both dropped by Ezra's decision.

### Disk

| Path | Size | Note |
|---|---|---|
| `backup/` | 64.99 GB | **deletable** once the archive is verified — it is |
| `originals/` | 30.12 GiB | the keeper: independent verified copy |
| `library/` | 30.13 GiB apparent | hard links, costs nothing extra |
| `transcripts/` | 0.03 GB | |

Deleting `backup/` frees ~65 GB. Deleting the recordings from the phone frees ~30 GB there.
Per spec §4, this tool never deletes from the phone — that stays a manual, deliberate act.

## Environment

- libimobiledevice v1.2.1-r1122 (win-x64) → `C:\Users\ezras\tools\libimobiledevice`, on user PATH
- Apple Mobile Device Service: running
- Python 3.13.3
- Data root: `C:\Users\ezras\memo-machine-data\` — **outside OneDrive** so 65 GB of personal
  audio never syncs to the cloud. Code lives in OneDrive; data does not.
- Already present, no installs needed: ffmpeg, faster-whisper 1.2.1 (large-v3 cached),
  torch 2.6.0+cu124, RTX 4080 SUPER 16 GB, `anthropic` 0.96.0, `openpyxl` 3.1.5
- Backup: `backup\00008150-000E14480204401C\` — 29,094 files / 64.99 GB, `Backup Successful`

## Non-negotiables (from spec §4)

Originals never modified · idempotent re-runs · one bad file never kills a run · confidence markers on inferred fields · nothing deleted from the phone by this tool.
