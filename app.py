"""EU Quiz: a Duolingo-style multiple-choice quiz on the dates of European integration
(1945-2025), the founding of the EU institutions and key economics terms.

All questions live in data/quiz.json, which can be edited at /admin.

Run:  ADMIN_PASSWORD=choose-one python3 app.py   then open http://localhost:5050
(Phones on the same Wi-Fi can use the "Network" address printed at start-up.)

On Railway set these variables:
  ADMIN_PASSWORD  password for the /admin editor (the editor is switched off without it)
  DATA_DIR        folder on a Railway volume, e.g. /data, so edits survive redeploys
"""
import hashlib
import hmac
import json
import os
import random
import re
import shutil
import socket
import time

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEED_FILE = os.path.join(BASE_DIR, "data", "quiz.json")
DATA_DIR = os.environ.get("DATA_DIR")
DATA_FILE = os.path.join(DATA_DIR, "quiz.json") if DATA_DIR else SEED_FILE
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

app = Flask(__name__)
# Derived from the password, so every gunicorn worker agrees and changing the password logs you out
app.secret_key = hashlib.sha256(f"eu-quiz-session:{ADMIN_PASSWORD}".encode()).hexdigest()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  SESSION_COOKIE_SECURE=bool(os.environ.get("RAILWAY_ENVIRONMENT")))

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
KINDS = {  # the fields each type of row has
    "event": ["date", "event"],
    "institution": ["date", "name", "note"],
    "term": ["term", "definition", "section"],
    "person": ["name", "known_for"],
    "explain": ["question", "answer"],
}
PLURALS = {"event": "events", "institution": "institutions", "term": "terms", "person": "people", "explain": "questions"}
DATED_KINDS = {"event", "institution"}
THEME_KINDS = {"dates", "terms", "essentials"}

NEW_PER_LEVEL = 12   # questions from the stage's own material
MAX_WRONG_REVIEW = 5  # earlier questions you got wrong that come back
MIN_REVIEW = 3        # earlier questions repeated even if you got none wrong


# ---------- dates ----------

def sort_key(date):
    """Months since Jan 1945 for the start of a date like 'July - August 1945'."""
    year = int(re.search(r"\d{4}", date).group())
    month = next((i for i, m in enumerate(MONTHS) if m in date.lower()), 0)
    return (year - 1945) * 12 + month


def date_from_key(key):
    return f"{MONTHS[key % 12].title()} {1945 + key // 12}"


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


def nearby(items, key_of, target_key, exclude, k=3, pool=9):
    """Pick k distractors from the `pool` items closest in time (keeps it tricky)."""
    candidates = [x for x in items if not exclude(x)]
    candidates.sort(key=lambda x: abs(key_of(x) - target_key) + random.random())
    return random.sample(candidates[:pool], k)


def fact_of(item):
    return {
        "event": lambda: f"{item['date']}: {item['event']}",
        "institution": lambda: f"{item['date']}: {item['name']} founded ({item['note']})",
        "term": lambda: f"{item['term']}: {item['definition']}",
        "person": lambda: f"{item['name']}: {item['known_for']}",
        "explain": lambda: f"{item['question']} {item['answer']}",
    }[item["kind"]]()


def fact_parts(item):
    """(bold part, rest) for the end-of-quiz review list."""
    if item["kind"] == "explain":
        return item["question"], item["answer"]
    head, _, rest = item["fact"].partition(": ")
    return head, rest


def shuffled(wrong, right, **question):
    options = wrong + [right]
    random.shuffle(options)
    return dict(question, options=options, answer=options.index(right))


