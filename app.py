"""EU History Dates: a Duolingo-style multiple-choice quiz (1945-1969).

Run:  python3 app.py   then open http://localhost:5050
(Phones on the same Wi-Fi can use the "Network" address printed at start-up.)
"""
import random
import re
import socket

from flask import Flask, jsonify, render_template, request

from events import EVENTS

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


def when_question(date, event):
    """'When did this happen?' -> pick the date."""
    wrong = nearby(DATES, sort_key, sort_key(date), lambda d: d == date)
    options = wrong + [date]
    random.shuffle(options)
    return {"type": "when", "prompt": "When did this happen?", "subject": event,
            "options": options, "answer": options.index(date)}


def what_question(date, event):
    """'What happened in <date>?' -> pick the event."""
    wrong = nearby(EVENTS, lambda e: sort_key(e[0]), sort_key(date),
                   lambda e: e[0] == date)
    options = [e for _, e in wrong] + [event]
    random.shuffle(options)
    return {"type": "what", "prompt": "What happened in…", "subject": date,
            "options": options, "answer": options.index(event)}


def year_of(date):
    return int(re.search(r"\d{4}", date).group())


# One level per decade: the 1940s, the 1950s, the 1960s
DECADES = sorted({year_of(d) // 10 * 10 for d, _ in EVENTS})
LEVELS = [{"decade": dec, "name": f"the {dec}s"} for dec in DECADES]
NEW_PER_LEVEL = 12   # questions from the level's own decade
MAX_WRONG_REVIEW = 5  # earlier questions you got wrong that come back
MIN_REVIEW = 3        # earlier questions repeated even if you got none wrong


def make_question(event_id, review=False):
    date, event = EVENTS[event_id]
    q = random.choice((when_question, what_question))(date, event)
    q.update(id=event_id, review=review, fact=f"{date}: {event}")
    return q


@app.route("/")
def index():
    return render_template("index.html", levels=LEVELS)


@app.route("/api/level/<int:n>")
def level(n):
    """Questions for level n, plus repeats from earlier decades (wrong answers first)."""
    n = max(0, min(n, len(LEVELS) - 1))
    decade = LEVELS[n]["decade"]
    this_decade = [k for k, (d, _) in enumerate(EVENTS) if year_of(d) // 10 * 10 == decade]
    earlier = [k for k, (d, _) in enumerate(EVENTS) if year_of(d) < decade]

    wrong = {int(x) for x in request.args.get("wrong", "").split(",") if x.isdigit()}
    wrong_earlier = [k for k in earlier if k in wrong]
    review = random.sample(wrong_earlier, min(MAX_WRONG_REVIEW, len(wrong_earlier)))
    others = [k for k in earlier if k not in review]
    review += random.sample(others, min(len(others), max(0, MIN_REVIEW - len(review))))

    questions = [make_question(k) for k in random.sample(this_decade, min(NEW_PER_LEVEL, len(this_decade)))]
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
