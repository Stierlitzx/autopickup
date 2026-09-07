"""
SDU course registration helper (test/pet project).

Handles courses that have BOTH a lecture (type N) and practice (type P)
component, where the available practice sections depend on which lecture
section you picked (each lecture lists its own child practice sections).

Workflow:
  1. Reads courses_config.json for required courses + elective groups.
     An elective group (e.g. "Elective 10") can offer a choice between
     several *different* courses (INF 410 / CSS 314 / etc) -- you pick
     one course, then pick a lecture section, then a matching practice
     section for that lecture.
  2. Required courses attempt the same lecture+practice flow automatically,
     picking the first available lecture and its first available practice.
  3. Registration via add_course() uses the real confirmed "add" payload
     (action=AddCourse, tid, stud, secN/secP/secL, secSQ, derskod).

Setup:
  1. Log into https://my.sdu.edu.kz in your browser.
  2. DevTools > Network > any index.php request > Headers > Request Headers
     > Cookie > copy the full string into COOKIE_STRING below.
  3. Edit courses_config.json with your real course codes / muf_sq_id values.
  4. (Optional) Install the ntfy app on your phone (https://ntfy.sh), pick a
     unique topic name, and set NTFY_TOPIC below to get push notifications
     when seats open / registration succeeds or fails.
  5. Run: python sdu_course_reg.py
"""

import json
import random
import re
import time
from datetime import datetime

import requests

BASE_URL = "https://my.sdu.edu.kz/index.php"

# Paste your fresh Cookie header value here (rotate the session after testing).
COOKIE_STRING = ""  # replace me

# Optional: set a unique topic name (e.g. "aibek-css314-9f3k2") and install
# the ntfy app (https://ntfy.sh) on your phone, then subscribe to that same
# topic name there. Leave as None to disable notifications.
NTFY_TOPIC = None  # e.g. "aibek-css314-9f3k2"


def notify(title: str, message: str):
    """Send a push notification via ntfy.sh, if NTFY_TOPIC is set."""
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title},
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        print(f"[notify] failed to send notification: {e}")

HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://my.sdu.edu.kz",
    "Referer": "https://my.sdu.edu.kz/index.php?mod=course_reg",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
    "Cookie": COOKIE_STRING,
}


def list_sections(course_code: str, prog_code: str, year: str,
                   muf_sq_id: str, track: str = "TRACK0") -> dict:
    """Fetch available sections (raw API response) for a single course."""
    payload = {
        "ajx": "1",
        "mod": "course_reg",
        "action": "ShowAvailableAllSections",
        "dk": course_code,
        "pc": prog_code,
        "py": year,
        "track": track,
        "muf_sq_id": muf_sq_id,
    }
    resp = requests.post(BASE_URL, headers=HEADERS, data=payload, timeout=15)
    resp.raise_for_status()
    try:
        return resp.json()
    except requests.exceptions.JSONDecodeError:
        print(f"\n[!] Server did not return JSON for course_code={course_code!r}, "
              f"muf_sq_id={muf_sq_id!r}.")
        print(f"[!] Status: {resp.status_code}")
        print(f"[!] Response body (first 500 chars):\n{resp.text[:500]}")
        print("[!] This usually means the session cookie is missing/expired, "
              "or the muf_sq_id/course_code is wrong (e.g. still a PLACEHOLDER).\n")
        raise


def add_course(tid: str, stud_id: str, secSQ: str, derskod: str,
                secN: str = "", secP: str = "", secL: str = "") -> dict:
    """
    Submits the actual "add" request, confirmed against a real captured
    request:

      ajx=1&mod=course_reg&action=AddCourse&tid=<id>&stud=<student_id>
      &secN=<lecture_section_id>&secP=<practice_section_id>
      &secL=<lab_section_id>&secSQ=<muf_sq_id>&derskod=<course code>

    tid: the "id" query param from the course's registration URL
         (e.g. index.php?mod=course_reg&id=928) -- NOT the same as secSQ.
    secN/secP/secL: leave empty string "" if that component isn't used.
    """
    body = (
        f"ajx=1&mod=course_reg&action=AddCourse"
        f"&tid={tid}&stud={stud_id}"
        f"&secN={secN}&secP={secP}&secL={secL}"
        f"&secSQ={secSQ}&derskod={derskod}"
        f"&{int(time.time() * 1000)}"
    )
    post_headers = dict(HEADERS)
    resp = requests.post(BASE_URL, headers=post_headers, data=body.encode("utf-8"),
                          timeout=15)
    print(f"[add_course] HTTP status: {resp.status_code}")
    try:
        return resp.json()
    except requests.exceptions.JSONDecodeError:
        print(f"[add_course] Non-JSON response body (first 800 chars):\n"
              f"{resp.text[:800]}")
        return {"raw_text": resp.text}


