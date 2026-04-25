# Lyfta to Garmin

This transfers Lyfta gym workouts to Garmin Connect through Strava:

1. Lyfta syncs the workout to Strava.
2. This script reads recent strength workouts from Strava.
3. The script creates a minimal FIT activity file.
4. The script uploads that FIT file to Garmin Connect.

When `WorkoutData.csv` is present and a match is found, Garmin will show activity type, start time, duration, calories, and individual sets with reps and weight. If an original Garmin strength activity exists near the same time, the app copies calories and heart-rate samples from it. Otherwise it estimates strength-training calories from duration, body weight, and `STRENGTH_MET`.

## Setup

1. In Lyfta, enable Strava sync.
2. Create a Strava API app at `https://www.strava.com/settings/api`.
3. Set the Strava app callback domain to `localhost`.
4. Copy `config.example.env` to `.env`.
5. Fill in `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` in `.env`.
6. Export your Lyfta data as CSV and put `WorkoutData.csv` in this folder.
7. Run `run_gui.bat`.

The GUI lets you save config, reset Strava login, and run the sync. On first run, it opens Strava authorization in your browser. After approving access, copy the `code` value from the redirected URL and paste it into the GUI dialog.

The one-file GUI app is `lyfta_garmin_app.py`. It contains the GUI, Strava sync, FIT builder, and Garmin upload logic in one file. You can still use `run_lyfta_to_garmin.bat` for the older terminal version.

If `WorkoutData.csv` is present, the GUI matches Lyfta CSV workouts to Strava workouts by start time and writes reps/weight as FIT set messages. If no CSV match is found, it uploads a summary-only strength activity.

Calorie fallback:

- `USER_WEIGHT_KG` can be set manually in `.env` or the GUI.
- If blank, the app uses Garmin profile weight when available.
- If Garmin profile weight is unavailable, it uses `85 kg`.
- `STRENGTH_MET` defaults to `5.0`, a moderate/vigorous strength-training estimate.

For existing Garmin activities that were already uploaded without exercise details, delete those empty Garmin activities first, then use `Reset Synced IDs` in the GUI and run sync again. Garmin may reject duplicate uploads if the old activities are still present.

## Daily Sync

Run the GUI once first and complete Strava/Garmin login so `strava_tokens.json` and `garmin_tokens/` exist.

In the GUI, set `Daily Sync Time`, then use the daily sync buttons:

- `Install Daily Sync`
- `Remove Daily Sync`
- `Check Daily Sync`
- `Run Daily Sync Now`
- `Open Log`

You can also install it from the command line:

```bat
install_daily_sync.bat
```

This creates a Windows Task Scheduler task named `LyftaToGarminDailySync`, running once per day at `08:00`.

For a manual headless run:

```bat
run_daily_sync.bat
```

Headless logs are written to `logs/daily-sync.log`.

To remove the daily task:

```bat
uninstall_daily_sync.bat
```

The script creates:

- `strava_tokens.json` for Strava OAuth tokens.
- `synced_ids.json` to avoid uploading the same Strava activity twice.
- `garmin_tokens/` for Garmin login tokens.
- `logs/daily-sync.log` for scheduled sync logs.
