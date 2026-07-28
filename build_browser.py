#!/usr/bin/env python3
"""Build a single-file HTML browser for the archive.

A spreadsheet cannot show a 40,000-character transcript in a cell, and it cannot
search across all of them at once. This writes one self-contained page holding
every recording's metadata and full transcript, with instant search over the
lot, filters, inline playback and links to the files on disk.

Everything is embedded, so the page works offline and can be moved anywhere —
though the audio links are absolute paths and stop resolving if the library
moves.

Usage:
    python build_browser.py
"""

import argparse
import csv
import html
import json
import sys
from pathlib import Path

DEFAULT_DATA = Path(r"C:\Users\ezras\memo-machine-data")

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Memo Archive</title>
<style>
  :root {
    --bg:#faf9f7; --panel:#fff; --ink:#1a1a1a; --muted:#6b6b6b; --line:#e3e0da;
    --accent:#8c5a3c; --accent-soft:#f2ece6; --mark:#ffe8a3;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16161a; --panel:#1e1e24; --ink:#eceaea; --muted:#9b9b9b;
            --line:#2e2e36; --accent:#d9a273; --accent-soft:#2a2229; --mark:#5c4a1a; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:16px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; }
  header { position:sticky; top:0; z-index:10; background:var(--bg);
    border-bottom:1px solid var(--line); padding:14px 20px 12px; }
  h1 { margin:0 0 3px; font-size:19px; letter-spacing:-.01em; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:11px; }
  #q { width:100%; padding:11px 13px; font-size:15px; border:1px solid var(--line);
    border-radius:9px; background:var(--panel); color:var(--ink); }
  #q:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  .chips { display:flex; gap:7px; flex-wrap:wrap; margin-top:10px; align-items:center; }
  .chip { padding:5px 12px; border:1px solid var(--line); border-radius:999px;
    background:var(--panel); color:var(--muted); cursor:pointer; font-size:13px; }
  .chip[aria-pressed="true"] { background:var(--accent); border-color:var(--accent);
    color:#fff; }
  .count { margin-left:auto; color:var(--muted); font-size:13px; }
  main { padding:16px 20px 60px; max-width:1120px; margin:0 auto; }
  article { background:var(--panel); border:1px solid var(--line); border-radius:11px;
    margin-bottom:11px; overflow:hidden; }
  .head { padding:13px 16px; cursor:pointer; display:grid;
    grid-template-columns:112px 1fr auto; gap:14px; align-items:baseline; }
  .head:hover { background:var(--accent-soft); }
  .when { color:var(--muted); font-size:13px; font-variant-numeric:tabular-nums; }
  .ttl { font-weight:600; }
  .topic { color:var(--muted); font-size:14px; margin-top:3px; }
  .tags { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
  .tag { font-size:11.5px; padding:2px 8px; border-radius:999px;
    background:var(--accent-soft); color:var(--muted); white-space:nowrap; }
  .tag.folder { background:var(--accent); color:#fff; }
  .body { display:none; padding:0 16px 16px; border-top:1px solid var(--line); }
  article.open .body { display:block; }
  .body h4 { margin:15px 0 5px; font-size:12px; text-transform:uppercase;
    letter-spacing:.07em; color:var(--muted); }
  ul { margin:0; padding-left:20px; }
  li { margin:3px 0; }
  .flag { color:var(--accent); }
  .who { font-size:14px; }
  .who .ev { color:var(--muted); font-style:italic; }
  audio { width:100%; margin-top:11px; }
  .links { margin-top:11px; display:flex; gap:14px; flex-wrap:wrap; font-size:13.5px; }
  .links a { color:var(--accent); }
  pre.tx { white-space:pre-wrap; background:var(--bg); border:1px solid var(--line);
    border-radius:8px; padding:13px; max-height:340px; overflow:auto;
    font:13.5px/1.65 ui-monospace,"Cascadia Code",Consolas,monospace; margin:0; }
  mark { background:var(--mark); color:inherit; padding:0 1px; border-radius:2px; }
  .none { text-align:center; color:var(--muted); padding:60px 0; }
  .banner { background:var(--accent); color:#fff; padding:8px 20px; font-size:13px;
    text-align:center; letter-spacing:.02em; }
</style>
</head>
<body>
__BANNER__
<header>
  <h1>Voice Memo Archive</h1>
  <div class="sub">__SUB__</div>
  <input id="q" type="search" placeholder="Search titles, summaries, people, action items — and the full text of every transcript…" autocomplete="off">
  <div class="chips" id="chips"></div>
</header>
<main id="list"></main>
<script>
const DATA = __DATA__;
const list = document.getElementById('list'), q = document.getElementById('q');
let folder = '', category = '';

const esc = s => (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function hl(text, term) {
  const t = esc(text);
  if (!term) return t;
  return t.replace(new RegExp('(' + term.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&') + ')','gi'),
                   '<mark>$1</mark>');
}
// Show the transcript around the first hit rather than from the top — a match
// 30,000 characters in is invisible otherwise.
function excerpt(text, term) {
  if (!term) return text.slice(0, 4000);
  const i = text.toLowerCase().indexOf(term.toLowerCase());
  if (i < 0) return text.slice(0, 4000);
  const s = Math.max(0, i - 900);
  return (s ? '…' : '') + text.slice(s, s + 4000);
}

function card(r, term) {
  const people = r.p.length
    ? r.p.map(p => `<div class="who"><b>${esc(p.name)}</b> — <span class="ev">“${esc(p.evidence)}”</span></div>`).join('')
    : '<div class="who" style="color:var(--muted)">No participant could be evidenced from the transcript.</div>';
  return `<article data-i="${r.i}">
    <div class="head">
      <div class="when">${r.d}<br>${r.t} · ${r.dur}</div>
      <div>
        <div class="ttl">${hl(r.ti, term)}</div>
        <div class="topic">${hl(r.to, term)}</div>
      </div>
      <div class="tags">
        ${r.f ? `<span class="tag folder">${esc(r.f)}</span>` : ''}
        <span class="tag">${esc(r.c)}</span>
        ${r.p.length ? `<span class="tag">${r.p.map(p=>esc(p.name)).join(', ')}</span>` : ''}
        ${r.tx ? '' : '<span class="tag">no transcript</span>'}
      </div>
    </div>
    <div class="body">
      <h4>Summary</h4><div>${hl(r.s, term)}</div>
      ${r.a.length ? `<h4>Action items</h4><ul>${r.a.map(x=>`<li>${hl(x,term)}</li>`).join('')}</ul>` : ''}
      <h4>Participants</h4>${people}
      ${r.fl.length ? `<h4>Flags</h4><ul>${r.fl.map(x=>`<li class="flag">${hl(x,term)}</li>`).join('')}</ul>` : ''}
      ${r.au ? `<audio controls preload="none" src="${r.au}"></audio>` : ''}
      <div class="links">
        ${r.au ? `<a href="${r.au}">Open audio</a>` : ''}
        ${r.txu ? `<a href="${r.txu}">Open transcript file</a>` : ''}
        <span style="color:var(--muted)">${esc(r.fn)}</span>
      </div>
      ${r.tx ? `<h4>Transcript (${r.tx.length.toLocaleString()} characters)</h4>
                <pre class="tx">${hl(excerpt(r.tx, term), term)}</pre>` : ''}
    </div>
  </article>`;
}

function render() {
  const term = q.value.trim();
  const lc = term.toLowerCase();
  const hits = DATA.filter(r =>
    (!folder || r.f === folder) &&
    (!category || r.c === category) &&
    (!lc || r.hay.includes(lc)));
  document.getElementById('n').textContent =
    `${hits.length} of ${DATA.length}` + (term ? ' matching' : '');
  list.innerHTML = hits.length
    ? hits.map(r => card(r, term)).join('')
    : '<div class="none">Nothing matches that.</div>';
  // With a search active the interesting part is inside, so open the first few.
  if (term) list.querySelectorAll('article').forEach((a,i) => { if (i<3) a.classList.add('open'); });
}

list.addEventListener('click', e => {
  const h = e.target.closest('.head');
  if (h) h.parentElement.classList.toggle('open');
});

const folders = [...new Set(DATA.map(r=>r.f))].filter(Boolean).sort();
const cats = [...new Set(DATA.map(r=>r.c))].filter(Boolean).sort();
const chips = document.getElementById('chips');
chips.innerHTML =
  ['<button class="chip" data-k="f" data-v="" aria-pressed="true">All folders</button>']
  .concat(folders.map(f=>`<button class="chip" data-k="f" data-v="${esc(f)}">${esc(f)}</button>`))
  .concat(['<span style="width:12px"></span>'])
  .concat(['<button class="chip" data-k="c" data-v="" aria-pressed="true">All types</button>'])
  .concat(cats.map(c=>`<button class="chip" data-k="c" data-v="${esc(c)}">${esc(c)}</button>`))
  .join('') + '<span class="count" id="n"></span>';

chips.addEventListener('click', e => {
  const b = e.target.closest('.chip'); if (!b) return;
  const k = b.dataset.k;
  chips.querySelectorAll(`.chip[data-k="${k}"]`).forEach(x=>x.setAttribute('aria-pressed','false'));
  b.setAttribute('aria-pressed','true');
  if (k === 'f') folder = b.dataset.v; else category = b.dataset.v;
  render();
});

let timer;
q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(render, 120); });
render();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    args = ap.parse_args()
    data = args.data
    library = data / "library"

    enriched = {}
    for path in (data / "enriched").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        enriched[payload["_hash"]] = payload

    records, missing_audio = [], 0
    with open(data / "index.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    for i, row in enumerate(rows):
        name = row["archive_filename"]
        found = (library / name) if (library / name).exists() else \
            next((p for p in library.glob(f"*/{name}")), None)
        if found is None:
            missing_audio += 1
        transcript = ""
        if found is not None:
            tpath = found.parent / (Path(name).stem + ".txt")
            if tpath.exists():
                transcript = tpath.read_text(encoding="utf-8")
        e = enriched.get(row["file_hash"], {})

        record = {
            "i": i,
            "d": row["date"], "t": row["time"], "dur": row["duration"],
            "ti": row["title"], "to": row["topic"], "s": row["summary"],
            "c": row["category"] or "unclassified", "f": row["folder"],
            "a": e.get("action_items", []),
            "fl": e.get("flags", []),
            "p": [{"name": p["name"], "evidence": p["evidence"]}
                  for p in e.get("participants", [])],
            "fn": name,
            "au": found.as_uri() if found else "",
            "txu": (found.parent / (Path(name).stem + ".txt")).as_uri()
                   if transcript else "",
            "tx": transcript,
        }
        # One lowercase blob per record so search is a single indexOf over
        # metadata *and* full transcript text.
        record["hay"] = " ".join([
            record["ti"], record["to"], record["s"], record["c"], record["f"],
            " ".join(record["a"]), " ".join(record["fl"]),
            " ".join(p["name"] for p in record["p"]), name, transcript,
        ]).lower()
        records.append(record)

    hours = sum(int(r["duration"].split(":")[0]) for r in rows) / 60
    chars = sum(len(r["tx"]) for r in records)
    sub = (f"{len(records)} recordings · {hours:.0f} hours · "
           f"{len([r for r in records if r['tx']])} transcripts · "
           f"{chars:,} characters searchable · click a row to expand")

    page = (PAGE.replace("__BANNER__", "")
                .replace("__SUB__", html.escape(sub))
                .replace("__DATA__", json.dumps(records, ensure_ascii=False)))
    out = data / "browse.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size/1e6:.1f} MB)")
    print(f"  {len(records)} recordings, {chars:,} characters of searchable transcript")
    if missing_audio:
        print(f"  {missing_audio} recordings had no audio file on disk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
