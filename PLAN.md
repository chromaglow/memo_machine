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
| 1 | Toolchain setup | Install libimobiledevice on Windows; verify device pairing (`ideviceinfo`). Manual/one-time, no code. | — | in progress |
| 2 | Phase 0 gate | Pull one memo via backup, run `phase0_check_tsrp.py`. Go/no-go for the architecture. | 1 | script ready |
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

## Non-negotiables (from spec §4)

Originals never modified · idempotent re-runs · one bad file never kills a run · confidence markers on inferred fields · nothing deleted from the phone by this tool.