def get_registered_courses(tid: str) -> dict:
    """
    Fetches the "basket" page (index.php?mod=course_reg&id=<tid>) and parses
    out each currently-registered course's drop ID ("did").

    Returns a dict keyed by course_code, e.g.:
      { "CSS 314": "6755800", "INF 414": "6755799", ... }
    """
    url = f"{BASE_URL}?mod=course_reg&id={tid}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    # Each basket row has a course-code cell, then (eventually) a drop link
    # like: href="?mod=course_reg&a=DropCourse&id=928&did=6755800"
    row_pattern = re.compile(
        r'<td align="center" class="clsTd">([A-Z]{2,4} \d{3})</td>'
        r'.*?did=(\d+)"',
        re.DOTALL,
    )

    courses = {}
    for course_code, did in row_pattern.findall(html):
        courses[course_code] = did
    return courses


def drop_course(tid: str, did: str) -> bool:
    """
    Drops a registered course via its "did" (from get_registered_courses).
    This is a GET request that 302-redirects back to the basket page on
    success; requests follows the redirect automatically.
    Returns True if the request completed without error.
    """
    url = f"{BASE_URL}?mod=course_reg&a=DropCourse&id={tid}&did={did}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    print(f"[drop_course] did={did} -> HTTP {resp.status_code}")
    return resp.status_code == 200


def parse_sections(raw: dict):
    """
    DATA2 is [ {lecture_sections}, {practice_sections}, [] ].
    Returns (lectures, practices) as lists of dicts. Each lecture dict
    includes 'practice_ids': the child practice section IDs (from the
    PRACTICE field) so we can filter practice options per lecture pick.
    """
    def to_entry(sec_id, sec):
        return {
            "id": sec_id,
            "code": f"{sec['DERS_KOD']}.{sec['SECTION']}",
            "type": sec["TYPE"],  # N = lecture, P = practice
            "teacher": sec["TEACHER"],
            "quota": int(sec["QUOTA"]),
            "count": int(sec["STUD_COUNT"]),
            "schedule": sec.get("SCHEDULE", ""),
            "practice_ids": [
                p.strip() for p in sec.get("PRACTICE", "").split(",") if p.strip()
            ],
        }

    groups = raw.get("DATA2", [])
    lectures = []
    practices = []
    if len(groups) > 0 and isinstance(groups[0], dict):
        lectures = [to_entry(sid, s) for sid, s in groups[0].items()]
    if len(groups) > 1 and isinstance(groups[1], dict):
        practices = [to_entry(sid, s) for sid, s in groups[1].items()]
    return lectures, practices


def has_seats(sec):
    return sec["count"] < sec["quota"]


def print_section_list(sections):
    for i, sec in enumerate(sections, start=1):
        seats_left = sec["quota"] - sec["count"]
        status = f"{seats_left} seats open" if seats_left > 0 else "FULL"
        print(f"  [{i}] {sec['code']} ({sec['type']}) - {sec['teacher']} "
              f"- {sec['schedule']} - {status}")


