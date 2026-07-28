# Changelog

Built over two days, 2026-07-27 and 2026-07-28. Entries are grouped by the phase of work rather than by release, since this is a one-time migration that also has to work as a repeatable quarterly habit.

Corrections are kept in place rather than tidied away — several of them are the most useful thing in this file.

---

## Phase 0 — proving the architecture (2026-07-27)

The whole design rested on one unverified claim: that Apple writes its on-device transcript into the audio file itself. Everything downstream was cheap if true and worthless if false.

**Verified true, but the first measurement was wrong by 22×.**

- `phase0_check_tsrp.py` walks the MPEG-4 box tree and reports whether a real transcript is present.
- First survey: 23 of 32 recordings had transcripts, 259,533 characters.
- **Correction:** filtering on `.m4a` had found 13% of the library. iOS 26 records to `.qta`, and 211 of the 243 recordings were that format. Real figure: **211 of 243, 5,824,648 characters.**

Three container traps, each of which silently produced "no transcript" rather than an error:

1. **The two formats store the transcript in different places.** `.m4a` uses `moov > trak > udta > tsrp` holding raw JSON; `.qta` uses `moov > trak > meta > keys/ilst` with the key `com.apple.VoiceMemos.tsrp`.
2. **QuickTime `meta` is a plain container; ISO-BMFF `meta` is a full box** with four leading version/flags bytes. Skipping them unconditionally desynced every `.qta` parse.
3. **A file has more than one `meta` box** — the real one under `trak`, a decorative one under `udta`. Collecting `keys`/`ilst` globally let the trailing one win and yielded nothing.

Plus a verdict bug of my own: an untranscribed recording still carries a `tsrp` payload with an empty string, so atom presence was being reported as success.

**Performance:** metadata lives in a ~2 MB `moov` box while audio runs to 276 MB per file. Seeking to `moov` instead of reading whole files took a full scan from 32 GB of I/O to **0.9 seconds**.

**Bonus:** the payload carries word-level timestamps — 1.1M of them — which the spec had only hoped for.

---

## Extraction and transcripts (2026-07-27)

- `extract_recordings.py` — pulls recordings from an `idevicebackup2` backup, restoring real filenames from `Manifest.db`. The Voice Memos domain is **discovered at runtime**, not hardcoded, since it has changed across iOS versions.
- `extract_transcripts.py` — one transcript per recording from whichever source has it.
- `build_library.py` — assembles audio beside transcript in one folder, hard-linked so it costs no extra disk.

Every file is SHA-256 hashed on both sides before the copy is reported good. That verification is what makes it safe to free space on the phone.

**Result: 243/243 extracted and verified, 0 failures. All 243 decode with durations matching the phone's database.**

Also recorded here because the spec had it backwards: **`ZCUSTOMLABEL` is not the user's title** — it holds a UTC ISO timestamp. The human title is in `ZENCRYPTEDTITLE`, which despite the name is plaintext. Trusting the spec would have produced 243 rows titled `2025-11-16T01:31:29Z`.

---

## Whisper fallback (2026-07-27)

Two long recordings Apple never transcribed, run locally on an RTX 4080 SUPER. Deliberately outside the main path — it exists for a minority of files.

Four attempts. The tuning **inverted two pieces of standard advice**:

- **VAD plus raw levels lost real speech** on a 67%-silent, −42.7 dB room recording. Only no-VAD *combined with* loudness normalisation helped (+36% on an A/B slice); either change alone did nothing or hurt.
- **The usual `compression_ratio > 2.4` hallucination filter catches none of these.** Dumping 404 segments with their statistics showed the hallucinations are *short* stock phrases that compress poorly and score **low** — median 0.56 against 1.48 for real speech. Filtering `cr < 0.7 AND nsp > 0.8` removes 81% of them at a 3% cost in genuine segments.
- **`avg_logprob` does not discriminate at all** (−0.61 vs −0.49) and filtering on it was destroying real quiet speech — it cut output back to 5,269 characters.
- `condition_on_previous_text=False` stops one invented "Thank you." from breeding a hundred more.

Net on the quiet file: **5,136 → 11,822 characters**, hallucinated "Thank you." from **101 → 3**.

I stopped guessing after the third attempt, dumped every segment with its statistics, and tuned offline in one pass. That should have been the first move.

---

## Enrichment (2026-07-27)

`enrich.py` — one Claude call per transcript via the **Batch API** (50% off), returning schema-constrained JSON.

**213/213 succeeded, zero errors, under two minutes, $7.55.**

| | |
|---|---|
| Input tokens | 2,227,559 |
| Output tokens | 158,250 |

### The participants rule needed teeth

The first sample listed five names for one recording at "low" confidence — including the archive owner, a third party never on the call, and a name the model itself flagged as a probable mistranscription. Hedging with a confidence field does not help: the name still reaches the spreadsheet as something to catch.

Three layers now stand between a guess and the spreadsheet:

