#!/usr/bin/env python3
"""Build a shareable demo of the archive browser using invented data.

The real browser embeds actual transcripts and links to `file:///` paths on one
machine, so it neither travels nor should be shown in a room. This produces the
same page driven by fabricated recordings: every control works, search runs over
full synthetic transcripts, and the audio player plays a short silence so the
control is real rather than a picture of one.

It imports the template from `build_browser.py` on purpose — the demo cannot
drift from the interface it is demonstrating.

Nothing here is derived from any real recording. Names, companies and content
are invented.

Usage:
    python build_demo.py [--out demo.html] [--count 48]
"""

import argparse
import base64
import datetime
import html
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_browser import PAGE  # noqa: E402

BANNER = ('<div class="banner">DEMONSTRATION — every recording, name and '
          'transcript on this page is invented. No real data is included.</div>')

FOLDERS = {
    "northwind": "Northwind Logistics client delivery",
    "atlas": "Atlas platform — internal product work",
    "harbor": "Harbor Advisory — board and investor",
    "": "unsorted",
}

PEOPLE = ["Dana", "Marcus", "Priya", "Tom", "Rosa", "Ingrid", "Wes", "Nadia",
          "Colin", "Yusuf", "Béatrice", "Sam"]

TOPICS = {
    "northwind": [
        ("Northwind depot rollout — phase two scope",
         "Working session on the phase-two depot rollout, covering scope, the revised timeline and who owns the integration work.",
         ["Confirm the phase-two scope document with the Northwind ops lead before Friday",
          "Pull last quarter's depot throughput numbers into the shared dashboard",
          "Get a written answer on whether the Leeds site is in scope"]),
        ("Northwind weekly: throughput dashboard and staffing",
         "Weekly delivery call on dashboard adoption, a staffing gap on the night shift, and whether the pilot metrics justify a wider rollout.",
         ["Rebuild the throughput view to split day and night shifts",
          "Ask about backfill for the two open night-shift roles"]),
        ("Depot integration blocker — inventory feed",
         "Call about the inventory feed arriving twice daily instead of hourly, and what that does to the live dashboard.",
         ["Escalate the feed frequency question to Northwind's data team",
          "Add a staleness indicator to the dashboard until the feed is fixed"]),
        ("Northwind contract renewal framing",
         "Discussion of how to frame the renewal conversation, which outcomes to lead with, and what the pricing floor should be.",
         ["Draft the renewal one-pager leading with throughput gains",
          "Agree an internal pricing floor before the meeting"]),
    ],
    "atlas": [
        ("Atlas ingest pipeline — design review",
         "Design review of the ingest pipeline, focused on where transformation should happen and how failures surface to the operator.",
         ["Write up the two ingest options with a recommendation",
          "Add a dead-letter path so failed records are visible rather than dropped"]),
        ("Atlas sprint planning — search and export",
         "Sprint planning covering the search rewrite, the export format, and what gets cut to protect the demo date.",
         ["Cut PDF export from this sprint and say so in the notes",
          "Timebox the search rewrite to four days"]),
        ("Atlas: onboarding friction walkthrough",
         "Walkthrough of the first-run experience, where new users stall, and two changes that would remove most of the friction.",
         ["Remove the workspace-name step from first run",
          "Instrument the onboarding funnel before changing anything else"]),
        ("Atlas pricing model discussion",
         "Long discussion of seat-based versus usage-based pricing and which one survives contact with the current customer mix.",
         ["Model both pricing options against the top ten accounts"]),
        ("Atlas incident review — export timeouts",
         "Review of the export timeouts, what the actual cause turned out to be, and the one change that would have caught it earlier.",
         ["Add a timeout alert at eighty percent of the limit",
          "Write the incident up while it is still fresh"]),
    ],
    "harbor": [
        ("Harbor board meeting — quarter close",
         "Quarterly board meeting covering revenue against plan, the hiring pause, and a decision on the second product line.",
         ["Circulate the revised quarterly numbers before the next board call",
          "Bring a written recommendation on the second product line"]),
        ("Investor update prep — narrative and numbers",
         "Preparation for the investor update: which numbers lead, how to frame the slower quarter, and what to leave out.",
         ["Rewrite the update to lead with retention rather than headcount",
          "Get the cohort chart corrected before it goes out"]),
        ("Harbor advisory intake — new engagement",
         "Intake call for a prospective advisory engagement, covering their situation, what they think they need, and what they actually need.",
         ["Send the scoping questionnaire",
          "Block two hours to write the engagement proposal"]),
        ("Runway and hiring sequencing",
         "Conversation about runway under two hiring scenarios and which roles genuinely unblock revenue.",
         ["Rebuild the runway model with the slower hiring plan",
          "Decide whether the ops hire comes before or after the renewal"]),
    ],
    "": [
        ("Note to self — restructure the weekly review",
         "A short note working through why the weekly review keeps running long and what to drop from it.",
         ["Cut the status round-robin from the weekly review"]),
        ("Conference debrief — three things worth following up",
         "Debrief after a conference, picking out the few conversations worth following up and discarding the rest.",
         ["Follow up with the two people worth a second conversation"]),
        ("Idea — automated meeting digest",
         "Thinking out loud about whether an automated digest of recorded meetings would actually get read, or just add another unread thing.",
         []),
        ("Quick note before the Tuesday call", "A brief note capturing two points to raise on the Tuesday call.", []),
    ],
}

