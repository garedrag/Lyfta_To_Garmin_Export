#!/usr/bin/env python3
"""Export Garmin health data and generate a daily longevity-focused report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import mimetypes
import shutil
import statistics
import subprocess
import sys
import traceback
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from garminconnect import Garmin
except ImportError:
    sys.exit("Missing dependency. Run: python -m pip install garminconnect")

try:
    from fitparse import FitFile
except ImportError:
    FitFile = None


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
GARMIN_TOKENS_DIR = ROOT / "garmin_tokens"
EXPORT_DIR = ROOT / "garmin_exports"
REPORT_DIR = ROOT / "reports" / "health"
ASSISTANT_DIR = ROOT / "exports" / "assistant"
LOCAL_EXPORT_DIR = ROOT / "local_garmin_exports"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "health-report.log"
TASK_NAME = "GarminHealthDailyReport"
RUN_DAILY_REPORT_BAT = ROOT / "run_daily_health_report.bat"

DEFAULT_SOURCES = {"daily", "history", "profile", "devices", "activities", "local"}


def read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def log(text: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    line = "[{}] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def login_garmin(values: dict[str, str]) -> Garmin:
    email = values.get("GARMIN_EMAIL")
    password = values.get("GARMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("GARMIN_EMAIL and GARMIN_PASSWORD must be set in .env.")

    garmin = Garmin(email, password)
    GARMIN_TOKENS_DIR.mkdir(exist_ok=True)
    garmin.login(tokenstore=str(GARMIN_TOKENS_DIR))
    return garmin


def fetch_daily_payload(
    garmin: Garmin,
    target: date,
    days_back: int,
    sources: set[str],
    activity_limit: int,
    download_activity_originals: bool,
    local_export_path: Path,
) -> dict[str, Any]:
    cdate = target.isoformat()
    start = (target - timedelta(days=days_back - 1)).isoformat()
    end = cdate

    payload: dict[str, Any] = {
        "date": cdate,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "garminconnect",
        "sources_requested": sorted(sources),
        "lookback_days": days_back,
        "daily": {},
        "history": {},
        "profile": {},
        "devices": {},
        "activities": {},
        "local_exports": {},
        "errors": {},
    }

    if source_enabled(sources, "daily"):
        daily_calls = {
        "stats": lambda: garmin.get_stats(cdate),
        "stats_and_body": lambda: garmin.get_stats_and_body(cdate),
        "user_summary": lambda: garmin.get_user_summary(cdate),
        "sleep": lambda: garmin.get_sleep_data(cdate),
        "hrv": lambda: garmin.get_hrv_data(cdate),
        "stress": lambda: garmin.get_stress_data(cdate),
        "all_day_stress": lambda: garmin.get_all_day_stress(cdate),
        "heart_rates": lambda: garmin.get_heart_rates(cdate),
        "rhr_day": lambda: garmin.get_rhr_day(cdate),
        "respiration": lambda: garmin.get_respiration_data(cdate),
        "spo2": lambda: garmin.get_spo2_data(cdate),
        "hydration": lambda: garmin.get_hydration_data(cdate),
        "floors": lambda: garmin.get_floors(cdate),
        "steps_data": lambda: garmin.get_steps_data(cdate),
        "intensity_minutes": lambda: garmin.get_intensity_minutes_data(cdate),
        "training_readiness": lambda: garmin.get_training_readiness(cdate),
        "morning_training_readiness": lambda: garmin.get_morning_training_readiness(cdate),
        "training_status": lambda: garmin.get_training_status(cdate),
        "max_metrics": lambda: garmin.get_max_metrics(cdate),
        "fitness_age": lambda: garmin.get_fitnessage_data(cdate),
        "all_day_events": lambda: garmin.get_all_day_events(cdate),
        "lifestyle_logging": lambda: garmin.get_lifestyle_logging_data(cdate),
        "nutrition_food_log": lambda: garmin.get_nutrition_daily_food_log(cdate),
        "nutrition_meals": lambda: garmin.get_nutrition_daily_meals(cdate),
        "nutrition_settings": lambda: garmin.get_nutrition_daily_settings(cdate),
        "body_battery": lambda: garmin.get_body_battery(cdate, cdate),
        "body_battery_events": lambda: garmin.get_body_battery_events(cdate),
        "body_composition": lambda: garmin.get_body_composition(cdate, cdate),
        "daily_weigh_ins": lambda: garmin.get_daily_weigh_ins(cdate),
    }
        fetch_many(payload["daily"], payload["errors"], daily_calls)

    if source_enabled(sources, "history"):
        history_calls = {
        "body_battery": lambda: garmin.get_body_battery(start, end),
        "body_composition": lambda: garmin.get_body_composition(start, end),
        "blood_pressure": lambda: garmin.get_blood_pressure(start, end),
        "daily_steps": lambda: garmin.get_daily_steps(start, end),
        "weigh_ins": lambda: garmin.get_weigh_ins(start, end),
        "weekly_steps": lambda: garmin.get_weekly_steps(end, max(1, min(52, days_back // 7 + 1))),
        "weekly_stress": lambda: garmin.get_weekly_stress(end, max(1, min(52, days_back // 7 + 1))),
        "weekly_intensity_minutes": lambda: garmin.get_weekly_intensity_minutes(start, end),
        "endurance_score": lambda: garmin.get_endurance_score(start, end),
        "hill_score": lambda: garmin.get_hill_score(start, end),
        "race_predictions": lambda: garmin.get_race_predictions(),
        "running_tolerance": lambda: garmin.get_running_tolerance(start, end),
        "lactate_threshold": lambda: garmin.get_lactate_threshold(start_date=start, end_date=end),
        "progress_distance": lambda: garmin.get_progress_summary_between_dates(start, end, "distance"),
        "progress_duration": lambda: garmin.get_progress_summary_between_dates(start, end, "duration"),
        "progress_calories": lambda: garmin.get_progress_summary_between_dates(start, end, "calories"),
    }
        fetch_many(payload["history"], payload["errors"], history_calls, prefix="history_")

    if source_enabled(sources, "profile"):
        profile_calls = {
            "full_name": lambda: garmin.get_full_name(),
            "unit_system": lambda: garmin.get_unit_system(),
            "user_profile": lambda: garmin.get_user_profile(),
            "userprofile_settings": lambda: garmin.get_userprofile_settings(),
            "goals_active": lambda: garmin.get_goals("active", 0, 100),
            "goals_past": lambda: garmin.get_goals("past", 0, 100),
            "personal_records": lambda: garmin.get_personal_record(),
            "earned_badges": lambda: garmin.get_earned_badges(),
            "available_badges": lambda: garmin.get_available_badges(),
            "in_progress_badges": lambda: garmin.get_in_progress_badges(),
            "available_badge_challenges": lambda: garmin.get_available_badge_challenges(1, 100),
            "badge_challenges": lambda: garmin.get_badge_challenges(1, 100),
            "adhoc_challenges": lambda: garmin.get_adhoc_challenges(0, 100),
            "training_plans": lambda: garmin.get_training_plans(),
            "workouts": lambda: garmin.get_workouts(0, 100),
            "cycling_ftp": lambda: garmin.get_cycling_ftp(),
            "pregnancy_summary": lambda: garmin.get_pregnancy_summary(),
        }
        fetch_many(payload["profile"], payload["errors"], profile_calls, prefix="profile_")
        profile = payload["profile"].get("user_profile")
        user_id = profile.get("userProfileId") if isinstance(profile, dict) else None
        if user_id:
            fetch_many(
                payload["profile"],
                payload["errors"],
                {
                    "gear": lambda: garmin.get_gear(str(user_id)),
                    "gear_defaults": lambda: garmin.get_gear_defaults(str(user_id)),
                },
                prefix="profile_",
            )

    if source_enabled(sources, "devices"):
        fetch_many(
            payload["devices"],
            payload["errors"],
            {
                "devices": lambda: garmin.get_devices(),
                "device_last_used": lambda: garmin.get_device_last_used(),
                "primary_training_device": lambda: garmin.get_primary_training_device(),
                "device_alarms": lambda: garmin.get_device_alarms(),
            },
            prefix="devices_",
        )
        for device in listify(payload["devices"].get("devices")):
            device_id = str(device.get("deviceId") or device.get("unitId") or "")
            if not device_id:
                continue
            payload["devices"].setdefault("per_device", {})[device_id] = {}
            fetch_many(
                payload["devices"]["per_device"][device_id],
                payload["errors"],
                {
                    "settings": lambda device_id=device_id: garmin.get_device_settings(device_id),
                },
                prefix="devices_{}_".format(device_id),
            )
            if device.get("solarChargeCapable") or device.get("solarPanelUtilizationCapable"):
                try:
                    payload["devices"]["per_device"][device_id]["solar_data"] = garmin.get_device_solar_data(device_id, start, end)
                except Exception as exc:
                    payload["devices"]["per_device"][device_id]["solar_data_unavailable"] = "{}: {}".format(
                        type(exc).__name__,
                        exc,
                    )

    if source_enabled(sources, "activities"):
        export_activities(garmin, payload, start, end, activity_limit, download_activity_originals)

    if source_enabled(sources, "local"):
        local_export_path = resolve_local_export_path(local_export_path, target)
        payload["local_exports"] = summarize_local_exports(local_export_path, target)

    return payload


def source_enabled(sources: set[str], source: str) -> bool:
    return "all" in sources or source in sources


def fetch_many(target: dict[str, Any], errors: dict[str, str], calls: dict[str, Any], prefix: str = "") -> None:
    for name, call in calls.items():
        try:
            target[name] = call()
        except Exception as exc:
            errors[prefix + name] = "{}: {}".format(type(exc).__name__, exc)


def listify(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("activityList", "items", "results"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return []


def export_activities(
    garmin: Garmin,
    payload: dict[str, Any],
    start: str,
    end: str,
    activity_limit: int,
    download_activity_originals: bool,
) -> None:
    activities: dict[str, Any] = payload["activities"]
    errors: dict[str, str] = payload["errors"]
    fetch_many(
        activities,
        errors,
        {
            "by_date": lambda: garmin.get_activities_by_date(start, end),
            "recent": lambda: garmin.get_activities(0, activity_limit),
            "last_activity": lambda: garmin.get_last_activity(),
            "activity_types": lambda: garmin.get_activity_types(),
            "golf_summary": lambda: garmin.get_golf_summary(0, activity_limit),
        },
        prefix="activities_",
    )

    candidates = listify(activities.get("by_date")) or listify(activities.get("recent"))
    candidates = candidates[:activity_limit]
    activities["details"] = {}
    downloaded: list[dict[str, Any]] = []
    for activity in candidates:
        if not isinstance(activity, dict):
            continue
        activity_id = str(activity.get("activityId") or activity.get("id") or "")
        if not activity_id:
            continue
        bucket: dict[str, Any] = {}
        activities["details"][activity_id] = bucket
        fetch_many(
            bucket,
            errors,
            {
                "summary": lambda activity_id=activity_id: garmin.get_activity(activity_id),
                "details": lambda activity_id=activity_id: garmin.get_activity_details(activity_id),
                "exercise_sets": lambda activity_id=activity_id: garmin.get_activity_exercise_sets(activity_id),
                "gear": lambda activity_id=activity_id: garmin.get_activity_gear(activity_id),
                "split_summaries": lambda activity_id=activity_id: garmin.get_activity_split_summaries(activity_id),
                "splits": lambda activity_id=activity_id: garmin.get_activity_splits(activity_id),
                "typed_splits": lambda activity_id=activity_id: garmin.get_activity_typed_splits(activity_id),
                "weather": lambda activity_id=activity_id: garmin.get_activity_weather(activity_id),
                "hr_timezones": lambda activity_id=activity_id: garmin.get_activity_hr_in_timezones(activity_id),
                "power_timezones": lambda activity_id=activity_id: garmin.get_activity_power_in_timezones(activity_id),
            },
            prefix="activities_{}_".format(activity_id),
        )
        if download_activity_originals:
            try:
                raw = garmin.download_activity(activity_id, Garmin.ActivityDownloadFormat.ORIGINAL)
                out = EXPORT_DIR / "activity_originals" / "{}.fit".format(activity_id)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(raw)
                downloaded.append({"activity_id": activity_id, "path": str(out), "bytes": len(raw)})
            except Exception as exc:
                errors["activities_{}_download_original".format(activity_id)] = "{}: {}".format(type(exc).__name__, exc)
    activities["downloaded_originals"] = downloaded


def summarize_local_exports(path: Path, target: date | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "files": 0,
        "bytes": 0,
        "copied_files": [],
        "fit_records": {},
        "suffix_counts": {},
        "text_records": {},
        "samples": [],
    }
    if not path.exists():
        return summary

    if path.is_file() and path.suffix.lower() == ".zip":
        copy_local_file(summary, path, target, path.name)
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                record_local_file(summary, info.filename, info.file_size)
                suffix = Path(info.filename).suffix.lower()
                if suffix in {".csv", ".json"}:
                    try:
                        data = archive.read(info)
                        add_text_record_summary(summary, info.filename, data)
                    except Exception as exc:
                        summary.setdefault("read_errors", {})[info.filename] = "{}: {}".format(type(exc).__name__, exc)
                elif suffix == ".fit":
                    try:
                        add_fit_record_summary(summary, info.filename, archive.read(info))
                    except Exception as exc:
                        summary.setdefault("fit_decode_errors", {})[info.filename] = "{}: {}".format(type(exc).__name__, exc)
        return summary

    files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    for item in files:
        size = item.stat().st_size
        record_local_file(summary, str(item), size)
        relative_name = item.name if path.is_file() else str(item.relative_to(path))
        copy_local_file(summary, item, target, relative_name)
        suffix = item.suffix.lower()
        if suffix in {".csv", ".json"}:
            try:
                add_text_record_summary(summary, str(item), item.read_bytes())
            except Exception as exc:
                summary.setdefault("read_errors", {})[str(item)] = "{}: {}".format(type(exc).__name__, exc)
        elif suffix == ".fit":
            try:
                add_fit_record_summary(summary, str(item), item.read_bytes())
            except Exception as exc:
                summary.setdefault("fit_decode_errors", {})[str(item)] = "{}: {}".format(type(exc).__name__, exc)
    return summary


def resolve_local_export_path(path: Path, target: date) -> Path:
    dated_manual_path = Path("F:/Garmin") / target.isoformat()
    if path == LOCAL_EXPORT_DIR and dated_manual_path.exists():
        return dated_manual_path
    if path == LOCAL_EXPORT_DIR and not path.exists() and dated_manual_path.exists():
        return dated_manual_path
    if path == LOCAL_EXPORT_DIR and path.exists() and not any(path.rglob("*")) and dated_manual_path.exists():
        return dated_manual_path
    return path


def record_local_file(summary: dict[str, Any], name: str, size: int) -> None:
    suffix = Path(name).suffix.lower() or "<none>"
    summary["files"] += 1
    summary["bytes"] += size
    summary["suffix_counts"][suffix] = summary["suffix_counts"].get(suffix, 0) + 1
    if len(summary["samples"]) < 25:
        summary["samples"].append(
            {
                "name": name,
                "bytes": size,
                "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            }
        )


def add_text_record_summary(summary: dict[str, Any], name: str, data: bytes) -> None:
    suffix = Path(name).suffix.lower()
    digest = hashlib.sha256(data).hexdigest()
    text = decode_text(data)
    record: dict[str, Any] = {"sha256": digest, "bytes": len(data)}
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        rows = 0
        for rows, _ in enumerate(reader, start=1):
            if rows >= 100000:
                break
        record["kind"] = "csv"
        record["columns"] = reader.fieldnames or []
        record["rows_sampled_or_counted"] = rows
    elif suffix == ".json":
        obj = json.loads(text)
        record["kind"] = "json"
        record["top_level_type"] = type(obj).__name__
        record["estimated_records"] = count_json_records(obj)
    summary["text_records"][name] = record


def add_fit_record_summary(summary: dict[str, Any], name: str, data: bytes) -> None:
    if FitFile is None:
        raise RuntimeError("fitparse is not installed. Run: python -m pip install fitparse")

    fit = FitFile(io.BytesIO(data))
    messages: list[dict[str, Any]] = []
    message_counts: dict[str, int] = {}
    for message in fit.get_messages():
        message_counts[message.name] = message_counts.get(message.name, 0) + 1
        messages.append(
            {
                "name": message.name,
                "fields": {
                    field.name: fit_value_json(field.value)
                    for field in message.fields
                },
            }
        )

    summary["fit_records"][name] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "message_count": len(messages),
        "message_counts": message_counts,
        "messages": messages,
    }


def fit_value_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [fit_value_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): fit_value_json(item) for key, item in value.items()}
    return str(value)


def copy_local_file(summary: dict[str, Any], source: Path, target: date | None, relative_name: str) -> None:
    export_date = target.isoformat() if target else date.today().isoformat()
    destination = EXPORT_DIR / "local_files" / export_date / safe_relative_path(relative_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    data = source.read_bytes()
    summary["copied_files"].append(
        {
            "source_path": str(source),
            "stored_path": str(destination),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )


def safe_relative_path(value: str) -> Path:
    parts = [
        part
        for part in Path(value.replace("\\", "/")).parts
        if part not in ("", ".", "..") and not part.endswith(":")
    ]
    return Path(*parts) if parts else Path("local_export_file")


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def count_json_records(obj: Any) -> int:
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        return sum(count_json_records(value) for value in obj.values() if isinstance(value, (dict, list))) or 1
    return 0


def save_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    return path


def scalar_values(payload: Any, key_patterns: tuple[str, ...]) -> list[float]:
    values: list[float] = []

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_lower = str(key).lower()
                if any(pattern in key_lower for pattern in key_patterns):
                    number = to_float(value)
                    if number is not None:
                        values.append(number)
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(payload)
    return values


def first_value(payload: Any, key_patterns: tuple[str, ...]) -> float | None:
    values = scalar_values(payload, key_patterns)
    return values[0] if values else None


def latest_value(payload: Any, key_patterns: tuple[str, ...]) -> float | None:
    values = scalar_values(payload, key_patterns)
    return values[-1] if values else None


def average_value(payload: Any, key_patterns: tuple[str, ...]) -> float | None:
    values = scalar_values(payload, key_patterns)
    return statistics.fmean(values) if values else None


def max_value(payload: Any, key_patterns: tuple[str, ...]) -> float | None:
    values = scalar_values(payload, key_patterns)
    return max(values) if values else None


def to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def derive_metrics(payload: dict[str, Any]) -> dict[str, float | None]:
    daily = payload.get("daily", {})
    stats = daily.get("stats_and_body") or daily.get("stats") or {}
    sleep = daily.get("sleep") or {}
    hrv = daily.get("hrv") or {}
    stress = daily.get("stress") or daily.get("all_day_stress") or {}
    body_battery = daily.get("body_battery") or {}
    composition = daily.get("body_composition") or stats

    daily_sleep = sleep.get("dailySleepDTO") if isinstance(sleep, dict) else {}
    sleep_seconds = dict_number(daily_sleep, "sleepTimeSeconds")
    if sleep_seconds is None and isinstance(daily_sleep, dict):
        sleep_parts = [
            value
            for value in (
                dict_number(daily_sleep, "deepSleepSeconds"),
                dict_number(daily_sleep, "lightSleepSeconds"),
                dict_number(daily_sleep, "remSleepSeconds"),
            )
            if value is not None
        ]
        sleep_seconds = sum(sleep_parts) if sleep_parts else None
    if sleep_seconds is None:
        sleep_seconds = dict_number(stats, "sleepingSeconds") or first_value(
            sleep,
            ("totalsleeptime", "sleepseconds", "sleepingseconds"),
        )
    sleep_hours = sleep_seconds / 3600 if sleep_seconds and sleep_seconds > 24 else sleep_seconds
    hrv_summary = hrv.get("hrvSummary") if isinstance(hrv, dict) else {}
    intensity_minutes = sum(
        value
        for value in (
            dict_number(stats, "moderateIntensityMinutes"),
            dict_number(stats, "vigorousIntensityMinutes"),
        )
        if value is not None
    )

    return {
        "steps": dict_number(stats, "totalSteps") or first_value(stats, ("totalsteps", "steps")),
        "resting_hr": dict_number(stats, "restingHeartRate") or first_value(stats, ("restingheartrate", "restinghr")),
        "active_kcal": dict_number(stats, "activeKilocalories") or first_value(stats, ("activekilocalories", "activecalories")),
        "intensity_minutes": intensity_minutes,
        "sleep_hours": sleep_hours,
        "hrv": dict_number(hrv_summary, "lastNightAvg") or average_value(hrv, ("hrvvalue", "rmssd")),
        "stress": dict_number(stress, "avgStressLevel") or dict_number(stats, "averageStressLevel"),
        "body_battery": body_battery_latest(body_battery) or dict_number(stats, "bodyBatteryMostRecentValue"),
        "weight": latest_value(composition, ("weight", "bodyweight")),
        "body_fat": dict_number(stats, "bodyFat") or latest_value(composition, ("bodyfat", "percentfat")),
        "vo2max": dict_number(stats, "genericVO2Max") or first_value(stats, ("vo2max", "genericvo2max")),
        "max_hr": dict_number(stats, "maxHeartRate") or max_value(stats, ("maxheartrate",)),
    }


def dict_number(payload: Any, key: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    return to_float(payload.get(key))


def body_battery_latest(payload: Any) -> float | None:
    if not isinstance(payload, list) or not payload:
        return None
    latest_day = payload[-1]
    if not isinstance(latest_day, dict):
        return None
    values = latest_day.get("bodyBatteryValuesArray")
    if not isinstance(values, list) or not values:
        return None
    latest_sample = values[-1]
    if not isinstance(latest_sample, list) or len(latest_sample) < 2:
        return None
    return to_float(latest_sample[1])


def render_report(payload: dict[str, Any]) -> str:
    target = payload["date"]
    metrics = derive_metrics(payload)
    errors = payload.get("errors", {})

    lines = [
        "# Garmin Health and Longevity Report - {}".format(target),
        "",
        "This is a trend report, not medical advice, diagnosis, or treatment.",
        "",
        "## Source Coverage",
    ]
    for item in source_coverage(payload):
        lines.append("- " + item)

    lines.extend([
        "",
        "## Key Signals",
    ])
    add_metric(lines, "Steps", metrics["steps"], "steps", whole=True)
    add_metric(lines, "Sleep", metrics["sleep_hours"], "hours")
    add_metric(lines, "Resting heart rate", metrics["resting_hr"], "bpm")
    add_metric(lines, "HRV", metrics["hrv"], "ms")
    add_metric(lines, "Stress", metrics["stress"], "avg")
    add_metric(lines, "Body Battery", metrics["body_battery"], "latest")
    add_metric(lines, "Intensity minutes", metrics["intensity_minutes"], "min", whole=True)
    add_metric(lines, "Active calories", metrics["active_kcal"], "kcal", whole=True)
    add_metric(lines, "Weight", metrics["weight"], "kg")
    add_metric(lines, "Body fat", metrics["body_fat"], "%")
    add_metric(lines, "VO2 max", metrics["vo2max"], "latest")

    lines.extend(["", "## Longevity Read"])
    lines.extend("- " + item for item in longevity_notes(metrics))

    lines.extend(["", "## Tomorrow"])
    lines.extend("- " + item for item in tomorrow_notes(metrics))

    if errors:
        lines.extend(["", "## Missing Garmin Data"])
        lines.append("- Optional endpoints with errors: {}. Full details are in the raw JSON export.".format(len(errors)))
        for name, error in sorted(errors.items())[:8]:
            lines.append("- {}: {}".format(name, error))
        if len(errors) > 8:
            lines.append("- ... {} more omitted from this report.".format(len(errors) - 8))

    lines.append("")
    return "\n".join(lines)


def source_coverage(payload: dict[str, Any]) -> list[str]:
    local = payload.get("local_exports") or {}
    activities = payload.get("activities") or {}
    fit_records = local.get("fit_records", {}) if isinstance(local.get("fit_records"), dict) else {}
    fit_messages = sum(
        item.get("message_count", 0)
        for item in fit_records.values()
        if isinstance(item, dict)
    )
    return [
        "Daily wellness endpoints: {} sections".format(len(payload.get("daily", {}))),
        "History endpoints: {} sections".format(len(payload.get("history", {}))),
        "Profile/account endpoints: {} sections".format(len(payload.get("profile", {}))),
        "Device endpoints: {} sections".format(len(payload.get("devices", {}))),
        "Activity metadata/detail endpoints: {} sections, {} detailed activities, {} original files downloaded".format(
            len(activities),
            len(activities.get("details", {})) if isinstance(activities.get("details"), dict) else 0,
            len(activities.get("downloaded_originals", [])) if isinstance(activities.get("downloaded_originals"), list) else 0,
        ),
        "Local Garmin export folder/file: {} files indexed, {} files copied, {} FIT files decoded, {} FIT messages stored from {}".format(
            local.get("files", 0),
            len(local.get("copied_files", [])) if isinstance(local.get("copied_files"), list) else 0,
            len(fit_records),
            fit_messages,
            local.get("path", ""),
        ),
    ]


def add_metric(lines: list[str], label: str, value: float | None, unit: str, whole: bool = False) -> None:
    if value is None:
        lines.append("- {}: not available".format(label))
        return
    formatted = "{:.0f}".format(value) if whole else "{:.1f}".format(value)
    lines.append("- {}: {} {}".format(label, formatted, unit))


def longevity_notes(metrics: dict[str, float | None]) -> list[str]:
    notes: list[str] = []
    steps = metrics["steps"]
    sleep = metrics["sleep_hours"]
    stress = metrics["stress"]
    hrv = metrics["hrv"]
    resting_hr = metrics["resting_hr"]
    intensity = metrics["intensity_minutes"]

    if steps is not None:
        if steps < 6000:
            notes.append("Movement was low; add easy walking before adding intensity.")
        elif steps >= 8000:
            notes.append("Movement volume was in a useful maintenance range.")
    if sleep is not None:
        if sleep < 7:
            notes.append("Sleep was below the common 7-hour baseline; recovery should lead the next day.")
        elif sleep <= 9:
            notes.append("Sleep duration was in a strong general range.")
    if stress is not None and stress > 50:
        notes.append("Stress was elevated; avoid stacking hard training on top of weak recovery.")
    if hrv is not None:
        notes.append("Interpret HRV against your own baseline; one day is a signal, not a verdict.")
    if resting_hr is not None:
        notes.append("Resting heart rate is most useful as a trend; watch sustained upward drift.")
    if intensity is not None and intensity > 0:
        notes.append("Logged intensity supports cardiovascular fitness when balanced with easy volume and sleep.")
    if not notes:
        notes.append("Not enough daily metrics were available for specific guidance.")
    return notes


def tomorrow_notes(metrics: dict[str, float | None]) -> list[str]:
    sleep = metrics["sleep_hours"]
    stress = metrics["stress"]
    body_battery = metrics["body_battery"]
    steps = metrics["steps"]
    notes: list[str] = []

    if sleep is not None and sleep < 7:
        notes.append("Protect sleep timing tonight; keep late caffeine, alcohol, and screens conservative.")
    if stress is not None and stress > 50:
        notes.append("Use an easy training day or active recovery unless other recovery signals are clearly strong.")
    if body_battery is not None and body_battery < 40:
        notes.append("Keep the first half of the day low-friction: hydration, light exposure, and easy movement.")
    if steps is not None and steps < 6000:
        notes.append("Set a simple floor of two or three short walks.")
    if not notes:
        notes.append("Maintain consistency: sleep window, walking, protein-rich meals, and planned training.")
    return notes


def write_report(payload: dict[str, Any]) -> Path:
    report_path = REPORT_DIR / "{}.md".format(payload["date"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(payload), encoding="utf-8")
    return report_path


def write_assistant_context(payload: dict[str, Any]) -> Path:
    metrics = derive_metrics(payload)
    context = {
        "purpose": "Daily Garmin health and longevity assistant context",
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "sources_requested": payload.get("sources_requested", []),
        "source_coverage": source_coverage(payload),
        "metrics": metrics,
        "available_sections": {
            "daily": sorted(payload.get("daily", {}).keys()),
            "history": sorted(payload.get("history", {}).keys()),
            "profile": sorted(payload.get("profile", {}).keys()),
            "devices": sorted(payload.get("devices", {}).keys()),
            "activities": sorted(payload.get("activities", {}).keys()),
            "local_exports": sorted(payload.get("local_exports", {}).keys()),
        },
        "errors": payload.get("errors", {}),
    }
    return save_json(ASSISTANT_DIR / "garmin_context_latest.json", context)


def run_once(
    target: date,
    days_back: int,
    sources: set[str],
    activity_limit: int,
    download_activity_originals: bool,
    local_export_path: Path,
) -> dict[str, Path]:
    values = read_env_file()
    log("Logging in to Garmin Connect.")
    garmin = login_garmin(values)
    log("Fetching Garmin data for {} from sources: {}.".format(target.isoformat(), ",".join(sorted(sources))))
    payload = fetch_daily_payload(
        garmin,
        target,
        days_back,
        sources,
        activity_limit,
        download_activity_originals,
        local_export_path,
    )

    raw_path = save_json(EXPORT_DIR / "{}.json".format(target.isoformat()), payload)
    report_path = write_report(payload)
    context_path = write_assistant_context(payload)

    log("Saved raw export: {}".format(raw_path))
    log("Saved report: {}".format(report_path))
    log("Saved assistant context: {}".format(context_path))
    return {"raw": raw_path, "report": report_path, "context": context_path}


def install_daily_task(start_time: str) -> str:
    validate_time(start_time)
    command = '"{}"'.format(RUN_DAILY_REPORT_BAT)
    args = [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        command,
        "/SC",
        "DAILY",
        "/ST",
        start_time,
        "/F",
    ]
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def uninstall_daily_task() -> str:
    return subprocess.check_output(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], text=True, stderr=subprocess.STDOUT)


def query_daily_task() -> str:
    try:
        return subprocess.check_output(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        return ""


def validate_time(value: str) -> None:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("Time must use HH:MM, for example 08:10.") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Garmin health data and generate a daily report.")
    parser.add_argument("--date", default=(date.today() - timedelta(days=1)).isoformat(), help="Report date, YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--start-date", help="Backfill start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Backfill end date, YYYY-MM-DD. Defaults to --start-date when omitted.")
    parser.add_argument("--days-back", type=int, default=30, help="Lookback window for trend-capable exports.")
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated sources: all,daily,history,profile,devices,activities,local. Defaults to all.",
    )
    parser.add_argument("--activity-limit", type=int, default=20, help="Maximum activities to detail per run.")
    parser.add_argument(
        "--download-activity-originals",
        action="store_true",
        help="Also download original activity files for selected activities. Can create large exports.",
    )
    parser.add_argument(
        "--local-export",
        type=Path,
        default=LOCAL_EXPORT_DIR,
        help="Garmin Account Data Management ZIP/folder to index. Defaults to local_garmin_exports.",
    )
    parser.add_argument("--install-task", metavar="HH:MM", help="Install Windows daily scheduled report task.")
    parser.add_argument("--uninstall-task", action="store_true", help="Remove Windows daily scheduled report task.")
    parser.add_argument("--query-task", action="store_true", help="Show Windows daily scheduled report task status.")
    return parser.parse_args()


def parse_sources(value: str) -> set[str]:
    requested = {item.strip().lower() for item in value.split(",") if item.strip()}
    if not requested or "all" in requested:
        return set(DEFAULT_SOURCES)
    valid = DEFAULT_SOURCES | {"all"}
    unknown = requested - valid
    if unknown:
        raise ValueError("Unknown sources: {}. Valid sources: {}".format(", ".join(sorted(unknown)), ", ".join(sorted(valid))))
    return requested


def main() -> int:
    args = parse_args()
    try:
        if args.install_task:
            print(install_daily_task(args.install_task))
            return 0
        if args.uninstall_task:
            print(uninstall_daily_task())
            return 0
        if args.query_task:
            output = query_daily_task()
            print(output or "Task is not installed.")
            return 0
        sources = parse_sources(args.sources)
        if args.start_date:
            start = date.fromisoformat(args.start_date)
            end = date.fromisoformat(args.end_date) if args.end_date else start
            if end < start:
                raise ValueError("--end-date must be on or after --start-date.")
            current = start
            while current <= end:
                run_once(
                    current,
                    args.days_back,
                    sources,
                    args.activity_limit,
                    args.download_activity_originals,
                    args.local_export,
                )
                current += timedelta(days=1)
            return 0
        target = date.fromisoformat(args.date)
        run_once(
            target,
            args.days_back,
            sources,
            args.activity_limit,
            args.download_activity_originals,
            args.local_export,
        )
        return 0
    except Exception:
        log("ERROR:\n{}".format(traceback.format_exc()))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