class Quiz:
    """Everything the game needs, built from the contents of quiz.json."""

    def __init__(self, data):
        self.data = data
        self.themes, self.items = [], []   # items[id] = one quizzable fact
        for t, theme in enumerate(data["themes"]):
            for s, stage in enumerate(theme["stages"]):
                self.items += [dict(it, theme=t, stage=s, fact=fact_of(it)) for it in stage["items"]]
            self.themes.append({"title": theme["title"], "kind": theme["kind"],
                                "stages": [s["name"] for s in theme["stages"]],
                                "count": sum(len(s["items"]) for s in theme["stages"])})
        # Pools for wrong options, shared across all themes
        rows = lambda kind, fields: list(dict.fromkeys(tuple(it[f] for f in fields)
                                                       for it in self.items if it["kind"] == kind))
        self.events = rows("event", ["date", "event"])
        self.dates = sorted({d for d, _ in self.events}, key=sort_key)
        self.institutions = list({n: (d, n) for d, n in rows("institution", ["date", "name"])}.values())
        self.people = list({n: (n, k) for n, k in rows("person", ["name", "known_for"])}.values())
        self.terms = list({t: (t, d, s) for t, d, s in rows("term", ["term", "definition", "section"])}.values())
        self.question_types = {
            "event": (self.event_when, self.event_what),
            "institution": (self.institution_when, self.institution_which),
            "term": (self.term_meaning, self.term_name),
            "person": (self.person_who, self.person_known_for),
            "explain": (self.explain,),
        }

    # ----- dated events -----
    def event_when(self, it):
        wrong = nearby(self.dates, sort_key, sort_key(it["date"]), lambda d: clashes(d, it["date"]))
        return shuffled(wrong, it["date"], type="when", prompt="When did this happen?", subject=it["event"])

    def event_what(self, it):
        wrong = nearby(self.events, lambda e: sort_key(e[0]), sort_key(it["date"]),
                       lambda e: clashes(e[0], it["date"]))
        return shuffled([e for _, e in wrong], it["event"], type="what", prompt="What happened in…",
                        subject=it["date"])

    # ----- EU institutions -----
    def institution_when(self, it):
        date = it["date"]
        others = sorted({d for d, _ in self.institutions if not clashes(d, date)})
        nearby_dates = {date_from_key(sort_key(date) + random.choice([-1, 1]) * random.randint(2, 30))
                        for _ in range(12)}
        nearby_dates = sorted(d for d in nearby_dates if not clashes(d, date))
        wrong = random.sample(others, min(1, len(others)))
        wrong += random.sample([d for d in nearby_dates if d not in wrong], 3 - len(wrong))
        return shuffled(wrong, date, type="when", prompt="When was this founded?",
                        subject=f"{it['name']} ({it['note']})")

    def institution_which(self, it):
        wrong = random.sample([n for d, n in self.institutions if not clashes(d, it["date"])], 3)
        return shuffled(wrong, it["name"], type="what", prompt="Which EU institution was founded in…",
                        subject=it["date"])

    # ----- key terms -----
    def term_distractors(self, it):
        """3 other terms, from the same section where possible (harder to guess)."""
        same = [x for x in self.terms if x[2] == it["section"] and x[0] != it["term"]]
        rest = [x for x in self.terms if x[2] != it["section"] and x[0] != it["term"]]
        picks = random.sample(same, min(3, len(same)))
        return picks + random.sample(rest, 3 - len(picks))

    def term_meaning(self, it):
        return shuffled([d for _, d, _ in self.term_distractors(it)], it["definition"], type="what",
                        prompt="What does this term mean?", subject=it["term"])

    def term_name(self, it):
        return shuffled([t for t, _, _ in self.term_distractors(it)], it["term"], type="term",
                        prompt="Which term matches this definition?", subject=it["definition"])

    # ----- people -----
    def person_who(self, it):
        wrong = random.sample([n for n, _ in self.people if n != it["name"]], 3)
        return shuffled(wrong, it["name"], type="when", prompt="Who was this?", subject=it["known_for"])

    def person_known_for(self, it):
        wrong = random.sample([k for n, k in self.people if n != it["name"]], 3)
        return shuffled(wrong, it["known_for"], type="what", prompt="What is this person known for?",
                        subject=it["name"])

    # ----- explain in your own words (typed, then self-marked) -----
    def explain(self, it):
        return {"type": "explain", "prompt": "Explain in your own words", "subject": it["question"],
                "answer": it["answer"], "options": []}

    # ----- building a stage -----
    def question(self, item_id, review=False):
        it = self.items[item_id]
        q = random.choice(self.question_types[it["kind"]])(it)
        title, text = fact_parts(it)
        q.update(id=item_id, review=review, fact=it["fact"], fact_title=title, fact_text=text)
        return q

    def stage(self, t, n, wrong):
        """Questions for stage n of theme t, plus repeats from its earlier stages (wrong answers first)."""
        t = max(0, min(t, len(self.themes) - 1))
        n = max(0, min(n, len(self.themes[t]["stages"]) - 1))
        this_stage = [k for k, it in enumerate(self.items) if it["theme"] == t and it["stage"] == n]
        earlier = [k for k, it in enumerate(self.items) if it["theme"] == t and it["stage"] < n]

        wrong_earlier = [k for k in earlier if k in wrong]
        review = random.sample(wrong_earlier, min(MAX_WRONG_REVIEW, len(wrong_earlier)))
        others = [k for k in earlier if k not in review]
        review += random.sample(others, min(len(others), max(0, MIN_REVIEW - len(review))))

        questions = [self.question(k) for k in random.sample(this_stage, min(NEW_PER_LEVEL, len(this_stage)))]
        questions += [self.question(k, review=True) for k in review]
        random.shuffle(questions)
        return questions