def pick_lecture_and_practice(course_code, prog_code, year, muf_sq_id, track,
                               interactive=True):
    """
    Fetches sections for one course and returns (lecture, practice) chosen,
    or (None, None) if nothing usable was found / user skipped.

    If interactive=True, prints options and prompts for a choice.
    If interactive=False, auto-picks the first available lecture and the
    first available practice section that belongs to it.
    """
    raw = list_sections(course_code, prog_code, year, muf_sq_id, track)
    lectures, practices = parse_sections(raw)

    if not lectures:
        print(f"No lecture sections found for {course_code}.")
        return None, None

    if interactive:
        print(f"\n--- {course_code}: Lecture sections ---")
        print_section_list(lectures)
        pick = input("Pick a lecture number (or 's' to skip): ").strip()
        if pick.lower() == "s":
            return None, None
        try:
            lecture = lectures[int(pick) - 1]
        except (ValueError, IndexError):
            print("Invalid choice, skipping.")
            return None, None
    else:
        lecture = next((s for s in lectures if has_seats(s)), None)
        if not lecture:
            print(f"No open lecture sections for {course_code}.")
            return None, None

    matching_practices = [p for p in practices if p["id"] in lecture["practice_ids"]]

    if not matching_practices:
        # Some courses might not have a practice component at all.
        return lecture, None

    if interactive:
        print(f"\n--- {course_code}: Practice sections for {lecture['code']} ---")
        print_section_list(matching_practices)
        pick = input("Pick a practice number (or 's' to skip): ").strip()
        if pick.lower() == "s":
            return lecture, None
        try:
            practice = matching_practices[int(pick) - 1]
        except (ValueError, IndexError):
            print("Invalid choice, no practice section selected.")
            return lecture, None
    else:
        practice = next((s for s in matching_practices if has_seats(s)), None)
        if not practice:
            print(f"No open practice sections for {course_code} "
                  f"under lecture {lecture['code']}.")

    return lecture, practice


def confirm_and_register(cfg, course_code, muf_sq_id, tid, lecture, practice):
    if lecture and not has_seats(lecture):
        print(f"WARNING: {lecture['code']} shows FULL "
              f"({lecture['count']}/{lecture['quota']}). Not registering.")
        return
    if practice and not has_seats(practice):
        print(f"WARNING: {practice['code']} shows FULL "
              f"({practice['count']}/{practice['quota']}). Not registering.")
        return

    codes = " + ".join(s["code"] for s in (lecture, practice) if s)
    print(f"Registering {course_code}: {codes}")

    try:
        result = add_course(
            tid=tid,
            stud_id=cfg["student_id"],
            secSQ=muf_sq_id,
            derskod=course_code,
            secN=lecture["id"] if lecture else "",
            secP=practice["id"] if practice else "",
        )
        print(f"Server response: {result}")
        if result.get("CODE") == "1":
            notify("Registered!", f"{course_code}: {codes}")
        else:
            notify("Registration failed", f"{course_code}: {codes}\n{result}")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        notify("Registration error", f"{course_code}: {codes}\n{e}")


def find_by_code(sections, code):
    return next((s for s in sections if s["code"] == code), None)


def drop_before_registering(cfg, tid, drop_course_code):
    """
    Looks up the registered course matching drop_course_code and drops it.
    Returns True if a matching registration was found and dropped
    successfully, False otherwise (e.g. nothing registered under that code).
    """
    registered = get_registered_courses(tid)
    did = registered.get(drop_course_code)
    if not did:
        print(f"[drop] No registered course found for "
              f"{drop_course_code!r} to drop.")
        return False
    print(f"Dropping current registration: {drop_course_code} (did={did})")
    ok = drop_course(tid, did)
    if ok:
        notify("Dropped course", f"{drop_course_code} (did={did})")
    return ok