FLAGS = [
    "Recording is largely ambient noise for the first four minutes.",
    "Several proper nouns are garbled in the transcript and may be mistranscribed.",
    "Names mentioned but not evidenced as present: Rosa, Colin.",
    "Contains a figure quoted from memory that should be checked against the source.",
    "Recording ends mid-sentence.",
    "Contains commercially sensitive pricing discussion.",
]

OPENERS = [
    "Right, are we recording? Good.", "Okay, so where did we leave this last time?",
    "Let me share my screen — can you see that?", "Sorry, I was on mute. Start again?",
    "Before we get into it, one quick thing.",
]
LINES = [
    "So the way I'd frame it is, we've got two options and neither is free.",
    "Yeah, and that's the bit I keep going back and forth on.",
    "Right, but that assumes the numbers we pulled last month are still good.",
    "Can I push back on that slightly? I don't think the timeline holds.",
    "Okay so action on me then — I'll take that away and come back Thursday.",
    "The honest answer is we don't know yet, and I'd rather say that than guess.",
    "It's not a technical problem, it's a sequencing problem.",
    "Let's park that. It's real but it's not this week's problem.",
    "What would have to be true for that to work?",
    "I think we're agreeing violently at this point.",
    "The dashboard says one thing and the floor says another, and the floor is usually right.",
    "If we do that, we're committing to supporting it for a year. Are we happy with that?",
    "Give me the short version — what breaks if we don't do this?",
    "That's fair. I'd want it written down though, otherwise it evaporates.",
    "Hmm. Say more about the second one.",
    "We tried that eighteen months ago and it didn't stick, but the reasons might be gone now.",
    "Let's take the decision now rather than carry it another fortnight.",
    "I'd rather ship the smaller thing and learn than plan the bigger one.",
    "Who owns this once we're done talking about it?",
    "I don't want to relitigate that — we settled it in January.",
    "Is that a guess or do we have the number in front of us?",
    "Fine. Write it down as a risk and we move on.",
    "My worry is we're solving the visible problem, not the expensive one.",
    "Can we do that without a migration? Because a migration eats the quarter.",
    "Honestly, I think we've been overthinking this one for a month.",
    "Let's not design it in the call. Take it away and bring back two options.",
    "Sorry, I lost you for a second there — say the last bit again.",
    "That's the first thing today that's actually changed my mind.",
    "I'd want to hear from the people doing the work before we commit.",
    "There's a version of this that's a week and a version that's a quarter.",
    "Right, so what's the smallest thing that tells us whether this is real?",
    "If it slips again we should just say so publicly rather than quietly.",
    "Are we sure the requirement is real, or did someone once ask for it?",
    "I'll be blunt — I don't think that timeline survives contact with reality.",
    "Good. Then that's a decision and I'll write it up as one.",
    "The cost of being wrong here is low, so let's just try it.",
    "We keep coming back to this because we never actually wrote it down.",
    "Say we do nothing. What breaks, and when?",
    "That reads as a bigger commitment than I think we intend.",
    "I'd flip that — start with the constraint and work backwards.",
    "Let's get it in front of a real customer before we polish it further.",
]
CLOSERS = [
    "Alright, good. I'll write this up and send it round.",
    "Okay, same time next week then. Thanks both.",
    "That's me done — anything else before we drop?",
]

