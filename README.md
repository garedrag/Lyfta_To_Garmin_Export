# Garmin Data Export and Daily Health Report

This project is a local-first app for collecting, exporting, and formatting as much Garmin data as practical from your Garmin Connect account.

The main workflow is:

1. Log in to Garmin Connect using the existing `.env` credentials and cached `garmin_tokens/`.
2. Collect Garmin data from all supported source groups.
3. Save the full raw export locally as JSON.
4. Format a compact assistant context file for Codex/ChatGPT.
5. Generate a daily health and longevity Markdown report.
6. Run automatically every day and post a concise report in the Codex thread.

The app is intended for personal tracking and trend review. It is not medical advice, diagnosis, or treatment.

## Main App

The Garmin export/report app is:

```bat
garmin_health_reporter.py
```

GUI launcher:

```bat
run_health_gui.bat
```

The GUI can:

- Run an export immediately.
- Select Garmin source groups.
- Set date, lookback window, activity limit, and local Garmin export path.
- Enable original activity-file downloads for selected runs.
- Install, remove, and check the daily service.
- Open raw exports, latest report, assistant context, and logs.

Default daily run:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py
```

This defaults to yesterday's date, all practical data source groups, and up to 20 detailed activities.

Run a specific date:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --date 2026-05-05
```

Backfill a historical range:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --start-date 2026-01-01 --end-date 2026-05-05
```

## Exported Files

Each run writes:

- `garmin_exports/YYYY-MM-DD.json`: full raw Garmin export payload for that date.
- `reports/health/YYYY-MM-DD.md`: human-readable health and longevity report.
- `exports/assistant/garmin_context_latest.json`: compact formatted context for Codex/ChatGPT.
- `logs/health-report.log`: run log.

Generated exports are private local data and are ignored by git.

## Garmin Source Groups

By default, the app requests all source groups:

```bat
--sources all
```

Available source groups:

- `daily`: daily wellness and health endpoints.
- `history`: multi-day trend endpoints.
- `profile`: Garmin account/profile, records, goals, badges, workouts, gear, and plans.
- `devices`: Garmin devices and per-device metadata.
- `activities`: activity metadata and per-activity detail endpoints.
- `local`: local Garmin Account Data Management export ZIP/folder indexing.

Choose source groups explicitly:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --sources daily,history,activities --activity-limit 10
```

## Data Collected

The exact data available depends on your Garmin devices and account, but the app currently attempts to collect and format these categories:

- Daily summary, user summary, steps, floors, distance, calories, intensity minutes.
- Sleep, sleep stages, sleep score fields where Garmin returns them.
- HRV, resting heart rate, heart-rate samples, heart-rate zones/timezones.
- Stress, all-day stress, Body Battery, Body Battery events.
- SpO2, respiration, hydration, lifestyle logging.
- Training readiness, morning training readiness, training status, max metrics, fitness age.
- Body composition, weigh-ins, blood pressure where available.
- Endurance score, hill score, race predictions, running tolerance, lactate threshold.
- Weekly steps, weekly stress, weekly intensity minutes, progress summaries.
- Recent activities, date-range activities, activity details, splits, typed splits, split summaries, weather, gear, exercise sets, HR/power timezones.
- Garmin profile, unit system, user settings, goals, personal records, badges, badge challenges.
- Training plans, workouts, cycling FTP, gear, pregnancy summary where available.
- Garmin devices, primary training device, last used device, alarms, device settings, solar data where available.
- Local Garmin export ZIP/folder file inventory, CSV metadata, JSON metadata, file sizes, hashes, and samples.

Garmin may return no data for some endpoints. The app records those results in the raw JSON and keeps the daily report focused on usable signals.

## Original Activity Files

The default run collects activity metadata and details, but does not download original FIT/TCX files because those can be large.

