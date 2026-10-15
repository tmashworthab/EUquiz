"""EU Quiz: a Duolingo-style multiple-choice quiz on the dates of European integration
(1945-2025), the founding of the EU institutions and key economics terms.

Run:  python3 app.py   then open http://localhost:5050
(Phones on the same Wi-Fi can use the "Network" address printed at start-up.)
"""
import random
import re
import socket

from flask import Flask, jsonify, render_template, request

from events import EVENTS
from essentials import ESSENTIAL_STAGES
from terms import INSTITUTIONS, TERM_STAGES, TERMS

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def sort_key(date):
    """Months since Jan 1945 for the start of a date like 'July - August 1945'."""
    year = int(re.search(r"\d{4}", date).group())
    month = next((i for i, m in enumerate(MONTHS) if m in date.lower()), 0)
    return (year - 1945) * 12 + month


DATES = sorted({d for d, _ in EVENTS}, key=sort_key)

app = Flask(__name__)


def nearby(items, key_of, target_key, exclude, k=3, pool=9):
    """Pick k distractors from the `pool` items closest in time (keeps it tricky)."""
    candidates = [x for x in items if not exclude(x)]
    candidates.sort(key=lambda x: abs(key_of(x) - target_key) + random.random())
    return random.sample(candidates[:pool], k)


def years(date):
    found = [int(y) for y in re.findall(r"\d{4}", date)]
    return set(range(found[0], found[-1] + 1))


def is_month_year(date):
    return re.fullmatch(r"[A-Z][a-z]+ \d{4}", date) is not None


def clashes(a, b):
    """True if dates a and b could both be right, e.g. '1989' and 'February 1989'."""
    if a == b:
        return True
    if is_month_year(a) and is_month_year(b):
        return False
    return bool(years(a) & years(b))


def when_question(date, event):
    """'When did this happen?' -> pick the date."""
    wrong = nearby(DATES, sort_key, sort_key(date), lambda d: clashes(d, date))
    options = wrong + [date]
    random.shuffle(options)
    return {"type": "when", "prompt": "When did this happen?", "subject": event,
            "options": options, "answer": options.index(date)}


def what_question(date, event):
    """'What happened in <date>?' -> pick the event."""
    wrong = nearby(EVENTS, lambda e: sort_key(e[0]), sort_key(date),
                   lambda e: clashes(e[0], date))
    options = [e for _, e in wrong] + [event]
    random.shuffle(options)
    return {"type": "what", "prompt": "What happened in…", "subject": date,
            "options": options, "answer": options.index(event)}


def year_of(date):
    return int(re.search(r"\d{4}", date).group())


def date_from_key(key):
    return f"{MONTHS[key % 12].title()} {1945 + key // 12}"


# ---------- EU institutions ----------

def institution_when(item):
    """'When was this founded?' -> pick the date (other founding dates + nearby months)."""
    date, name, note = item["date"], item["name"], item["note"]
    others = {d for d, _, _ in INSTITUTIONS} - {date}
    nearby_dates = {date_from_key(sort_key(date) + random.choice([-1, 1]) * random.randint(2, 30))
                    for _ in range(12)} - {date}
    wrong = random.sample(sorted(others), 1)
    wrong += random.sample(sorted(nearby_dates - set(wrong)), 2)
    options = wrong + [date]
    random.shuffle(options)
    return {"type": "when", "prompt": "When was this founded?", "subject": f"{name} ({note})",
            "options": options, "answer": options.index(date)}


def institution_which(item):
    """'Which institution was founded in <date>?' -> pick the institution."""
    date, name = item["date"], item["name"]
    wrong = random.sample([n for d, n, _ in INSTITUTIONS if d != date], 3)
    options = wrong + [name]
    random.shuffle(options)
    return {"type": "what", "prompt": "Which EU institution was founded in…", "subject": date,
            "options": options, "answer": options.index(name)}


# ---------- economics terms ----------

ALL_TERMS = [(t, d, section) for section, rows in TERMS.items() for t, d in rows]


def term_distractors(item):
    """3 other terms, from the same section of the PDF where possible (harder to guess)."""
    same = [x for x in ALL_TERMS if x[2] == item["section"] and x[0] != item["term"]]
    rest = [x for x in ALL_TERMS if x[2] != item["section"]]
    picks = random.sample(same, min(3, len(same)))
    return picks + random.sample(rest, 3 - len(picks))


def term_meaning(item):
    """Term -> pick its definition."""
    options = [d for _, d, _ in term_distractors(item)] + [item["definition"]]
    random.shuffle(options)
    return {"type": "what", "prompt": "What does this term mean?", "subject": item["term"],
            "options": options, "answer": options.index(item["definition"])}


def term_name(item):
    """Definition -> pick the term."""
    options = [t for t, _, _ in term_distractors(item)] + [item["term"]]
    random.shuffle(options)
    return {"type": "term", "prompt": "Which term matches this definition?", "subject": item["definition"],
            "options": options, "answer": options.index(item["term"])}


# ---------- people (The essentials) ----------

ALL_PEOPLE = [p for _, stage in ESSENTIAL_STAGES for p in stage.get("people", [])]


def person_who(item):
    """What someone did -> pick the person."""
    wrong = random.sample([n for n, _ in ALL_PEOPLE if n != item["name"]], 3)
    options = wrong + [item["name"]]
    random.shuffle(options)
    return {"type": "when", "prompt": "Who was this?", "subject": item["known_for"],
            "options": options, "answer": options.index(item["name"])}