# Specific, searchable details. Generic filler makes every transcript match
# every query, which kills the point of the demo — a search has to return a
# small, believable set. These carry distinctive nouns and figures so a precise
# query lands on a handful of recordings.
DETAILS = [
    "The Leeds depot was running at sixty-two percent of capacity last month.",
    "Rotterdam came back with a counter-offer on the warehousing rate.",
    "We're still waiting on the customs paperwork for the Bilbao route.",
    "The night shift at Doncaster is two people short and has been since April.",
    "Their finance team wants everything in euros, which changes the invoicing.",
    "The pilot covered four depots, not the six we originally scoped.",
    "Throughput went from eleven hundred to about fourteen hundred units a day.",
    "The inventory feed lands at six in the morning and again at six at night.",
    "Migration off the legacy scheduler is pencilled in for the second week of March.",
    "There's a hard dependency on the warehouse management upgrade finishing first.",
    "The export job times out at ninety seconds and nobody had alerted on it.",
    "We're at about four hundred monthly active users, up from three-twenty.",
    "Storage costs roughly doubled after we started retaining raw events.",
    "The search rewrite is the one piece I'd protect if the sprint slips.",
    "Two customers asked for SSO in the same week, which is usually a signal.",
    "The onboarding funnel drops forty percent at the workspace-name step.",
    "Median time to first value is currently about eleven minutes.",
    "The API rate limit is a hundred requests a minute and we're nowhere near it.",
    "Churn is concentrated entirely in accounts under five seats.",
    "Retention at month six is sitting just above seventy percent.",
    "Runway is fourteen months on the current plan, nine if we hire the two engineers.",
    "The board wants a decision on the second product line before the quarter closes.",
    "Gross margin came in at sixty-eight percent, which is better than forecast.",
    "One investor asked specifically about concentration risk in the top three accounts.",
    "We closed the quarter about eight percent under plan on new revenue.",
    "The hiring pause holds until the renewal cycle is finished.",
    "Their term sheet had a liquidation preference we weren't going to accept.",
    "Legal flagged the indemnity clause and wants it capped.",
    "The advisory engagement is scoped at three months with a review at six weeks.",
    "Half the value of that meeting was in the corridor conversation afterwards.",
    "I'd rather ship the smaller version in March than the full thing in June.",
    "The Tuesday call keeps overrunning because we do status before decisions.",
    "Nobody has owned the data quality question since Marcus moved teams.",
    "We agreed to revisit this after the Northwind renewal is signed.",
    "The demo environment still has last year's sample data in it.",
    "Support tickets about the export doubled the week after the release.",
    "Two of the three blockers turned out to be the same underlying issue.",
    "The forecast assumes no seasonality, which is obviously wrong for this business.",
    "We should stop calling it a pilot — it's been in production for five months.",
    "The contract auto-renews in November unless someone gives notice in September.",
]


def silent_wav(seconds: float = 0.4, rate: int = 8000) -> str:
    """A short silent WAV as a data URI, so the audio control genuinely works."""
    frames = int(rate * seconds)
    data = b"\x00\x00" * frames
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
              struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
              b"data" + struct.pack("<I", len(data)))
    return "data:audio/wav;base64," + base64.b64encode(header + data).decode()