def watch_and_register(cfg):
    """
    Polls a specific lecture (+ optional practice) section repeatedly until
    both show open seats, then attempts registration and stops.

    Config format (add a "watch" key to courses_config.json):
      "watch": {
        "course_code": "CSS 314",
        "muf_sq_id": "6580",
        "lecture_code": "CSS 314.02",
        "practice_code": "CSS 314.06",   # omit/null if no practice needed
        "drop_course_code": "CSS 451",   # optional: drop this before adding
        "min_interval_seconds": 5,
        "max_interval_seconds": 10
      }
    """
    watch = cfg["watch"]
    course_code = watch["course_code"]
    muf_sq_id = watch["muf_sq_id"]
    tid = watch["tid"]
    drop_course_code = watch.get("drop_course_code")
    lecture_code = watch["lecture_code"]
    practice_code = watch.get("practice_code")
    min_interval = watch.get("min_interval_seconds", 5)
    max_interval = watch.get("max_interval_seconds", 10)

    target = f"{lecture_code}" + (f" + {practice_code}" if practice_code else "")
    print(f"Watching for: {target}")
    print("Press Ctrl+C to stop.\n")

    attempt = 0
    try:
        while True:
            attempt += 1
            now = datetime.now().strftime("%H:%M:%S")
            try:
                raw = list_sections(course_code, cfg["prog_code"], cfg["year"],
                                     muf_sq_id, cfg["track"])
            except (requests.exceptions.RequestException,
                     requests.exceptions.JSONDecodeError) as e:
                print(f"[{now}] attempt {attempt}: request failed ({e}), "
                      f"will retry")
                time.sleep(random.uniform(min_interval, max_interval))
                continue

            lectures, practices = parse_sections(raw)
            lecture = find_by_code(lectures, lecture_code)
            practice = find_by_code(practices, practice_code) if practice_code else None

            if not lecture:
                print(f"[{now}] attempt {attempt}: lecture "
                      f"{lecture_code} not found in response")
            else:
                lecture_status = (f"{lecture['quota'] - lecture['count']} open"
                                   if has_seats(lecture) else "FULL")
                practice_status = ""
                if practice_code:
                    if practice:
                        practice_status = (
                            f", practice {practice_code}: "
                            f"{'FULL' if not has_seats(practice) else str(practice['quota'] - practice['count']) + ' open'}"
                        )
                    else:
                        practice_status = f", practice {practice_code}: not found"

                print(f"[{now}] attempt {attempt}: lecture {lecture_code}: "
                      f"{lecture_status}{practice_status}")

                lecture_ok = has_seats(lecture)
                practice_ok = (not practice_code) or (practice and has_seats(practice))

                if lecture_ok and practice_ok:
                    print("\nSeats available! Attempting registration...")
                    notify("Seats open!", f"{course_code}: {lecture_code}"
                           + (f" + {practice_code}" if practice_code else "")
                           + " -- attempting to register now")

                    if drop_course_code:
                        dropped = drop_before_registering(cfg, tid, drop_course_code)
                        if not dropped:
                            print("Drop failed or nothing to drop -- "
                                  "NOT registering the new course to avoid "
                                  "ending up with neither. Resuming watch.")
                            time.sleep(random.uniform(min_interval, max_interval))
                            continue

                    confirm_and_register(cfg, course_code, muf_sq_id, tid,
                                          lecture, practice)
                    return

            time.sleep(random.uniform(min_interval, max_interval))
    except KeyboardInterrupt:
        print("\nStopped watching.")


def handle_required(cfg):
    for course in cfg["required"]:
        lecture, practice = pick_lecture_and_practice(
            course["course_code"], cfg["prog_code"], cfg["year"],
            course["muf_sq_id"], cfg["track"], interactive=False,
        )
        if lecture:
            confirm_and_register(cfg, course["course_code"],
                                  course["muf_sq_id"], course["tid"],
                                  lecture, practice)


def handle_electives(cfg):
    for group in cfg["electives"]:
        print(f"\n=== Elective group: {group['group_name']} ===")
        # muf_sq_id (secSQ) and tid belong to the elective SLOT, not the
        # individual course -- all options in this group share both.
        muf_sq_id = group["muf_sq_id"]
        tid = group["tid"]
        options = group["options"]  # list of course_code strings
        for i, course_code in enumerate(options, start=1):
            print(f"  [{i}] {course_code}")
        pick = input("Which course do you want for this elective "
                     "(or 's' to skip)? ").strip()
        if pick.lower() == "s":
            continue
        try:
            chosen_course_code = options[int(pick) - 1]
        except (ValueError, IndexError):
            print("Invalid choice, skipping group.")
            continue

        lecture, practice = pick_lecture_and_practice(
            chosen_course_code, cfg["prog_code"], cfg["year"],
            muf_sq_id, cfg["track"], interactive=True,
        )
        if lecture:
            confirm_and_register(cfg, chosen_course_code, muf_sq_id, tid,
                                  lecture, practice)


if __name__ == "__main__":
    with open("courses_config.json") as f:
        cfg = json.load(f)

    if cfg.get("watch"):
        watch_and_register(cfg)
    else:
        handle_required(cfg)
        handle_electives(cfg)