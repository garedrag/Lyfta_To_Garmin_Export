#!/usr/bin/env python3
"""One-file GUI app for syncing Lyfta/Strava gym workouts to Garmin Connect."""

import json
import os
import queue
import csv
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from tkinter import BOTH, DISABLED, END, NORMAL, W
from tkinter import Button, Entry, Frame, Label, StringVar, Tk, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText
from urllib.parse import urlencode

if sys.version_info < (3, 8):
    sys.exit("Python 3.8 or newer is required.")

try:
    import requests
    from garminconnect import Garmin
    from fitparse import FitFile
except ImportError:
    sys.exit("Missing dependency. Run: python -m pip install requests garminconnect fitparse")


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
STRAVA_TOKENS_FILE = ROOT / "strava_tokens.json"
SYNCED_IDS_FILE = ROOT / "synced_ids.json"
GARMIN_TOKENS_DIR = ROOT / "garmin_tokens"
LOG_DIR = ROOT / "logs"
DAILY_LOG_FILE = LOG_DIR / "daily-sync.log"
TASK_NAME = "LyftaToGarminDailySync"
RUN_DAILY_SYNC_BAT = ROOT / "run_daily_sync.bat"

STRENGTH_TYPES = {"WeightTraining", "Workout", "Crossfit", "Pilates", "Yoga"}
FIT_EPOCH = 631065600

ENUM = 0x00
UINT16 = 0x84
UINT32 = 0x86
STRING = 0x07
UINT8 = 0x02
SPORT_TRAINING = 10
SUB_STRENGTH = 20
SUB_YOGA = 43
SUB_PILATES = 44
SET_ACTIVE = 1
UNKNOWN_EXERCISE = 65534


def read_env_file():
    values = {}
    if not ENV_FILE.exists():
        return values
    for raw_line in ENV_FILE.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def write_env_file(values):
    ENV_FILE.write_text("\n".join([
        "STRAVA_CLIENT_ID={}".format(values.get("STRAVA_CLIENT_ID", "")),
        "STRAVA_CLIENT_SECRET={}".format(values.get("STRAVA_CLIENT_SECRET", "")),
        "",
        "GARMIN_EMAIL={}".format(values.get("GARMIN_EMAIL", "")),
        "GARMIN_PASSWORD={}".format(values.get("GARMIN_PASSWORD", "")),
        "",
        "STRAVA_CALLBACK_URL={}".format(values.get("STRAVA_CALLBACK_URL", "http://localhost")),
        "LYFTA_CSV={}".format(values.get("LYFTA_CSV", "WorkoutData.csv")),
        "DAYS_BACK={}".format(values.get("DAYS_BACK", "30")),
        "USER_WEIGHT_KG={}".format(values.get("USER_WEIGHT_KG", "")),
        "STRENGTH_MET={}".format(values.get("STRENGTH_MET", "5.0")),
        "DAILY_SYNC_TIME={}".format(values.get("DAILY_SYNC_TIME", "08:00")),
        "",
    ]))


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2))


def fit_crc(data):
    table = [
        0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
    ]
    crc = 0
    for byte in data:
        crc = (crc >> 4) ^ table[(crc ^ byte) & 0xF]
        crc = (crc >> 4) ^ table[(crc ^ (byte >> 4)) & 0xF]
    return crc


def def_msg(local_num, global_num, fields):
    header = bytes([0x40 | local_num])
    body = struct.pack("<BBHB", 0, 0, global_num, len(fields))
    for field_def_num, size, base_type in fields:
        body += struct.pack("BBB", field_def_num, size, base_type)
    return header + body


def data_msg(local_num, *pairs):
    return bytes([local_num & 0x0F]) + b"".join(struct.pack(fmt, val) for fmt, val in pairs)


def data_msg_chunks(local_num, *chunks):
    return bytes([local_num & 0x0F]) + b"".join(chunks)


def fit_string(value, size):
    raw = str(value or "").encode("utf-8", errors="replace")
    if len(raw) >= size:
        raw = raw[:size - 1]
    return raw + b"\x00" * (size - len(raw))


