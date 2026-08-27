import unittest
from datetime import datetime, timezone

from lyfta_garmin_app import build_direct_workouts


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


if __name__ == "__main__":
    unittest.main()
