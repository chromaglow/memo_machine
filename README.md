# Memo Machine

Turns a phone full of iPhone voice memos into a durable, searchable archive — so the phone can be wiped clean.

**Status: complete.** 243 recordings, 131.7 hours, extracted, transcribed, enriched, renamed and sorted.

| | |
|---|---|
| Recordings | 243 (32.34 GB) |
| Transcripts | 213 — 211 free from the audio files, 2 via local Whisper |
| Enrichment cost | **$7.55** total |
| Verification | every file SHA-256 checked against the phone backup |

**Further reading:** [CHANGELOG.md](CHANGELOG.md) for the chronological record and the corrections along the way · [HANDOFF.md](HANDOFF.md) for operating it, the traps, and the quarterly repeat · [PLAN.md](PLAN.md) for the original plan with outcomes recorded against it · [voice-memo-archive-spec.md](voice-memo-archive-spec.md) for the spec this started from.

---

## The architectural unlock

**Apple's Voice Memos app writes its on-device transcript into the `.m4a`/`.qta` file itself.** No Whisper, no transcription API, no per-minute cost, and the audio never leaves the machine. 5.8M characters of transcript came out of the files in **0.9 seconds**.

The payload is JSON carrying word-level timestamps:

```json
{"locale": {...},
 "attributedString": {"runs": ["And", 0, " probably", 1, ...],
                      "attributeTable": [{"timeRange": [0, 1.98]}, ...]}}
```

`runs` alternates a text token with an index into `attributeTable` — 1.1M word timings across the corpus, enough to build jump-to-position search later.

### Three traps that cost real time

1. **iOS 26 records to `.qta`, not `.m4a`.** Filtering on `.m4a` finds 13% of a modern library. The 211 `.qta` files were the bulk of it.
2. **The two formats store the transcript in completely different places.**
   ```
   .m4a   moov > trak > udta > tsrp                     → raw JSON
   .qta   moov > trak > meta > keys → "com.apple.VoiceMemos.tsrp"
                             > ilst → item N > data     → same JSON
   ```
3. **QuickTime `meta` is a plain container; ISO-BMFF `meta` is a full box** with 4 leading version/flags bytes. Skipping those unconditionally desyncs every `.qta` parse and the transcript vanishes silently.

Two more worth knowing: a file has **more than one `meta` box** (the real one under `trak`, a decorative one under `udta`) so `keys`/`ilst` must be paired within the same box; and an untranscribed recording still carries a `tsrp` payload with an empty string — **atom presence is not evidence of a transcript**.

Metadata lives in a ~2 MB `moov` box while audio runs to 276 MB per file, so the parser seeks to `moov` instead of reading whole files. Full 243-file scan: **0.9 s** instead of 32 GB of I/O.

---

## Pipeline

| Script | What it does |
|---|---|
| `phase0_check_tsrp.py` | Verifies a file carries a real transcript. The go/no-go gate. |
| `extract_recordings.py` | Pulls recordings out of an `idevicebackup2` backup, restoring real filenames from `Manifest.db`. Hashes both sides before reporting success. |
| `extract_transcripts.py` | Writes one transcript per recording from whichever source has it. |
| `whisper_fallback.py` | Local Whisper for recordings Apple never transcribed. Deliberately outside the main path. |
| `build_library.py` | Assembles audio beside transcript in one browsable folder, hard-linked so it costs no extra disk. |
| `enrich.py` | One Claude call per transcript → structured metadata. Batch API, hash-keyed cache. |
| `build_index.py` | Builds `index.csv` / `index.xlsx` and renames the archive. |
| `classify_topic.py` | Sorts recordings into topic subfolders from their summaries. |
| `build_browser.py` | Builds `browse.html` — every recording and transcript in one searchable page. |

Every stage is idempotent and keyed on the audio file's SHA-256, so re-running never duplicates work or re-spends on API calls.

---

## Ground rules

- **Originals are immutable.** `originals/` holds the byte-for-byte copies and is never renamed or moved. Everything else is hard links.
- **Fail loud, continue anyway.** One corrupt file never kills a run.
- **Nothing is ever deleted from the phone by this tool.**
- **Inferred fields carry confidence markers** — and the participants rule below has teeth, not just a label.

---

## The participants rule

Transcription gives words, not identities. This is the field most likely to be quietly wrong, so it is enforced mechanically rather than requested politely.

A first pass listed five names for one recording at "low" confidence — including the archive owner, a third party never on the call, and a name the model itself flagged as a probable mistranscription. Hedging with a confidence field doesn't help; the name still reaches the spreadsheet as something to catch.

Three layers now stand between a guess and the spreadsheet:

1. **Every name must carry a verbatim quote** proving the person was present — addressed by name, self-introducing, or signing off. The model cannot fill the field without producing a quote.
2. **`verify_participants()` checks each quote against the transcript.** Four names were dropped because their evidence could not be found.
3. **The owner is filtered deterministically.** People address him by name constantly, so his quotes are genuine and pass step 2 — the rule has to live in code.

Result across 213 recordings: **114 with evidenced names, 101 correctly returning `none`.** A blank cell means nobody could be evidenced, not that nobody was there.

---

## Output