def parse_duration(value):
    parts = [int(part) for part in (value or "0:0:0").split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    return parts[0] if parts else 0


def parse_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return float(text)


def parse_int(value):
    parsed = parse_float(value)
    return None if parsed is None else int(round(parsed))


def exercise_category_for_name(name):
    text = (name or "").lower()
    rules = [
        (0, ("bench", "chest press")),
        (1, ("calf",)),
        (7, ("curl", "biceps")),
        (8, ("deadlift",)),
        (9, ("fly", "flye", "pec deck")),
        (14, ("lateral raise", "side raise")),
        (15, ("leg curl", "hamstring curl")),
        (16, ("leg raise",)),
        (17, ("lunge",)),
        (19, ("plank",)),
        (21, ("pull up", "pull-up", "chin up", "chin-up", "pulldown", "pull down")),
        (22, ("push up", "push-up")),
        (23, ("row",)),
        (24, ("shoulder press", "overhead press", "military press")),
        (26, ("shrug",)),
        (28, ("squat", "leg press")),
        (30, ("triceps", "pushdown", "extension")),
        (5, ("ab ", "abs", "core")),
    ]
    for category, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category
    return 29


def exercise_subtype_for_name(name, category):
    text = (name or "").lower()
    rules = [
        (0, 1, ("bench press",)),
        (0, 4, ("close-grip bench", "close grip bench")),
        (0, 8, ("incline bench press",)),
        (0, 9, ("dumbbell incline bench", "incline dumbbell")),
        (0, 6, ("dumbbell bench",)),
        (7, 19, ("preacher curl",)),
        (7, 3, ("barbell curl",)),
        (7, 16, ("hammer curl",)),
        (7, 33, ("dumbbell seated curl", "seated curl")),
        (7, 0, ("alternate biceps curl", "alternating")),
        (9, 0, ("cable standing fly", "cable fly", "fly")),
        (14, 11, ("lateral raise", "one arm lateral raise")),
        (14, 5, ("face pull",)),
        (15, 0, ("leg curl",)),
        (16, 0, ("captains chair", "straight leg raise")),
        (17, 7, ("bulgarian split squat",)),
        (21, 25, ("wide-grip lat pulldown", "wide grip lat pulldown")),
        (21, 13, ("lat pulldown", "lateral pulldown", "pulldown")),
        (21, 38, ("assisted pull-up", "assisted pull up", "pull-up", "pull up")),
        (23, 28, ("t-bar row", "t bar row")),
        (23, 18, ("seated row", "v bar", "v-bar")),
        (23, 1, ("standing row",)),
        (24, 4, ("shoulder press", "military press", "behind neck press")),
        (24, 17, ("dumbbell seated shoulder press", "seated dumbbell shoulder")),
        (28, 0, ("leg press",)),
        (28, 61, ("full squat", "squat")),
        (30, 39, ("triceps pushdown", "pushdown")),
        (30, 13, ("skull crusher", "lying triceps extension")),
        (30, 0, ("triceps dip", "dip")),
        (6, 64, ("standing crunch",)),
        (6, 28, ("kneeling crunch",)),
        (13, 30, ("hyperextension",)),
        (5, 1, ("dead bug",)),
    ]
    for rule_category, subtype, keywords in rules:
        if rule_category == category and any(keyword in text for keyword in keywords):
            return subtype
    return UNKNOWN_EXERCISE


def rest_seconds_for_exercise_category(category):
    large_muscle_categories = {
        0,   # bench press / chest press
        8,   # deadlift
        9,   # fly / chest
        15,  # leg curl / hamstrings
        17,  # lunge
        21,  # pull-up / pulldown
        23,  # row
        28,  # squat / leg press
    }
    return 180 if category in large_muscle_categories else 120


def planned_sets_duration_seconds(sets):
    if not sets:
        return 0
    total = 0
    for index, set_info in enumerate(sets):
        category = exercise_category_for_name(set_info.get("exercise", ""))
        total += 60
        if index < len(sets) - 1:
            total += rest_seconds_for_exercise_category(category)
    return total


def unique_exercises(sets):
    exercises = []
    indexes = {}
    for set_info in sets:
        name = set_info.get("exercise", "").strip() or "Exercise"
        if name not in indexes:
            indexes[name] = len(exercises)
            exercises.append({
                "name": name,
                "category": exercise_category_for_name(name),
                "subtype": exercise_subtype_for_name(name, exercise_category_for_name(name)),
                "index": indexes[name],
            })
    return exercises, indexes


def load_lyfta_workouts(csv_path):
    path = Path(csv_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return []

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        grouped = {}

        for raw in reader:
            row = {key.strip(): value for key, value in raw.items()}
            date_text = row.get("Date", "").strip()
            if not date_text:
                continue

            started_at = datetime.strptime(date_text, "%Y-%m-%d %H:%M:%S")
            key = (row.get("Title", "").strip(), started_at, row.get("Duration", "").strip())
            workout = grouped.setdefault(key, {
                "title": row.get("Title", "").strip() or "Strength Training",
                "started_at": started_at,
                "duration": parse_duration(row.get("Duration", "")),
                "sets": [],
            })
            reps = parse_int(row.get("Reps"))
            weight = parse_float(row.get("Weight"))
            if reps is None and weight is None:
                continue
            workout["sets"].append({
                "exercise": row.get("Exercise", "").strip(),
                "reps": reps or 0,
                "weight": weight or 0.0,
                "set_type": row.get("Set Type", "").strip(),
            })

    return sorted(grouped.values(), key=lambda item: item["started_at"])


def match_lyfta_workout(activity, lyfta_workouts):
    candidates = []
    for key in ("start_date_local", "start_date"):
        value = activity.get(key)
        if not value:
            continue
        try:
            parsed = datetime.strptime(value.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            candidates.append(parsed.replace(tzinfo=None))
        except ValueError:
            pass

    if not candidates:
        return None

    best = None
    best_delta = None
    for workout in lyfta_workouts:
        for candidate in candidates:
            delta = abs((workout["started_at"] - candidate).total_seconds())
            if best_delta is None or delta < best_delta:
                best = workout
                best_delta = delta

    if best is not None and best_delta is not None and best_delta <= 6 * 3600:
        return best
    return None


def build_fit(activity, lyfta_workout=None, garmin_source=None):
    start_dt = datetime.strptime(activity["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    start_fit = int(start_dt.timestamp()) - FIT_EPOCH
    elapsed = int(activity.get("elapsed_time", 0) or 0)
    if lyfta_workout and lyfta_workout.get("duration"):
        elapsed = lyfta_workout["duration"]
    if elapsed <= 0 and lyfta_workout and lyfta_workout.get("sets"):
        elapsed = planned_sets_duration_seconds(lyfta_workout["sets"])
    end_fit = start_fit + elapsed
    duration_ms = elapsed * 1000

    sport_type = activity.get("sport_type") or activity.get("type", "WeightTraining")
    sub_sport = {"Yoga": SUB_YOGA, "Pilates": SUB_PILATES}.get(sport_type, SUB_STRENGTH)
    calories = int(activity.get("calories", 0) or 0)
    if garmin_source and garmin_source.get("calories"):
        calories = int(garmin_source["calories"])

    has_sets = bool(lyfta_workout and lyfta_workout.get("sets"))
    has_hr = bool(garmin_source and garmin_source.get("hr_offsets"))

    # local 0 = file_id (global 0)
    # local 1 = event   (global 21)
    # local 2 = session (global 18)
    # local 3 = activity(global 34)
    # local 4 = lap     (global 19)  — required by Garmin Connect to display set data
    # local 5 = set            (global 225) — only defined when CSV sets are present
    # local 6 = exercise_title (global 264) — text exercise names linked from sets
    # local 7 = record         (global 20)  — heart-rate samples from Garmin source activity
    definitions = [
        def_msg(0, 0, [(0, 1, ENUM), (4, 4, UINT32)]),
        def_msg(1, 21, [(253, 4, UINT32), (0, 1, ENUM), (1, 1, ENUM)]),
        def_msg(2, 18, [
            (253, 4, UINT32), (2, 4, UINT32), (5, 1, ENUM), (6, 1, ENUM),
            (7, 4, UINT32), (8, 4, UINT32), (11, 2, UINT16), (28, 1, ENUM),
            (1, 2, UINT16),  # num_laps
        ]),
        def_msg(3, 34, [
            (253, 4, UINT32), (0, 4, UINT32), (1, 2, UINT16),
            (2, 1, ENUM), (3, 1, ENUM), (4, 1, ENUM),
        ]),
        def_msg(4, 19, [
            (253, 4, UINT32), (2, 4, UINT32), (7, 4, UINT32), (8, 4, UINT32),
            (5, 1, ENUM), (6, 1, ENUM), (0, 2, UINT16),
        ]),
    ]

    if has_sets:
        definitions.append(def_msg(5, 225, [
            (254, 4, UINT32),  # timestamp
            (6,   4, UINT32),  # start_time
            (0,   4, UINT32),  # duration, ms
            (3,   2, UINT16),  # repetitions
            (4,   2, UINT16),  # weight, kg * 16
            (5,   1, ENUM),    # set_type
            (7,   1, ENUM),    # category
            (8,   2, UINT16),  # category_subtype / exercise_name
            (10,  2, UINT16),  # message_index
            (11,  2, UINT16),  # wkt_step_index, links to exercise_title
        ]))
        definitions.append(def_msg(6, 264, [
            (254, 2, UINT16),  # message_index
            (0,   1, ENUM),    # exercise_category
            (1,   2, UINT16),  # exercise_name
            (2,   64, STRING), # wkt_step_name
        ]))

    if has_hr:
        definitions.append(def_msg(7, 20, [
            (253, 4, UINT32), # timestamp
            (3,   1, UINT8),  # heart_rate
        ]))

    set_records = []
    exercise_title_records = []
    if has_sets:
        exercises, exercise_indexes = unique_exercises(lyfta_workout["sets"])
        for exercise in exercises:
            exercise_title_records.append(data_msg_chunks(6,
                struct.pack("<H", exercise["index"]),
                struct.pack("<B", exercise["category"]),
                struct.pack("<H", exercise["subtype"]),
                fit_string(exercise["name"], 64),
            ))

        set_count = len(lyfta_workout["sets"])
        spacing = max(1, elapsed // max(1, set_count))
        set_duration = min(60, spacing)
        cursor = start_fit
        message_index = 0
        for index, set_info in enumerate(lyfta_workout["sets"]):
            set_start = min(end_fit, cursor)
            reps = max(0, min(65535, int(set_info.get("reps", 0) or 0)))
            weight = max(0, min(65535, int(round(float(set_info.get("weight", 0.0) or 0.0) * 16))))
            exercise_name = set_info.get("exercise", "").strip() or "Exercise"
            exercise_index = exercise_indexes.get(exercise_name, 0)
            category = exercises[exercise_index]["category"]
            subtype = exercises[exercise_index]["subtype"]
            set_records.append(data_msg(5,
                ("<I", set_start),
                ("<I", set_start),
                ("<I", set_duration * 1000),
                ("<H", reps),
                ("<H", weight),
                ("<B", SET_ACTIVE),
                ("<B", category),
                ("<H", subtype),
                ("<H", message_index),
                ("<H", exercise_index),
            ))
            message_index += 1
            cursor = set_start + set_duration

            if index < set_count - 1:
                cursor += rest_seconds_for_exercise_category(category)

    data_records = [
        data_msg(0, ("<B", 4), ("<I", start_fit)),
        data_msg(1, ("<I", start_fit), ("<B", 0), ("<B", 0)),
        *exercise_title_records,
        *set_records,
    ]

    if has_hr:
        for offset, heart_rate in garmin_source["hr_offsets"]:
            if 0 <= offset <= elapsed:
                data_records.append(data_msg(7,
                    ("<I", start_fit + offset),
                    ("<B", max(0, min(255, int(heart_rate)))),
                ))

    data_records += [
        data_msg(4,
            ("<I", end_fit), ("<I", start_fit),
            ("<I", duration_ms), ("<I", duration_ms),
            ("<B", SPORT_TRAINING), ("<B", sub_sport), ("<H", 0),
        ),
        data_msg(2,
            ("<I", end_fit), ("<I", start_fit), ("<B", SPORT_TRAINING), ("<B", sub_sport),
            ("<I", duration_ms), ("<I", duration_ms), ("<H", calories), ("<B", 0),
            ("<H", 1),
        ),
        data_msg(3, ("<I", end_fit), ("<I", duration_ms), ("<H", 1), ("<B", 0), ("<B", 26), ("<B", 1)),
        data_msg(1, ("<I", end_fit), ("<B", 0), ("<B", 1)),
    ]

    records = b"".join(definitions + data_records)

    header = struct.pack("<BBHI4s", 14, 16, 2140, len(records), b".FIT")
    header += struct.pack("<H", fit_crc(header))
    return header + records + struct.pack("<H", fit_crc(records))


def fetch_strava_workouts(token, days_back):
    after = int(time.time()) - days_back * 86400
    headers = {"Authorization": "Bearer {}".format(token)}
    page = 1
    workouts = []

    while True:
        response = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers=headers,
            params={"after": after, "per_page": 100, "page": page},
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break

        for activity in batch:
            kind = activity.get("sport_type") or activity.get("type", "")
            if kind in STRENGTH_TYPES:
                workouts.append(activity)
        page += 1

    return workouts


def parse_strava_local_time(activity):
    for key in ("start_date_local", "start_date"):
        value = activity.get(key)
        if not value:
            continue
        try:
            return datetime.strptime(value.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    return None


def parse_garmin_local_time(activity):
    value = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def is_garmin_strength_activity(activity):
    activity_type = activity.get("activityType") or {}
    return activity_type.get("typeKey") == "strength_training"


def fetch_garmin_strength_activities(garmin, limit=500):
    activities = garmin.get_activities(0, limit)
    return [activity for activity in activities if is_garmin_strength_activity(activity)]


def is_replaceable_uploaded_activity(activity):
    name = (activity.get("activityName") or "").strip().lower()
    return name == "strength training"


def is_source_garmin_activity(activity):
    if not is_garmin_strength_activity(activity):
        return False
    if is_replaceable_uploaded_activity(activity):
        return False
    return float(activity.get("calories") or 0) > 0


def find_garmin_matches(strava_activity, garmin_activities, tolerance_seconds=2 * 3600):
    strava_time = parse_strava_local_time(strava_activity)
    if strava_time is None:
        return []
    matches = []
    for activity in garmin_activities:
        if not is_replaceable_uploaded_activity(activity):
            continue
        garmin_time = parse_garmin_local_time(activity)
        if garmin_time is None:
            continue
        delta = abs((garmin_time - strava_time).total_seconds())
        if delta <= tolerance_seconds:
            matches.append((delta, activity))
    return [activity for _, activity in sorted(matches, key=lambda item: item[0])[:1]]


def find_source_garmin_activity(strava_activity, garmin_activities, tolerance_seconds=3 * 3600):
    strava_time = parse_strava_local_time(strava_activity)
    if strava_time is None:
        return None
    matches = []
    for activity in garmin_activities:
        if not is_source_garmin_activity(activity):
            continue
        garmin_time = parse_garmin_local_time(activity)
        if garmin_time is None:
            continue
        delta = abs((garmin_time - strava_time).total_seconds())
        if delta <= tolerance_seconds:
            matches.append((delta, activity))
    return sorted(matches, key=lambda item: item[0])[0][1] if matches else None


def extract_garmin_hr_source(garmin, source_activity, max_step_seconds=5):
    result = {
        "activity_id": source_activity.get("activityId"),
        "calories": int(round(float(source_activity.get("calories") or 0))),
        "hr_offsets": [],
    }
    try:
        data = garmin.download_activity(
            str(source_activity["activityId"]),
            Garmin.ActivityDownloadFormat.ORIGINAL,
        )
        archive = zipfile.ZipFile(io.BytesIO(data))
        fit_name = next(name for name in archive.namelist() if name.lower().endswith(".fit"))
        fit = FitFile(io.BytesIO(archive.read(fit_name)))
    except Exception:
        return result

    first_timestamp = None
    last_offset = None
    for message in fit.get_messages("record"):
        values = {field.name: field.value for field in message}
        timestamp = values.get("timestamp")
        heart_rate = values.get("heart_rate")
        if timestamp is None or heart_rate is None:
            continue
        if first_timestamp is None:
            first_timestamp = timestamp
        offset = int((timestamp - first_timestamp).total_seconds())
        if last_offset is not None and offset - last_offset < max_step_seconds:
            continue
        result["hr_offsets"].append((offset, int(heart_rate)))
        last_offset = offset
    return result


def get_garmin_profile_weight_kg(garmin):
    try:
        profile = garmin.get_user_profile()
    except Exception:
        return None
    user_data = profile.get("userData", {}) if isinstance(profile, dict) else {}
    weight = user_data.get("weight")
    if weight is None:
        return None
    try:
        weight = float(weight)
    except (TypeError, ValueError):
        return None
    if weight > 300:
        weight = weight / 1000
    return weight if weight > 0 else None


def resolve_weight_kg(values, garmin):
    configured = str(values.get("USER_WEIGHT_KG", "")).strip()
    if configured:
        try:
            weight = float(configured.replace(",", "."))
            if weight > 0:
                return weight, "config"
        except ValueError:
            pass

    profile_weight = get_garmin_profile_weight_kg(garmin)
    if profile_weight:
        return profile_weight, "Garmin profile"
    return 85.0, "default"


def estimate_strength_calories(activity, lyfta_workout, weight_kg, strength_met):
    elapsed = int(activity.get("elapsed_time", 0) or 0)
    if lyfta_workout and lyfta_workout.get("duration"):
        elapsed = lyfta_workout["duration"]
    if elapsed <= 0 and lyfta_workout and lyfta_workout.get("sets"):
        elapsed = planned_sets_duration_seconds(lyfta_workout["sets"])
    if elapsed <= 0:
        elapsed = 45 * 60

    minutes = elapsed / 60
    calories = strength_met * 3.5 * weight_kg * minutes / 200
    return max(1, int(round(calories)))


def garmin_activity_in_range(activity, start_time, end_time):
    garmin_time = parse_garmin_local_time(activity)
    if garmin_time is None:
        return False
    return start_time <= garmin_time <= end_time


def upload_to_garmin(garmin, fit_bytes):
    with tempfile.NamedTemporaryFile(suffix=".fit", delete=False, prefix="lyfta_") as handle:
        handle.write(fit_bytes)
        tmp_path = Path(handle.name)
    try:
        garmin.upload_activity(str(tmp_path))
        return True
    except Exception as exc:
        text = str(exc).lower()
        if "409" in text or "duplicate" in text:
            return True
        raise
    finally:
        tmp_path.unlink(missing_ok=True)


def validate_values(values, require_garmin=True):
    if not values.get("STRAVA_CLIENT_ID") or not values.get("STRAVA_CLIENT_SECRET"):
        raise ValueError("Strava Client ID and Client Secret are required.")
    try:
        days_back = int(values.get("DAYS_BACK", "30"))
    except ValueError:
        raise ValueError("Days Back must be a number.")
    if days_back < 1:
        raise ValueError("Days Back must be at least 1.")
    try:
        strength_met = float(str(values.get("STRENGTH_MET", "5.0")).replace(",", "."))
    except ValueError:
        raise ValueError("Strength MET must be a number.")
    if strength_met <= 0:
        raise ValueError("Strength MET must be greater than 0.")
    weight = str(values.get("USER_WEIGHT_KG", "")).strip()
    if weight:
        try:
            user_weight = float(weight.replace(",", "."))
        except ValueError:
            raise ValueError("User Weight KG must be a number or empty.")
        if user_weight <= 0:
            raise ValueError("User Weight KG must be greater than 0 or empty.")
    if require_garmin and (not values.get("GARMIN_EMAIL") or not values.get("GARMIN_PASSWORD")):
        raise ValueError("Garmin email and password are required.")


def validate_daily_time(value):
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        raise ValueError("Daily sync time must use HH:MM, for example 08:00.")


def run_schtasks(args):
    return subprocess.run(
        ["schtasks"] + args,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def install_daily_task(start_time):
    validate_daily_time(start_time)
    command = '"{}"'.format(RUN_DAILY_SYNC_BAT)
    result = run_schtasks([
        "/Create",
        "/TN", TASK_NAME,
        "/TR", command,
        "/SC", "DAILY",
        "/ST", start_time,
        "/F",
    ])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def uninstall_daily_task():
    result = run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def query_daily_task():
    result = run_schtasks(["/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_strava_token(values, log, ask_code=None, open_browser=False):
    token_data = load_json(STRAVA_TOKENS_FILE, None)
    if token_data:
        if token_data["expires_at"] > time.time() + 60:
            log("Using cached Strava token.")
            return token_data["access_token"]
        log("Refreshing Strava token...")
        response = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": values["STRAVA_CLIENT_ID"],
            "client_secret": values["STRAVA_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
        })
        response.raise_for_status()
        token_data = response.json()
        save_json(STRAVA_TOKENS_FILE, token_data)
        return token_data["access_token"]

    if ask_code is None:
        raise RuntimeError("No cached Strava token. Run the GUI once to authorize Strava before using daily sync.")

    auth_url = "https://www.strava.com/oauth/authorize?{}".format(urlencode({
        "client_id": values["STRAVA_CLIENT_ID"],
        "redirect_uri": values["STRAVA_CALLBACK_URL"],
        "response_type": "code",
        "scope": "activity:read_all",
    }))
    log("Opening Strava authorization in your browser.")
    if open_browser:
        webbrowser.open(auth_url)
    code = ask_code(auth_url)
    if not code:
        raise RuntimeError("Strava authorization was cancelled.")

    response = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": values["STRAVA_CLIENT_ID"],
        "client_secret": values["STRAVA_CLIENT_SECRET"],
        "grant_type": "authorization_code",
        "code": code.strip(),
    })
    response.raise_for_status()
    token_data = response.json()
    save_json(STRAVA_TOKENS_FILE, token_data)

    athlete = token_data.get("athlete", {})
    log("Strava authenticated as {} {}".format(
        athlete.get("firstname", ""),
        athlete.get("lastname", ""),
    ).strip())
    return token_data["access_token"]


def run_sync(values, log, ask_strava_code=None, ask_garmin_mfa=None, open_browser=False, resync_existing=False, dry_run=False):
    validate_values(values, require_garmin=True)
    values = dict(values)
    values.setdefault("STRAVA_CALLBACK_URL", "http://localhost")
    values.setdefault("LYFTA_CSV", "WorkoutData.csv")
    values.setdefault("DAYS_BACK", "30")
    values.setdefault("STRENGTH_MET", "5.0")

    days_back = int(values["DAYS_BACK"])
    strength_met = float(str(values["STRENGTH_MET"]).replace(",", "."))
    token = get_strava_token(values, log, ask_code=ask_strava_code, open_browser=open_browser)

    log("Fetching Strava strength workouts from the last {} days...".format(days_back))
    workouts = fetch_strava_workouts(token, days_back)
    log("Found {} strength-type workouts.".format(len(workouts)))

    lyfta_workouts = load_lyfta_workouts(values["LYFTA_CSV"])
    log("Loaded {} Lyfta CSV workouts with set data.".format(len(lyfta_workouts)))

    if not workouts:
        log("Nothing to sync. Confirm Lyfta is connected to Strava and has synced workouts.")
        return {"uploaded": 0, "failed": 0, "skipped": 0}

    log("Logging in to Garmin Connect...")
    garmin = Garmin(
        values["GARMIN_EMAIL"],
        values["GARMIN_PASSWORD"],
        prompt_mfa=ask_garmin_mfa,
    )
    GARMIN_TOKENS_DIR.mkdir(exist_ok=True)
    garmin.login(tokenstore=str(GARMIN_TOKENS_DIR))
    log("Logged in to Garmin Connect.")
    weight_kg, weight_source = resolve_weight_kg(values, garmin)
    log("Using {:.1f} kg from {} for estimated calories when Garmin calories are unavailable.".format(weight_kg, weight_source))

    synced = set(load_json(SYNCED_IDS_FILE, []))
    garmin_activities = fetch_garmin_strength_activities(garmin)
    log("Loaded {} Garmin strength activities for calories/HR matching.".format(len(garmin_activities)))

    if resync_existing:
        log("Resync mode: looking for old app-uploaded Garmin strength activities to replace.")

        strava_times = [parse_strava_local_time(workout) for workout in workouts]
        strava_times = [value for value in strava_times if value is not None]
        if not strava_times:
            raise RuntimeError("Cannot resync because no Strava workout start times were available.")

        start_range = min(strava_times).replace(hour=0, minute=0, second=0) 
        end_range = max(strava_times).replace(hour=23, minute=59, second=59)
        replaceable = [
            activity for activity in garmin_activities
            if is_replaceable_uploaded_activity(activity)
            and garmin_activity_in_range(activity, start_range, end_range)
        ]
        replaceable = sorted(replaceable, key=lambda activity: activity.get("startTimeLocal") or "")

        for activity in replaceable:
            activity_id = str(activity["activityId"])
            activity_name = activity.get("activityName", "")
            activity_time = activity.get("startTimeLocal", "")
            if dry_run:
                log("DRY RUN would delete Garmin activity {} [{}] {}".format(activity_id, activity_time, activity_name))
            else:
                log("RESYNC deleting Garmin activity {} [{}] {}".format(activity_id, activity_time, activity_name))
                garmin.delete_activity(activity_id)
                time.sleep(0.5)

        if dry_run:
            log("Dry run complete. Would delete {} old app-uploaded Garmin activities. No Garmin activities were deleted or uploaded.".format(len(replaceable)))
            return {"uploaded": 0, "failed": 0, "skipped": 0}
        synced.clear()
        save_json(SYNCED_IDS_FILE, [])
        log("Resync mode: deleted {} old Garmin activities and cleared synced IDs.".format(len(replaceable)))

    uploaded = 0
    failed = 0
    skipped = 0
    source_cache = {}

    for workout in sorted(workouts, key=lambda x: x["start_date"]):
        wid = str(workout["id"])
        name = workout.get("name", "Strength Training")
        date = workout["start_date"][:10]
        mins = workout.get("elapsed_time", 0) // 60
        kind = workout.get("sport_type") or workout.get("type", "?")

        if wid in synced:
            skipped += 1
            log("SKIP  [{}] {} ({})".format(date, name, kind))
            continue

        try:
            lyfta_match = match_lyfta_workout(workout, lyfta_workouts)
            source_activity = find_source_garmin_activity(workout, garmin_activities)
            garmin_source = None
            if source_activity:
                source_id = str(source_activity["activityId"])
                if source_id not in source_cache:
                    source_cache[source_id] = extract_garmin_hr_source(garmin, source_activity)
                garmin_source = source_cache[source_id]
                log("GARMIN [{}] matched source activity {} with {} kcal and {} HR samples.".format(
                    date,
                    source_id,
                    garmin_source.get("calories", 0),
                    len(garmin_source.get("hr_offsets", [])),
                ))
            else:
                estimated_calories = estimate_strength_calories(workout, lyfta_match, weight_kg, strength_met)
                garmin_source = {
                    "activity_id": None,
                    "calories": estimated_calories,
                    "hr_offsets": [],
                    "calories_source": "estimated",
                }
                log("EST   [{}] estimated {} kcal using {:.1f} kg and {:.1f} MET.".format(
                    date,
                    estimated_calories,
                    weight_kg,
                    strength_met,
                ))
            if lyfta_match:
                fit_bytes = build_fit(workout, lyfta_match, garmin_source)
                exercise_count = len(unique_exercises(lyfta_match["sets"])[0])
                log("SETS  [{}] matched {} exercises and {} sets from Lyfta CSV with names, reps, and weight.".format(date, exercise_count, len(lyfta_match["sets"])))
            else:
                fit_bytes = build_fit(workout, garmin_source=garmin_source)
                log("SETS  [{}] no Lyfta CSV match; uploading summary only.".format(date))
            upload_to_garmin(garmin, fit_bytes)
            synced.add(wid)
            save_json(SYNCED_IDS_FILE, sorted(synced))
            uploaded += 1
            kcal = int((garmin_source or {}).get("calories") or workout.get("calories", 0) or 0)
            log("OK    [{}] {}  {} min  {} kcal  ({})".format(date, name, mins, kcal, kind))
        except Exception as exc:
            failed += 1
            log("FAIL  [{}] {}: {}".format(date, name, exc))

        time.sleep(1.5)
    log("Done. Uploaded {}, skipped {}, failed {}.".format(uploaded, skipped, failed))
    return {"uploaded": uploaded, "failed": failed, "skipped": skipped}


def run_headless_sync():
    values = read_env_file()
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / "daily-sync.log"

    def log(text):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = "[{}] {}".format(timestamp, text)
        console_encoding = sys.stdout.encoding or "utf-8"
        print(line.encode(console_encoding, errors="replace").decode(console_encoding, errors="replace"))
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    log("Starting scheduled Lyfta to Garmin sync.")
    try:
        run_sync(
            values,
            log,
            ask_strava_code=None,
            ask_garmin_mfa=None,
            open_browser=False,
            resync_existing="--resync" in sys.argv,
            dry_run="--dry-run" in sys.argv,
        )
    except Exception:
        log("ERROR:\n{}".format(traceback.format_exc()))
        return 1
    log("Scheduled sync finished.")
    return 0


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Lyfta to Garmin")
        self.root.geometry("820x620")
        self.root.minsize(760, 560)

        self.log_queue = queue.Queue()
        self.prompt_queue = queue.Queue()
        self.worker = None

        env = read_env_file()
        self.strava_client_id = StringVar(value=env.get("STRAVA_CLIENT_ID", ""))
        self.strava_client_secret = StringVar(value=env.get("STRAVA_CLIENT_SECRET", ""))
        self.garmin_email = StringVar(value=env.get("GARMIN_EMAIL", ""))
        self.garmin_password = StringVar(value=env.get("GARMIN_PASSWORD", ""))
        self.callback_url = StringVar(value=env.get("STRAVA_CALLBACK_URL", "http://localhost"))
        self.lyfta_csv = StringVar(value=env.get("LYFTA_CSV", "WorkoutData.csv"))
        self.days_back = StringVar(value=env.get("DAYS_BACK", "30"))
        self.user_weight_kg = StringVar(value=env.get("USER_WEIGHT_KG", ""))
        self.strength_met = StringVar(value=env.get("STRENGTH_MET", "5.0"))
        self.daily_sync_time = StringVar(value=env.get("DAILY_SYNC_TIME", "08:00"))
        self.status = StringVar(value="Ready")

        self.build_ui()
        self.root.after(100, self.process_log_queue)
        self.root.after(100, self.process_prompt_queue)

    def build_ui(self):
        outer = Frame(self.root, padx=16, pady=14)
        outer.pack(fill=BOTH, expand=True)

        Label(outer, text="Lyfta to Garmin", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, columnspan=3, sticky=W, pady=(0, 10)
        )
        self.add_field(outer, 1, "Strava Client ID", self.strava_client_id)
        self.add_field(outer, 2, "Strava Client Secret", self.strava_client_secret, show="*")
        self.add_field(outer, 3, "Garmin Email", self.garmin_email)
        self.add_field(outer, 4, "Garmin Password", self.garmin_password, show="*")
        self.add_field(outer, 5, "Callback URL", self.callback_url)
        self.add_field(outer, 6, "Lyfta CSV", self.lyfta_csv)
        self.add_field(outer, 7, "Days Back", self.days_back, width=12)
        self.add_field(outer, 8, "User Weight KG", self.user_weight_kg, width=12)
        self.add_field(outer, 9, "Strength MET", self.strength_met, width=12)
        self.add_field(outer, 10, "Daily Sync Time", self.daily_sync_time, width=12)

        buttons = Frame(outer)
        buttons.grid(row=11, column=0, columnspan=3, sticky=W, pady=(12, 8))
        self.save_button = Button(buttons, text="Save Config", width=16, command=self.save_config)
        self.save_button.pack(side="left", padx=(0, 8))
        self.run_button = Button(buttons, text="Run Sync", width=16, command=self.start_sync)
        self.run_button.pack(side="left", padx=(0, 8))
        self.reset_strava_button = Button(buttons, text="Reset Strava Login", width=18, command=self.reset_strava_login)
        self.reset_strava_button.pack(side="left", padx=(0, 8))
        self.reset_garmin_button = Button(buttons, text="Reset Garmin Login", width=18, command=self.reset_garmin_login)
        self.reset_garmin_button.pack(side="left", padx=(0, 8))
        self.reset_synced_button = Button(buttons, text="Reset Synced IDs", width=16, command=self.reset_synced_ids)
        self.reset_synced_button.pack(side="left", padx=(0, 8))
        self.resync_button = Button(buttons, text="Resync Old", width=14, command=self.start_resync)
        self.resync_button.pack(side="left")

        service_buttons = Frame(outer)
        service_buttons.grid(row=12, column=0, columnspan=3, sticky=W, pady=(0, 12))
        self.install_task_button = Button(service_buttons, text="Install Daily Sync", width=18, command=self.install_daily_sync)
        self.install_task_button.pack(side="left", padx=(0, 8))
        self.uninstall_task_button = Button(service_buttons, text="Remove Daily Sync", width=18, command=self.uninstall_daily_sync)
        self.uninstall_task_button.pack(side="left", padx=(0, 8))
        self.status_task_button = Button(service_buttons, text="Check Daily Sync", width=18, command=self.check_daily_sync)
        self.status_task_button.pack(side="left", padx=(0, 8))
        self.run_headless_button = Button(service_buttons, text="Run Daily Sync Now", width=18, command=self.start_headless_sync)
        self.run_headless_button.pack(side="left", padx=(0, 8))
        self.open_log_button = Button(service_buttons, text="Open Log", width=12, command=self.open_daily_log)
        self.open_log_button.pack(side="left")

        Label(outer, textvariable=self.status, anchor="w").grid(row=13, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        self.log = ScrolledText(outer, height=18, wrap="word", state=DISABLED)
        self.log.grid(row=14, column=0, columnspan=3, sticky="nsew")

        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(14, weight=1)

    def add_field(self, parent, row, label, variable, show=None, width=48):
        Label(parent, text=label).grid(row=row, column=0, sticky=W, pady=4, padx=(0, 10))
        entry = Entry(parent, textvariable=variable, show=show, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        return entry

    def config(self):
        return {
            "STRAVA_CLIENT_ID": self.strava_client_id.get().strip(),
            "STRAVA_CLIENT_SECRET": self.strava_client_secret.get().strip(),
            "GARMIN_EMAIL": self.garmin_email.get().strip(),
            "GARMIN_PASSWORD": self.garmin_password.get(),
            "STRAVA_CALLBACK_URL": self.callback_url.get().strip() or "http://localhost",
            "LYFTA_CSV": self.lyfta_csv.get().strip() or "WorkoutData.csv",
            "DAYS_BACK": self.days_back.get().strip() or "30",
            "USER_WEIGHT_KG": self.user_weight_kg.get().strip(),
            "STRENGTH_MET": self.strength_met.get().strip() or "5.0",
            "DAILY_SYNC_TIME": self.daily_sync_time.get().strip() or "08:00",
        }

    def validate_config(self, require_garmin):
        validate_values(self.config(), require_garmin=require_garmin)
        validate_daily_time(self.config()["DAILY_SYNC_TIME"])

    def save_config(self):
        try:
            self.validate_config(require_garmin=False)
        except ValueError as exc:
            messagebox.showerror("Invalid Config", str(exc))
            return False
        write_env_file(self.config())
        self.log_line("Saved config to {}".format(ENV_FILE))
        return True

    def reset_strava_login(self):
        if STRAVA_TOKENS_FILE.exists():
            STRAVA_TOKENS_FILE.unlink()
            self.log_line("Removed cached Strava token.")
        else:
            self.log_line("No cached Strava token found.")

    def reset_garmin_login(self):
        if not GARMIN_TOKENS_DIR.exists():
            self.log_line("No cached Garmin token folder found.")
            return
        for path in GARMIN_TOKENS_DIR.glob("*"):
            if path.is_file():
                path.unlink()
        self.log_line("Removed cached Garmin tokens.")

    def reset_synced_ids(self):
        if not messagebox.askyesno(
            "Reset Synced IDs",
            "This lets the app try to upload Strava workouts again. Delete the old empty Garmin activities first, otherwise Garmin may reject duplicates. Continue?",
        ):
            return
        if SYNCED_IDS_FILE.exists():
            SYNCED_IDS_FILE.unlink()
            self.log_line("Removed synced_ids.json.")
        else:
            self.log_line("No synced_ids.json file found.")

    def start_sync(self):
        try:
            self.validate_config(require_garmin=True)
        except ValueError as exc:
            messagebox.showerror("Invalid Config", str(exc))
            return
        if not self.save_config():
            return

        self.set_running(True)
        self.worker = threading.Thread(target=self.run_sync, daemon=True)
        self.worker.start()

    def start_resync(self):
        try:
            self.validate_config(require_garmin=True)
        except ValueError as exc:
            messagebox.showerror("Invalid Config", str(exc))
            return
        if not messagebox.askyesno(
            "Resync Old Activities",
            "This will delete matching old Garmin strength activities and upload them again with exercise details. Continue?",
        ):
            return
        if not self.save_config():
            return
        self.set_running(True)
        self.worker = threading.Thread(target=lambda: self.run_sync(resync_existing=True), daemon=True)
        self.worker.start()

    def set_running(self, running):
        state = DISABLED if running else NORMAL
        for button in (
            self.run_button,
            self.save_button,
            self.reset_strava_button,
            self.reset_garmin_button,
            self.reset_synced_button,
            self.resync_button,
            self.install_task_button,
            self.uninstall_task_button,
            self.status_task_button,
            self.run_headless_button,
            self.open_log_button,
        ):
            button.config(state=state)
        self.status.set("Sync running..." if running else "Ready")

    def install_daily_sync(self):
        if not self.save_config():
            return
        try:
            output = install_daily_task(self.config()["DAILY_SYNC_TIME"])
            self.log_line(output or "Installed daily sync task.")
            messagebox.showinfo("Daily Sync", "Daily sync installed.")
        except Exception as exc:
            messagebox.showerror("Daily Sync", str(exc))
            self.log_line("Failed to install daily sync: {}".format(exc))

    def uninstall_daily_sync(self):
        try:
            output = uninstall_daily_task()
            self.log_line(output or "Removed daily sync task.")
            messagebox.showinfo("Daily Sync", "Daily sync removed.")
        except Exception as exc:
            messagebox.showerror("Daily Sync", str(exc))
            self.log_line("Failed to remove daily sync: {}".format(exc))

    def check_daily_sync(self):
        output = query_daily_task()
        if output:
            self.log_line("Daily sync task is installed:\n{}".format(output))
            messagebox.showinfo("Daily Sync", "Daily sync task is installed.")
        else:
            self.log_line("Daily sync task is not installed.")
            messagebox.showinfo("Daily Sync", "Daily sync task is not installed.")

    def start_headless_sync(self):
        try:
            self.validate_config(require_garmin=True)
        except ValueError as exc:
            messagebox.showerror("Invalid Config", str(exc))
            return
        if not self.save_config():
            return
        self.set_running(True)
        self.worker = threading.Thread(target=self.run_headless_sync_now, daemon=True)
        self.worker.start()

    def run_headless_sync_now(self):
        try:
            self.log_line("Starting headless daily sync now...")
            exit_code = run_headless_sync()
            if exit_code == 0:
                self.log_line("Headless daily sync finished.")
            else:
                self.log_line("Headless daily sync failed. Check logs/daily-sync.log.")
        finally:
            self.root.after(0, lambda: self.set_running(False))

    def open_daily_log(self):
        LOG_DIR.mkdir(exist_ok=True)
        if not DAILY_LOG_FILE.exists():
            DAILY_LOG_FILE.write_text("", encoding="utf-8")
        os.startfile(str(DAILY_LOG_FILE))

    def run_sync(self, resync_existing=False):
        try:
            values = self.config()
            run_sync(
                values,
                self.log_line,
                ask_strava_code=lambda auth_url: self.ask_text(
                    "Strava Authorization",
                    "Paste the code value from the redirected Strava URL:",
                ),
                ask_garmin_mfa=lambda: self.ask_text(
                    "Garmin 2FA",
                    "Enter the Garmin 2FA code from your email/app:",
                ) or "",
                open_browser=True,
                resync_existing=resync_existing,
            )
        except Exception as exc:
            self.log_line("ERROR: {}".format(exc))
            self.log_line(traceback.format_exc())
        finally:
            self.root.after(0, lambda: self.set_running(False))

    def ask_text(self, title, prompt):
        result = {"value": None}
        done = threading.Event()
        self.prompt_queue.put((title, prompt, result, done))
        done.wait()
        return result["value"]

    def process_prompt_queue(self):
        try:
            while True:
                title, prompt, result, done = self.prompt_queue.get_nowait()
                result["value"] = simpledialog.askstring(title, prompt, parent=self.root)
                done.set()
        except queue.Empty:
            pass
        self.root.after(100, self.process_prompt_queue)

    def log_line(self, text):
        self.log_queue.put(text)

    def process_log_queue(self):
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log.config(state=NORMAL)
                self.log.insert(END, text + "\n")
                self.log.see(END)
                self.log.config(state=DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self.process_log_queue)


def main():
    if len(sys.argv) > 1 and any(arg in ("--sync", "sync", "--headless", "--resync") for arg in sys.argv[1:]):
        sys.exit(run_headless_sync())
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