Download original activity files for selected activities:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --date 2026-05-05 --sources activities --activity-limit 5 --download-activity-originals
```

Original files are saved under:

```text
garmin_exports/activity_originals/
```

## Local Garmin Account Export and FIT Storage

Garmin's personal Account Data Management export can contain files that are not always exposed through daily Garmin Connect endpoints.

Put a Garmin export ZIP or extracted folder here:

```text
local_garmin_exports/
```

Or pass a path directly:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --sources local --local-export "C:\path\to\garmin-export.zip"
```

The app now stores substantially more local export data:

- Copies source files into `garmin_exports/local_files/YYYY-MM-DD/`.
- Calculates SHA-256 hashes for copied files.
- Decodes Garmin `.fit` files with `fitparse`.
- Stores FIT message names, fields, and values in the raw JSON export.
- Summarizes CSV and JSON files when present.

If `--local-export` is not provided, the app automatically checks for a dated manual Garmin folder:

```text
F:\Garmin\YYYY-MM-DD
```

For example, an export for `2026-05-06` will automatically include:

```text
F:\Garmin\2026-05-06
```

This is useful when Garmin Connect manually exports daily wellness FIT files into dated folders.

## Daily Automation

Install the Windows daily export task:

```bat
install_daily_health_report.bat
```

Remove it:

```bat
uninstall_daily_health_report.bat
```

Current task:

- Name: `GarminHealthDailyReport`
- Default time: `08:10`
- Command: `run_daily_health_report.bat`

You can also manage the service from the GUI:

```bat
run_health_gui.bat
```

Add this to `.env` to change the time:

```env
DAILY_REPORT_TIME=08:10
```

## Codex Daily Report

This workspace has a Codex thread automation named:

```text
Daily Garmin health and longevity report
```

It runs after the local Garmin export, reads:

- `reports/health/YYYY-MM-DD.md`
- `exports/assistant/garmin_context_latest.json`

Then it posts a concise daily health/longevity report in the Codex thread, including:

- Key signals.
- Recovery/readiness.
- Movement.
- Sleep.
- HRV, stress, Body Battery.
- Practical actions for the day.

## Setup

Install dependencies:

```bat
"C:\Program Files\Python311\python.exe" -m pip install -r requirements.txt
```

Create `.env` from `config.example.env` and set:

```env
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your_password
```

Run once manually:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --sources all --activity-limit 20
```

If Garmin requires MFA or token refresh, use the existing GUI flow in `lyfta_garmin_app.py` to refresh `garmin_tokens/`.

## Testing

Syntax check:

```bat
"C:\Program Files\Python311\python.exe" -m py_compile garmin_health_reporter.py garmin_health_gui.py lyfta_garmin_app.py
```

All-source smoke test:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --date 2026-05-05 --sources all --activity-limit 3
```

Heavy activity-file download test:

```bat
"C:\Program Files\Python311\python.exe" garmin_health_reporter.py --date 2026-05-05 --sources activities --activity-limit 1 --download-activity-originals
```

## Legacy Lyfta to Garmin Sync

The repo also contains the older `lyfta_garmin_app.py` GUI, which transfers Lyfta gym workouts to Garmin Connect through Strava:

1. Lyfta syncs the workout to Strava.
2. The app reads recent strength workouts from Strava.
3. The app creates a FIT activity file.
4. The app uploads that FIT file to Garmin Connect.

Related scripts:

- `run_gui.bat`
- `run_daily_sync.bat`
- `install_daily_sync.bat`
- `uninstall_daily_sync.bat`

The legacy sync still works independently from the Garmin export/report workflow.

### Direct Lyfta to Garmin mode (without Strava)

The **Direct Lyfta → Garmin** GUI button and scheduled `run_daily_sync.bat` read
`WorkoutData.csv` directly, create FIT workouts with exercise sets, and upload
those workouts to Garmin Connect. This path does not call Strava and does not
need Strava credentials. Successful imports are tracked in `direct_synced_ids.json`.

For a safe connection check that does not upload workouts, run:

```bat
"C:\Program Files\PyManager\python.exe" lyfta_garmin_app.py --direct --dry-run
```