```
C:\Users\ezras\memo-machine-data\
├── library\              audio + transcript side by side, sorted by topic
│   ├── amazon\           82   Amazon employment
│   ├── qed\              66   QED venture — Josh, Aaron, Hard Shell
│   ├── techland\         20   Techland / Geode — Charlie
│   └── (root)            75   everything else
├── originals\           243   immutable, phone filenames
├── transcripts\         213   text + word timings
├── enriched\            213   structured metadata, one JSON per recording
├── index.csv                  source of truth
├── index.xlsx                 formatted, with an About sheet
├── browse.html                searchable single-page browser
└── inventory.csv              every file + SHA-256
```

### Two ways to read it

**`index.xlsx`** — sort, filter and scan. Each row's **title links to its transcript** and the **filename links to the audio**, so a promising row is one click from the file. A second *About* sheet explains provenance, folders, every column and the caveats.

**`browse.html`** — a single 12.6 MB page holding all 243 recordings and the full text of all 213 transcripts. Search runs over metadata *and* every transcript at once, so a phrase buried 30,000 characters into a call is findable; matches are highlighted and the transcript excerpt is centred on the first hit rather than starting at the top. Folder and category filters compose with search. Audio plays inline. No server, no internet — just open the file.

The spreadsheet is better for sorting and bulk scanning; the page is better for reading and for finding a half-remembered phrase.

Filenames follow `YYYY-MM-DD_HHMM_category_slug_participants.ext`:

```
2025-10-20_1530_call_aht-bands-pilot-data-review_charlie.qta
2026-07-07_1517_meeting_qed-board-meeting-july-7_josh-aaron-etal.qta
```

Date first so the folder sorts chronologically; participants omitted when nothing was evidenced; capped at 100 characters, with length recovered from the slug rather than the date.

---

## Topic sorting

Keyword matching does not work for this. "Amazon" appears in 89 transcripts, but one is *"I just found it on Amazon"* about a book and another is a job interview at a **medical device company** where Amazon comes up 21 times as a former employer. Both would land in the folder.

`classify_topic.py` classifies from the generated title/topic/summary instead — what a recording is *about*, not which words it contains. Cheap, because those are two sentences rather than a 40,000-character transcript.

Three lessons are baked into the tool:

- **The topic definition is the thing that gets refined.** The first Amazon pass missed two internal AI-governance training sessions because the definition enumerated AHT and promo work and never said *training*. The model followed the spec correctly; the spec was incomplete.
- **A recording can belong to two topics.** Whichever folder claims it first keeps it, and the overlap is reported rather than silently counted as moved. `--reclaim` lets a narrower topic take files back from a broader one — Techland reclaimed 19 recordings that the venture pass had swept into `qed/`.
- **`--min-confidence` protects confident placements.** Three recordings scored *low* for Techland while already sitting in `qed/` at *high* confidence; moving them would have traded a confident placement for an unconfident one.

The same person can span topics: Charlie appears both as an Amazon colleague and as the Techland Ventures partner, which is exactly why name matching fails and topic classification works.

---

## Enrichment

One Claude call per transcript via the **Batch API** (50% off), returning schema-constrained JSON: title, slug, category, topic, summary, participants with evidence, action items, flags.

| | |
|---|---|
| Input tokens | 2,227,559 |
| Output tokens | 158,250 |
| Succeeded | 213 / 213 |
| Wall clock | under 2 minutes |
| **Cost** | **$7.55** |

Chunking turned out to be unnecessary — the longest transcript is ~24k tokens against a 1M-token context window.

The `flags` column earns its place: it caught personal contact details spoken aloud, health disclosures, layoff speculation, recordings that are mostly silence, and systematic transcription garbling ("ABS" for AWS, "Blue Crawlers" for Glue Crawlers).

---

## Local Whisper fallback

Two long recordings Apple never transcribed were run locally on an RTX 4080 SUPER. Getting them right took four attempts, and the tuning inverted two pieces of standard advice:

- **VAD plus raw levels lost real speech** on a 67%-silent, -42.7 dB room recording. Only no-VAD *combined with* loudness normalization helped (+36% on an A/B slice); either change alone did nothing or hurt.
- **The usual `compression_ratio > 2.4` hallucination filter catches none of these.** Dumping 404 segments with their statistics showed the hallucinations are *short* stock phrases that compress poorly and score **low** — median 0.56 against 1.48 for real speech. Filtering `cr < 0.7 AND nsp > 0.8` removes 81% of them for a 3% cost in genuine segments. `avg_logprob` does not discriminate at all, and filtering on it was destroying real quiet speech.
- `condition_on_previous_text=False` stops one invented "Thank you." from breeding a hundred more.

Net on the quiet file: **5,136 → 11,822 characters**, hallucinated "Thank you." from **101 → 3**.

---

## Requirements

- Windows (pipeline portable to Linux) · Python 3.11+
- [libimobiledevice](https://libimobiledevice.org/) for extraction
- `anthropic`, `openpyxl` · ffmpeg + `faster-whisper` only for the fallback
- `ANTHROPIC_API_KEY` in the environment

## Running it again

```bash
python extract_recordings.py          # backup → originals/, hash-verified
python extract_transcripts.py         # → transcripts/
python build_library.py               # → library/
python enrich.py --sample 3           # eyeball quality first
python enrich.py --submit             # batch, 50% off
python enrich.py --collect <batch_id>
python build_index.py --rename        # index + rename
python classify_topic.py --topic amazon --folder amazon --move
```

Dry run is the default for anything that touches files. Cached results mean a re-run costs nothing for work already done.