def person_known_for(item):
    """Person -> pick what they did."""
    wrong = random.sample([k for n, k in ALL_PEOPLE if n != item["name"]], 3)
    options = wrong + [item["known_for"]]
    random.shuffle(options)
    return {"type": "what", "prompt": "What is this person known for?", "subject": item["name"],
            "options": options, "answer": options.index(item["known_for"])}


# ---------- themes and stages ----------
# Each theme is practised on its own and is split into stages (one per decade, or two
# PDF sections of key terms). Institutions sit in the decade they were founded in.

def event_item(d, e):
    return {"kind": "event", "date": d, "event": e, "fact": f"{d}: {e}"}


def institution_item(d, n, note):
    return {"kind": "institution", "date": d, "name": n, "note": note, "fact": f"{d}: {n} founded ({note})"}


def term_item(t, d, sec):
    return {"kind": "term", "term": t, "definition": d, "section": sec, "fact": f"{t}: {d}"}


def decade_items(dec):
    return ([event_item(d, e) for d, e in EVENTS if year_of(d) // 10 * 10 == dec]
            + [institution_item(*inst) for inst in INSTITUTIONS if year_of(inst[0]) // 10 * 10 == dec])


def essential_items(spec):
    items = []
    for start in spec.get("events", []):
        matches = [(d, e) for d, e in EVENTS if e.startswith(start)]
        assert len(matches) == 1, f"essentials.py: {start!r} matches {len(matches)} events"
        items.append(event_item(*matches[0]))
    if spec.get("institutions"):
        items += [institution_item(*inst) for inst in INSTITUTIONS]
    items += [{"kind": "person", "name": n, "known_for": k, "fact": f"{n}: {k}"}
              for n, k in spec.get("people", [])]
    for name in spec.get("terms", []):
        matches = [term_item(t, d, sec) for sec, rows in TERMS.items() for t, d in rows if t == name]
        assert len(matches) == 1, f"essentials.py: no term {name!r}"
        items += matches
    return items


def decade_name(dec):
    return f"the {dec}s"


DECADE_THEMES = [("40s and 50s", [1940, 1950]), ("60s and 70s", [1960, 1970]),
                 ("80s and 90s", [1980, 1990]), ("2000s", [2000]), ("2010s and 2020s", [2010, 2020])]

THEMES, ITEMS = [], []   # ITEMS[id] = one quizzable fact, tagged with its theme and stage
THEMES.append({"title": "The essentials", "kind": "essentials", "stages": [name for name, _ in ESSENTIAL_STAGES]})
for stage, (_, spec) in enumerate(ESSENTIAL_STAGES):
    ITEMS += [dict(it, theme=0, stage=stage) for it in essential_items(spec)]
for title, decades in DECADE_THEMES:
    THEMES.append({"title": title, "kind": "dates", "stages": [decade_name(d) for d in decades]})
    for stage, dec in enumerate(decades):
        ITEMS += [dict(it, theme=len(THEMES) - 1, stage=stage) for it in decade_items(dec)]
THEMES.append({"title": "Key terms", "kind": "terms", "stages": [name for name, _ in TERM_STAGES]})
for stage, (_, sections) in enumerate(TERM_STAGES):
    ITEMS += [dict(term_item(t, d, sec), theme=len(THEMES) - 1, stage=stage)
              for sec in sections for t, d in TERMS[sec]]
for theme_id, theme in enumerate(THEMES):
    theme["count"] = sum(1 for it in ITEMS if it["theme"] == theme_id)

QUESTION_TYPES = {
    "event": (lambda it: when_question(it["date"], it["event"]), lambda it: what_question(it["date"], it["event"])),
    "institution": (institution_when, institution_which),
    "term": (term_meaning, term_name),
    "person": (person_who, person_known_for),
}
NEW_PER_LEVEL = 12   # questions from the stage's own material
MAX_WRONG_REVIEW = 5  # earlier questions you got wrong that come back
MIN_REVIEW = 3        # earlier questions repeated even if you got none wrong


def make_question(item_id, review=False):
    item = ITEMS[item_id]
    q = random.choice(QUESTION_TYPES[item["kind"]])(item)
    q.update(id=item_id, review=review, fact=item["fact"])
    return q


@app.route("/")
def index():
    return render_template("index.html", themes=THEMES)


@app.route("/api/theme/<int:t>/stage/<int:n>")
def stage(t, n):
    """Questions for stage n of theme t, plus repeats from its earlier stages (wrong answers first)."""
    t = max(0, min(t, len(THEMES) - 1))
    n = max(0, min(n, len(THEMES[t]["stages"]) - 1))
    this_stage = [k for k, it in enumerate(ITEMS) if it["theme"] == t and it["stage"] == n]
    earlier = [k for k, it in enumerate(ITEMS) if it["theme"] == t and it["stage"] < n]

    wrong = {int(x) for x in request.args.get("wrong", "").split(",") if x.isdigit()}
    wrong_earlier = [k for k in earlier if k in wrong]
    review = random.sample(wrong_earlier, min(MAX_WRONG_REVIEW, len(wrong_earlier)))
    others = [k for k in earlier if k not in review]
    review += random.sample(others, min(len(others), max(0, MIN_REVIEW - len(review))))

    questions = [make_question(k) for k in random.sample(this_stage, min(NEW_PER_LEVEL, len(this_stage)))]
    questions += [make_question(k, review=True) for k in review]
    random.shuffle(questions)
    return jsonify(questions)


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def free_port(start=5050):
    """First free port from `start` upwards, so a second copy doesn't crash."""
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


if __name__ == "__main__":
    port = free_port()
    ip = lan_ip()
    print(f"\n  Local:   http://localhost:{port}")
    if ip:
        print(f"  Network: http://{ip}:{port}   (open this on your phone)\n")
    app.run(host="0.0.0.0", port=port, debug=False)
