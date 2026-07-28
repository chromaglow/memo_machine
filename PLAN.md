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
| 5 | Transcript extractor | Atom walk per file → `transcripts/<hash>.txt`; no-atom files → `needs_attention`, never halts. Reuses Phase 0 parser. | 3* | — |
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
`AppDomainGroup-group.com.apple.VoiceMemos.shared` (32 `.m4a`, plus `Recordings/CloudRecordings.db`).

| Outcome | Count | Notes |
|---|---|---|
| Usable transcript | **23** | 259,533 chars total, free of charge |
| `tsrp` present but empty | 6 | never transcribed on-device |
| No `tsrp` atom | 3 | zero-byte files, unrecoverable |

Corpus spans 2025-10-27 → 2026-06-26. `ZCLOUDRECORDING` confirmed to carry `ZPATH`/`ZDATE`/`ZDURATION`.

### `tsrp` payload schema (verified, not assumed)

```json
{"locale": {...},
 "attributedString": {"runs": ["And", 0, " probably", 1, ...],
                      "attributeTable": [{"timeRange": [0, 1.98]}, ...]}}
```

`runs` alternates text token / index into `attributeTable` → **word-level timestamps** (1,382 on a 7,010-char sample). Richer than the spec assumed; enables jump-to-position in a v2 search.

**Trap:** an untranscribed recording still gets a `tsrp` atom with `attributedString: ""`. Atom presence is *not* evidence of a transcript — check for non-empty text. `phase0_check_tsrp.py` initially got this wrong and now reports PARTIAL for this case.

### Untranscribed gap — recoverable by opening each in the Voice Memos app, then re-backing-up

| File | Duration | Worth recovering? |
|---|---|---|
| `20251115 173129.m4a` | **87m 22s** | yes — longest in corpus |
| `20260617 120121.m4a` | **41m 13s** | yes |
| `20260414 090828.m4a` (+ dup) | 1m 05s | marginal |
| `20251103 120035.m4a` | 0m 23s | no |
| `20251216 082437.m4a` | 0m 00s | no |

## Environment

- libimobiledevice v1.2.1-r1122 (win-x64) → `C:\Users\ezras\tools\libimobiledevice`, on user PATH
- Apple Mobile Device Service: running
- Python 3.13.3

## Non-negotiables (from spec §4)

Originals never modified · idempotent re-runs · one bad file never kills a run · confidence markers on inferred fields · nothing deleted from the phone by this tool.