1. **Every name must carry a verbatim quote** proving presence. The model cannot fill the field without producing a quote.
2. **`verify_participants()` checks each quote against the transcript.** Four names were dropped because their evidence could not be found — Ravi, Jesse, Josh, and "North" (a transcription artifact).
3. **The owner is filtered deterministically.** People address him by name constantly, so his quotes are genuine and pass step 2. "Ezra" survived in 6 records until the rule was moved into code.

Re-running the same sample after the fix: the recording titled *"Josh onboard 1"* returned an **empty** participant list, explaining that every mention of Josh occurred inside a hypothetical sales pitch.

**Across 213 recordings: 114 with evidenced names, 101 correctly returning `none`.**

### Estimates that moved

$5.35 → $6.00 → $7.75 → **$7.55 actual**. Each revision replaced a guess with a measurement: voice transcripts tokenise denser than prose (2.8 chars/token, not the 3.7 assumed), and requiring evidence quotes pushed output from ~350 to ~983 tokens per recording. The final estimate was measured with `count_tokens` across all 213 requests and landed within 3%.

**Chunking turned out to be unnecessary** — the longest transcript is ~24k tokens against a 1M-token window. That removed the most complex part of the design and the accuracy loss that comes with stitching chunk summaries.

---

## Index and renaming (2026-07-27)

`build_index.py` — `index.csv` (source of truth) and `index.xlsx` (formatted), then renames the archive to:

```
YYYY-MM-DD_HHMM_category_slug_participants.ext
```

The index is written **before** any rename, so a rename failure can never leave the sheet describing files that were never created. Length is recovered from the slug rather than the date or category, which are what make the folder sortable.

**243 rows, 213 renamed, 0 collisions, longest filename 80 characters against the 100 cap. Every date sourced from the phone's database — none fell back to file timestamps.** Sampled files re-hash correctly after renaming and `originals/` is untouched.

---

## Topic sorting (2026-07-28)

`classify_topic.py` — sorts recordings into subfolders by subject.

**Keyword matching does not work for this.** "Amazon" appears in 89 transcripts, but one is *"I just found it on Amazon"* about a book and another is a job interview at a medical device company where Amazon comes up 21 times as a former employer. Classification runs on the generated title/topic/summary instead — what a recording is *about* — which is also cheap, since those are two sentences rather than a 40,000-character transcript.

Three lessons, each now a feature:

- **The topic definition is the thing that gets refined.** The first Amazon pass matched 77 but missed two internal AI-governance training sessions, because the definition enumerated AHT and promo work and never said *training*. The model followed the spec correctly; the spec was incomplete. Widened → 82.
- **A recording can belong to two topics.** The move loop counted a file as moved when another folder had already claimed it, because it only checked `library/` root and incremented regardless. Now reported explicitly. `--reclaim` lets a narrower topic take files back from a broader one.
- **`--min-confidence` protects confident placements.** Three recordings scored *low* for Techland while already sitting in `qed/` at *high* confidence; moving them would have traded a confident placement for an unconfident one.

**"Dad's board meetings" was resolved from the transcripts, not guessed.** DADS is a client account alongside United Way — *"United Way and Dads are… hot potatoes, but they're coming"* — not the owner's father.

The same person spans topics: Charlie appears both as an Amazon colleague and as the Techland Ventures partner. That is exactly why name matching fails and topic classification works. Techland reclaimed **19 recordings** the venture pass had swept into `qed/`.

Final: `library/` 75 · `amazon/` 82 · `qed/` 66 · `techland/` 20 = **243**, no file in two folders.

---

## Documentation and interfaces (2026-07-28)

- `index.xlsx` gained a **`folder` column** and a second **About sheet** covering provenance, folders, every column, and the caveats — notably that a blank participants cell means *nobody could be evidenced*, not that nobody was there.
- **Spreadsheet hyperlinks:** each row's title opens its transcript, the filename opens the audio. Absolute URIs, because Excel resolves relative links against the workbook's own location.
- **`build_browser.py` → `browse.html`:** a single self-contained 12.6 MB page holding all 243 recordings and the full text of all 213 transcripts. Search covers metadata *and* transcript text in one pass; excerpts centre on the first hit rather than the top of the file, since a match 30,000 characters in is otherwise invisible. Filters compose with search; audio plays inline.

**A latent bug surfaced here:** `build_index.py` only ever looked in `library/` root. After the topic sort moved 168 files into subfolders, re-running it would have reported all 213 as missing. It now locates files wherever they sit and renames in place.

Verified in a real browser rather than assumed. The check worth keeping: searching **"Glue Crawlers" returns nothing while "Blue Crawlers" returns 1** — "Blue Crawlers" is the actual mistranscription in the audio, "Glue Crawlers" appears only in the flag describing it. That confirms search reaches transcript text, not just metadata about it.

---

## Storage

| Event | Change |
|---|---|
| Backup taken | +65 GB |
| Recordings extracted | +30 GB (`originals/`) |
| Library assembled | +0 (hard links) |
| Backup deleted after verification | **−65 GB** |

Deletion was gated on re-verifying all 243 files present in `originals/` with matching hashes. `originals/` keeps the phone's own filenames and is never renamed or moved.

**Nothing was ever deleted from the phone.** That remains a manual, deliberate act.
