import unittest
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from lyfta_garmin_app import build_direct_workouts, load_lyfta_workouts, update_lyfta_csv_from_api


class DirectWorkoutBuildTests(unittest.TestCase):
    def test_builds_garmin_activity_from_lyfta_csv_workout(self):
        lyfta = {
            "title": "Push day",
            "started_at": datetime(2026, 6, 9, 14, 35, 43),
            "duration": 3600,
            "sets": [{"exercise": "Bench Press", "reps": 10, "weight": 100.0, "set_type": "NORMAL_SET"}],
        }

        activity = build_direct_workouts([lyfta])[0]

        self.assertEqual(activity["name"], "Push day")
        expected_start = lyfta["started_at"].replace(
            tzinfo=datetime.now().astimezone().tzinfo
        ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(activity["start_date"], expected_start)
        self.assertEqual(activity["elapsed_time"], 3600)
        self.assertEqual(activity["sport_type"], "WeightTraining")
        self.assertTrue(activity["id"].startswith("lyfta:"))

    def test_updates_csv_from_api_without_duplicate_workouts(self):
        lyfta = {
            "source_id": "lyfta-123",
            "title": "Leg day",
            "started_at": datetime(2026, 8, 26, 18, 15, 0),
            "duration": 5400,
            "sets": [
                {"exercise": "Squat", "reps": 8, "weight": 100.0, "set_type": "0"},
                {"exercise": "Squat", "reps": 6, "weight": 110.0, "set_type": "0"},
            ],
        }

        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "WorkoutData.csv"
            logs = []
            with patch("lyfta_garmin_app.fetch_lyfta_workouts_from_api", return_value=[lyfta]):
                added_first = update_lyfta_csv_from_api(csv_path, "fake-key", 3650, logs.append)
                added_second = update_lyfta_csv_from_api(csv_path, "fake-key", 3650, logs.append)

            workouts = load_lyfta_workouts(csv_path)

        self.assertEqual(added_first, 1)
        self.assertEqual(added_second, 0)
        self.assertEqual(len(workouts), 1)
        self.assertEqual(workouts[0]["sets"][0]["exercise"], "Squat")
        self.assertEqual(workouts[0]["sets"][0]["reps"], 8)
        self.assertEqual(workouts[0]["sets"][0]["weight"], 100.0)


if __name__ == "__main__":
    unittest.main()