# ---------- loading and saving quiz.json ----------

def validate(data):
    """Return a plain-English problem with the data, or None if it is fine."""
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list) or not data["themes"]:
        return "There must be at least one quiz."
    for theme in data["themes"]:
        title = str(theme.get("title", "")).strip()
        if not title:
            return "Every quiz needs a name."
        if theme.get("kind") not in THEME_KINDS:
            return f"Quiz '{title}' has an unknown type."
        if not theme.get("stages"):
            return f"Quiz '{title}' needs at least one stage."
        for s, stage in enumerate(theme["stages"]):
            where = f"'{title}', stage {s + 1}"
            if not str(stage.get("name", "")).strip():
                return f"{where} needs a name."
            if not stage.get("items"):
                return f"{where} has no questions."
            for r, it in enumerate(stage["items"]):
                fields = KINDS.get(it.get("kind"))
                if not fields:
                    return f"{where}, row {r + 1}: unknown row type."
                for f in fields:
                    if not isinstance(it.get(f), str) or not it[f].strip():
                        return f"{where}, row {r + 1}: the '{f.replace('_', ' ')}' box is empty."
                if it["kind"] in DATED_KINDS and not re.search(r"\b\d{4}\b", it["date"]):
                    return f"{where}, row {r + 1}: the date '{it['date']}' needs a 4-digit year."
    # Make sure every row can actually become a question (enough wrong options to choose from)
    quiz = Quiz(data)
    for k, it in enumerate(quiz.items):
        for make in quiz.question_types[it["kind"]]:
            try:
                q = make(it)
                assert it["kind"] == "explain" or len(set(q["options"])) == 4
            except (ValueError, AssertionError, IndexError):
                theme = data["themes"][it["theme"]]
                return (f"'{theme['title']}', stage {it['stage'] + 1}: '{it['fact'][:60]}' can't be turned into "
                        f"a question, because there aren't enough other {PLURALS[it['kind']]} to use as wrong answers.")
    return None


def clean(data, applied=()):
    """Keep only the known fields, with surrounding spaces trimmed."""
    return {"applied": list(applied), "themes": [
        {"title": t["title"].strip(), "kind": t["kind"],
         "stages": [{"name": s["name"].strip(),
                     "items": [{"kind": it["kind"], **{f: it[f].strip() for f in KINDS[it["kind"]]}}
                               for it in s["items"]]}
                    for s in t["stages"]]}
        for t in data["themes"]]}


# One-off additions to the questions, e.g. data/explain-v1.json. Each is merged into the live
# quiz.json once (recorded under "applied"), so edits made in /admin are never overwritten.
MIGRATIONS = ["explain-v1"]


