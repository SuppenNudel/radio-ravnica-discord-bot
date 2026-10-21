import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from modules import date_time_interpretation as dates


class DateTimeInterpretationTests(unittest.TestCase):
    def setUp(self):
        self.base = dates.timezone.localize(datetime(2026, 10, 21, 12, 34, 56))
        self.settings_patch = patch.dict(dates.settings, {"RELATIVE_BASE": self.base})
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def assert_parses_as(self, value, expected):
        parsed = dates.parse_date(value)
        self.assertIsNotNone(parsed, value)
        self.assertEqual(parsed, dates.timezone.localize(expected), value)

    def test_german_date_with_compact_hour(self):
        self.assert_parses_as("22.10. 17uhr", datetime(2026, 10, 22, 17))

    def test_iso_date_and_time(self):
        self.assert_parses_as("2026-10-21 17:00", datetime(2026, 10, 21, 17))

    def test_tomorrow_evening(self):
        self.assert_parses_as("morgen abend", datetime(2026, 10, 22, 18))

    def test_relative_day_preserves_explicit_hour(self):
        for value in ("morgen 17uhr", "morgen 17 Uhr", "morgen um 17 Uhr"):
            with self.subTest(value=value):
                self.assert_parses_as(value, datetime(2026, 10, 22, 17))

    def test_explicit_time_overrides_evening_default(self):
        self.assert_parses_as("morgen abend 19uhr", datetime(2026, 10, 22, 19))

    def test_minutes_are_preserved(self):
        self.assert_parses_as("22.10. 17:30 Uhr", datetime(2026, 10, 22, 17, 30))

    def test_existing_german_inputs(self):
        for value in ("22.10. um 17:00", "morgen um 17:00", "am 22.10.2026 um 17:00 Uhr"):
            with self.subTest(value=value):
                self.assert_parses_as(value, datetime(2026, 10, 22, 17))

    def test_relative_duration_is_unchanged(self):
        self.assertEqual(dates.parse_date("in 2 Stunden"), self.base + timedelta(hours=2))

    def test_iso_timezone_is_converted_to_berlin(self):
        for value in ("2026-10-21T15:00:00Z", "2026-10-21T15:00:00+00:00"):
            with self.subTest(value=value):
                self.assert_parses_as(value, datetime(2026, 10, 21, 17))

    def test_iso_date_without_time(self):
        self.assert_parses_as("2026-10-21", datetime(2026, 10, 21))

    def test_german_date_without_year_rolls_forward(self):
        self.assert_parses_as("02.01. 17uhr", datetime(2027, 1, 2, 17))

    def test_invalid_input_returns_none(self):
        for value in ("", "kein Datum", "2026-13-40 17:00"):
            with self.subTest(value=value):
                self.assertIsNone(dates.parse_date(value))