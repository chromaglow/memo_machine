# Handoff

Everything needed to pick this up cold — current state, where things live, decisions that were made and why, and the traps that will bite anyone who touches the parsing or the participants field.

**Status as of 2026-07-28: complete and verified.** The migration is done. What remains is the quarterly repeat.

---

## Current state

| | |
|---|---|
| Recordings | 243 · 131.7 hours · 32.34 GB |
| Transcripts | 213 — 211 read from the audio files, 2 via local Whisper |
| Enriched | 213 (all that have transcripts) |
| Without transcripts | 30 — mostly sub-60-second fragments, kept and flagged |
| Verification | every file SHA-256 checked against the phone backup |
| Enrichment spend | $7.55, one Batch API job |

Folders: `library/` 75 · `amazon/` 82 · `qed/` 66 · `techland/` 20.

**The phone has not been touched.** Its ~30 GB is still occupied; clearing it is a manual decision for Ezra after he has spot-checked the archive.

---

## Where everything lives

**Code** — `C:\Users\ezras\OneDrive\Documents\work\GitHub\memo machine`, pushed to [github.com/chromaglow/memo_machine](https://github.com/chromaglow/memo_machine). Code only; `.gitignore` keeps all audio, transcripts and indexes out.

**Data** — `C:\Users\ezras\memo-machine-data`, deliberately **outside OneDrive**. The repo lives in OneDrive, and while `.gitignore` keeps audio out of git, OneDrive would still sync 30 GB of personal recordings to the cloud.

```
memo-machine-data\
├── library\        the archive: audio + transcript side by side, sorted by topic
│   ├── amazon\  qed\  techland\   and the root for everything else
├── originals\      immutable, phone filenames, never renamed or moved
├── transcripts\    text + word timings
├── enriched\       one JSON per recording, keyed by audio SHA-256
├── index.csv       source of truth
├── index.xlsx      formatted, hyperlinked, with an About sheet
├── browse.html     searchable single-page browser
├── inventory.csv   every file + hash
└── classification_*.json   cached topic verdicts
```

`library/` is hard-linked to `originals/` — the same bytes under two names, so the archive costs no extra disk and deleting one does not free space while the other exists.

**Credentials** — `ANTHROPIC_API_KEY` is set as a Windows **user** environment variable. A process started before it was set will not see it; read it explicitly with:

```powershell
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY","User")
```

**libimobiledevice** — `C:\Users\ezras\tools\libimobiledevice`, on the user PATH.

---

## The identifier that matters

**Everything is keyed on the audio file's SHA-256, not its filename or location.** Files have been renamed once and moved between folders twice; the hash is what ties a row in the spreadsheet to a JSON in `enriched/` to a file on disk. Any new stage should key on it too.

This is also why re-running anything is safe: enrichment skips work already cached under a hash, and topic verdicts cache to disk.

---

## Decisions and why

| Decision | Reasoning |
|---|---|
| Data outside OneDrive | 30 GB of personal audio would otherwise sync to the cloud |
| Hard links, not copies | The archive is browsable without doubling disk usage |
| `originals/` keeps phone filenames | The immutable copy stays recognisable and never participates in renaming |
| Batch API for enrichment | 50% cheaper; nothing here is latency-sensitive |
| Opus 5 over Haiku | The spread was about four dollars on a one-time job, and the participants rule is exactly where a stronger model earns it |
| Whisper kept out of the main path | It exists for two recordings out of 243 |
| Filenames carry participants | Ezra's call — lets you eyeball "everything with Charlie" in a listing, omitted when nothing was evidenced |
| Index written before renaming | A rename failure must never leave the sheet describing files that don't exist |
| Classification from summaries, not transcripts | Two sentences instead of 40,000 characters, and it judges subject rather than vocabulary |

---

## Traps

**Read these before touching the parser or the participants field.** Each one produced a silent wrong answer, not an error.

### Container parsing

- **iOS 26 records to `.qta`, not `.m4a`.** Filtering on `.m4a` finds 13% of a modern library. Drive off `ZCLOUDRECORDING.ZPATH` or match both extensions.
- **The two formats store the transcript in different places** — `.m4a` in `udta > tsrp`, `.qta` in `meta > keys/ilst` under the key `com.apple.VoiceMemos.tsrp`.
- **QuickTime `meta` is a plain container; ISO-BMFF `meta` is a full box.** Skipping four version/flags bytes unconditionally desyncs every `.qta` parse.
- **A file has more than one `meta` box.** Pair `keys` and `ilst` within the same box or the decorative `udta` one wins.
- **An untranscribed recording still carries a `tsrp` payload** with an empty string. Presence is not evidence of a transcript.
- **`ZDATE` is a Core Data timestamp** — add 978307200. Getting it wrong shifts every date by 31 years and a spot check of the time portion will not reveal it.
- **`ZCUSTOMLABEL` is a UTC ISO timestamp, not the title.** The human title is `ZENCRYPTEDTITLE`, which is plaintext despite the name.

### Participants

The field is governed by three layers, and **all three are load-bearing**:

1. Every name must carry a verbatim quote proving presence.
2. `verify_participants()` checks that quote against the transcript — it caught four fabricated ones.
3. `OWNER_NAMES` filters Ezra deterministically. His quotes are *genuine*, so layers 1 and 2 pass; the rule only works in code.

If the rule is ever tightened, use `enrich.py --reverify` to re-apply it across the corpus **with no API calls** rather than re-running a paid batch.

### Everything else

- **`--min-confidence` exists for a reason.** A weak match on a recording already filed confidently elsewhere trades a good placement for a bad one.
- **A recording can belong to two topics.** The mover reports overlaps rather than silently counting them; `--reclaim` is the deliberate override.
- **`build_index.py` must stay subfolder-aware.** It once looked only in `library/` root and would have reported all 213 files missing after the topic sort.

---

## Running it again

The quarterly repeat, assuming new recordings on the phone:

```powershell
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY","User")

idevicebackup2 backup --full C:\Users\ezras\memo-machine-data\backup
python extract_recordings.py          # hash-verified; skips what it already has
python extract_transcripts.py
python build_library.py
python enrich.py --sample 3           # eyeball quality before spending
python enrich.py --submit             # only un-enriched recordings go in the batch
python enrich.py --collect <batch_id>
python build_index.py --rename
python build_browser.py
```

Then sort any new recordings:

```powershell
python classify_topic.py --topic amazon   --folder amazon
python classify_topic.py --topic venture  --folder qed
python classify_topic.py --topic techland --folder techland --min-confidence medium --reclaim qed
```

**Dry run is the default for anything that touches files.** Add `--move` or `--rename` only after reading the plan. Delete `classification_*.json` to force a re-classification; otherwise cached verdicts are reused free.

Adding a new topic means adding an entry to `TOPICS` in `classify_topic.py` — an `include` and an `exclude` paragraph. Write the `exclude` against the neighbouring topics explicitly; `venture` and `techland` each name the other, which is what keeps Charlie's two worlds apart.

---

## Open items

Small, none blocking.

**Three low-confidence placements.** These scored *low* for Techland while sitting in `qed/` at *high* confidence, so they were left alone. `cafe-anclair-biomass-victor` is genuinely about biomass and smart-farm ag-tech and may belong with Techland.

**One recording is honestly both.** *"Amazon layoffs vent, then Techline Ventures planning with Charlie"* sits in `amazon/`. Half of each; the request that created `techland/` was scoped against QED, not Amazon.

**Two long recordings were dropped by choice.** Yojin (87m) and China meetings (33m) have no transcript. On-device transcription could not be triggered for them; local Whisper would work if they ever matter.

**The 30 untranscribed recordings** keep their phone filenames and are flagged in the spreadsheet. 24 are under 60 seconds.

**v2, if the archive proves useful:** semantic search over `transcripts/`. The word-level timestamps are already extracted, so jump-to-position playback is achievable — `browse.html` is the natural place for it.

---

## If something looks wrong

Start with `index.csv` and the `file_hash` column. Then:

- **A file seems missing** — it moved to a topic folder. `build_index.py` reports the current folder per row.
- **A participant looks wrong** — open the recording in `browse.html`; every name is shown with the quote that justified it, so you can judge the claim directly.
- **A summary seems thin** — check the `flags` column. Recordings that are mostly silence are flagged as such rather than padded.
- **A name looks garbled** — expected. Proper nouns and acronyms suffer most ("ABS" for AWS, "Blue Crawlers" for Glue Crawlers); summaries read through the errors where intent is clear and flag them where it is not.