def apply_migrations(data):
    changed = False
    for name in MIGRATIONS:
        if name in data.get("applied", []):
            continue
        with open(os.path.join(BASE_DIR, "data", f"{name}.json"), encoding="utf-8") as f:
            patch = json.load(f)
        for block in patch["add"]:
            theme = next((t for t in data["themes"] if t["title"].lower() == block["theme"].lower()), None)
            stage = theme and next((s for s in theme["stages"] if s["name"].lower() == block["stage"].lower()), None)
            if not stage:
                continue   # quiz or stage renamed/deleted in /admin: skip rather than guess
            have = {json.dumps(it, sort_keys=True) for it in stage["items"]}
            stage["items"] += [it for it in block["items"] if json.dumps(it, sort_keys=True) not in have]
        data.setdefault("applied", []).append(name)
        changed = True
    return changed


_quiz, _loaded_mtime = None, None


def quiz():
    """The current quiz, reloaded if quiz.json changed (e.g. saved by another worker)."""
    global _quiz, _loaded_mtime
    if not os.path.exists(DATA_FILE):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        shutil.copyfile(SEED_FILE, DATA_FILE)   # first run on a new volume
    mtime = os.path.getmtime(DATA_FILE)
    if _quiz is None or mtime != _loaded_mtime:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if apply_migrations(data):
            save(data)
            mtime = os.path.getmtime(DATA_FILE)
        _quiz, _loaded_mtime = Quiz(data), mtime
    return _quiz


def save(data):
    tmp = f"{DATA_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


# ---------- quiz pages ----------

@app.route("/")
def index():
    return render_template("index.html", themes=quiz().themes)


@app.route("/api/theme/<int:t>/stage/<int:n>")
def stage(t, n):
    q = quiz()
    wrong = {int(x) for x in request.args.get("wrong", "").split(",") if x.isdigit() and int(x) < len(q.items)}
    return jsonify(q.stage(t, n, wrong))


# ---------- question editor (/admin) ----------

def logged_in():
    return bool(ADMIN_PASSWORD) and session.get("admin") is True


@app.before_request
def admin_guard():
    if request.path.startswith("/admin"):
        if not ADMIN_PASSWORD:
            abort(404)   # editor switched off until a password is set
        if request.endpoint not in ("admin", "admin_login") and not logged_in():
            abort(401)


@app.route("/admin")
def admin():
    if not logged_in():
        return render_template("login.html", error=None)
    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
    return render_template("admin.html", kinds=KINDS, unsaved_warning=on_railway and not DATA_DIR)


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if hmac.compare_digest(request.form.get("password", "").encode(), ADMIN_PASSWORD.encode()):
        session.clear()
        session["admin"] = True
        session.permanent = True
        return redirect(url_for("admin"))
    time.sleep(1)   # slow down guessing
    return render_template("login.html", error="That password isn't right."), 401


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin"))


@app.route("/admin/api/data", methods=["GET", "POST"])
def admin_data():
    if request.method == "GET":
        return jsonify(quiz().data)
    data = request.get_json(silent=True)   # JSON only, so other sites can't post forms here
    problem = validate(data)
    if problem:
        return jsonify(ok=False, error=problem), 400
    data = clean(data, applied=quiz().data.get("applied", []))
    save(data)
    return jsonify(ok=True, count=sum(len(s["items"]) for t in data["themes"] for s in t["stages"]))


@app.route("/admin/download")
def admin_download():
    quiz()
    return send_file(DATA_FILE, as_attachment=True, download_name="eu-quiz-questions.json")


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
        print(f"  Network: http://{ip}:{port}   (open this on your phone)")
    print(f"  Editor:  http://localhost:{port}/admin" if ADMIN_PASSWORD
          else "  Editor:  off (start with ADMIN_PASSWORD=... to switch it on)")
    print()
    app.run(host="0.0.0.0", port=port, debug=False)