def transcript(rng, minutes: int, seed_terms: list) -> str:
    """Build a plausible conversation long enough that search has to work for it.

    Filler is sampled *without replacement* and details are drawn from a large
    pool, so a distinctive query hits a handful of recordings rather than nearly
    all of them. Sampling with replacement from a small pool put the same
    sentence in 45 of 48 transcripts, which made search look useless.
    """
    beats = max(8, minutes * 3)
    # A genuine subset, never the whole pool: asking for more lines than the
    # pool holds returns all of them, which is how the same sentence ended up
    # in 45 of 48 transcripts.
    filler = rng.sample(LINES, min(len(LINES) // 4, rng.randint(6, 9)))
    details = rng.sample(DETAILS, min(len(DETAILS), rng.randint(3, 6)))

    out = [rng.choice(OPENERS)]
    for i in range(beats):
        out.append(filler[i % len(filler)])
        if details and rng.random() < 0.3:
            out.append(details.pop())
        if seed_terms and rng.random() < 0.1:
            out.append(f"And that comes back to {rng.choice(seed_terms).lower()}, "
                       f"which we still haven't closed out.")
    out.extend(details)          # make sure every detail lands somewhere
    out.append(rng.choice(CLOSERS))
    return " ".join(out)


def build(count: int) -> list:
    rng = random.Random(20260728)
    audio = silent_wav()
    start = datetime.date(2026, 2, 2)
    records, used = [], set()

    for i in range(count):
        folder = rng.choice(["northwind", "atlas", "harbor", "", "atlas", "harbor"])
        title, topic, actions = rng.choice(TOPICS[folder])
        # Keep titles distinguishable when a template repeats.
        n = 2
        base = title
        while title in used:
            title = f"{base} ({n})"
            n += 1
        used.add(title)

        day = start + datetime.timedelta(days=int(i * 1.7) + rng.randint(0, 2))
        hour, minute = rng.choice([(9, 5), (10, 30), (11, 15), (13, 0), (14, 45), (16, 20)])
        minutes = rng.choice([4, 9, 14, 22, 31, 46, 58])
        category = rng.choice(["meeting", "call", "call", "meeting", "note-to-self"]) \
            if folder else rng.choice(["note-to-self", "idea", "other", "call"])

        people = []
        if category in ("meeting", "call") and rng.random() > 0.35:
            for name in rng.sample(PEOPLE, rng.choice([1, 1, 2, 3])):
                people.append({
                    "name": name,
                    "evidence": rng.choice([
                        f"Thanks {name}, that's helpful.",
                        f"{name}, do you want to take that one?",
                        f"Hi, this is {name} — can everyone hear me?",
                        f"I'll hand over to {name} for the numbers.",
                    ]),
                })

        seed = [w for w in title.replace("—", " ").split() if len(w) > 5][:3]
        text = transcript(rng, minutes, seed)
        flags = rng.sample(FLAGS, rng.choice([0, 0, 1, 1, 2]))
        acts = actions[: rng.randint(0, len(actions))] if actions else []

        slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        filename = f"{day:%Y-%m-%d}_{hour:02d}{minute:02d}_{category}_{slug[:44]}.m4a"

        rec = {
            "i": i,
            "d": f"{day:%Y-%m-%d}", "t": f"{hour:02d}:{minute:02d}",
            "dur": f"{minutes}:{rng.randint(0,59):02d}",
            "ti": title, "to": topic,
            "s": topic + " " + rng.choice([
                "The discussion stayed practical throughout and ended with a clear owner for each open point.",
                "No decision was reached on the second item; it was deferred rather than resolved.",
                "Most of the value is in the last ten minutes, after the tangent about tooling.",
                "Two participants disagreed on sequencing and the disagreement was left open deliberately.",
            ]),
            "c": category, "f": folder,
            "a": acts, "fl": flags, "p": people,
            "fn": filename, "au": audio, "txu": "", "tx": text,
        }
        rec["hay"] = " ".join([
            rec["ti"], rec["to"], rec["s"], rec["c"], rec["f"],
            " ".join(acts), " ".join(flags),
            " ".join(p["name"] for p in people), filename, text,
        ]).lower()
        records.append(rec)

    records.sort(key=lambda r: (r["d"], r["t"]), reverse=True)
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path(r"C:\Users\ezras\memo-machine-data\demo.html"))
    ap.add_argument("--count", type=int, default=48)
    args = ap.parse_args()

    records = build(args.count)
    hours = sum(int(r["dur"].split(":")[0]) for r in records) / 60
    chars = sum(len(r["tx"]) for r in records)
    sub = (f"{len(records)} recordings · {hours:.0f} hours · {len(records)} transcripts "
           f"· {chars:,} characters searchable · click a row to expand   "
           f"— sample data for demonstration")

    page = (PAGE.replace("__BANNER__", BANNER)
                .replace("__SUB__", html.escape(sub))
                .replace("__DATA__", json.dumps(records, ensure_ascii=False))
                .replace("<title>Voice Memo Archive</title>",
                         "<title>Voice Memo Archive — Demo</title>")
                .replace("<h1>Voice Memo Archive</h1>",
                         "<h1>Voice Memo Archive <span style=\"font-weight:400;"
                         "color:var(--muted)\">— demo</span></h1>"))
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out}  ({args.out.stat().st_size/1e3:.0f} KB)")
    print(f"  {len(records)} invented recordings, {chars:,} characters of "
          f"synthetic transcript")
    folders = {}
    for r in records:
        folders[r["f"] or "(root)"] = folders.get(r["f"] or "(root)", 0) + 1
    print("  " + " · ".join(f"{k} {v}" for k, v in sorted(folders.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
