import os
import tempfile
import time
import unittest
from pathlib import Path

from pulsepc.core import (
    MIN_FILE_AGE_DAYS,
    _within_root,
    bytes_human,
    clean_temp,
    recommendations,
    report_payload,
    scan_temp,
)


class SafeTempCleanerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="pulsepc-tests-")
        self.root = Path(self.folder.name)
        self.reference_time = time.time()
        self.old = self.reference_time - 10 * 86400
        self.recent = self.reference_time - 60

    def tearDown(self):
        self.folder.cleanup()

    def create_file(self, relative, content=b"test", age=None):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        t = self.old if age is None else age
        os.utime(target, (t, t))
        return target

    def test_scan_only_old_files(self):
        old = self.create_file("a/old.tmp", b"older")
        newer = self.create_file("a/new.tmp", b"newer", self.recent)
        result = scan_temp(self.root, now=self.reference_time)
        self.assertEqual([item.path for item in result.files], [str(old)])
        self.assertEqual(result.bytes_total, 5)
        self.assertTrue(newer.exists())

    def test_manual_cleanup_deletes_only_scanned_old_files(self):
        old = self.create_file("old.tmp", b"old")
        newer = self.create_file("new.tmp", b"new", self.recent)
        scan = scan_temp(self.root, now=self.reference_time)
        result = clean_temp(scan, now=self.reference_time)
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(result.removed_bytes, 3)
        self.assertFalse(old.exists())
        self.assertTrue(newer.exists())
        self.assertTrue(self.root.exists())

    def test_does_not_remove_file_changed_after_scan(self):
        old = self.create_file("old.tmp")
        scan = scan_temp(self.root, now=self.reference_time)
        old.write_bytes(b"replaced")
        result = clean_temp(scan, now=self.reference_time)
        self.assertEqual(result.removed_count, 0)
        self.assertTrue(old.exists())

    def test_does_not_traverse_symbolic_links(self):
        with tempfile.TemporaryDirectory(prefix="pulsepc-outside-") as external:
            outside = Path(external) / "outside.tmp"
            outside.write_text("important", encoding="utf-8")
            os.utime(outside, (self.old, self.old))
            link = self.root / "link"
            try:
                link.symlink_to(Path(external), target_is_directory=True)
            except (NotImplementedError, PermissionError, OSError):
                self.skipTest("Symlinki niedostępne na tym systemie")
            result = scan_temp(self.root, now=self.reference_time)
            self.assertEqual(len(result.files), 0)
            self.assertTrue(outside.exists())

    def test_does_not_follow_file_symlink(self):
        with tempfile.TemporaryDirectory(prefix="pulsepc-outside-") as external:
            outside = Path(external) / "outside.tmp"
            outside.write_bytes(b"should remain")
            os.utime(outside, (self.old, self.old))
            try:
                (self.root / "shortcut.tmp").symlink_to(outside)
            except (NotImplementedError, PermissionError, OSError):
                self.skipTest("Symlinki niedostępne na tym systemie")
            scan = scan_temp(self.root, now=self.reference_time)
            self.assertEqual(len(scan.files), 0)
            self.assertTrue(outside.exists())

    def test_common_prefix_is_not_same_directory(self):
        self.assertFalse(_within_root(Path(str(self.root) + "-outside") / "x", self.root))
        self.assertFalse(_within_root(self.root, self.root))

    def test_too_recent_age_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            scan_temp(self.root, min_age_days=MIN_FILE_AGE_DAYS - 1)

    def test_scan_limit_is_reported(self):
        for i in range(4):
            self.create_file(f"old{i}.tmp")
        scan = scan_temp(self.root, max_files=2, now=self.reference_time)
        self.assertEqual(len(scan.files), 2)
        self.assertTrue(scan.limited)


class OtherTests(unittest.TestCase):
    def test_size_formatting(self):
        self.assertEqual(bytes_human(0), "0 B")
        self.assertEqual(bytes_human(1024), "1.0 KB")

    def test_recommendations_and_private_report(self):
        data = {"ram_percent": 91, "disk_percent": 32, "cpu_percent": 15, "uptime_seconds": 200}
        tips = recommendations(data)
        self.assertTrue(any(t["title"] == "Wysokie użycie RAM" for t in tips))
        report = report_payload(data, [{"name": "test.exe"}], [{"command": "C:\\secret\\run.exe"}], None)
        self.assertEqual(report["startup_count"], 1)
        self.assertNotIn("C:\\secret", str(report))


if __name__ == "__main__":
    unittest.main()
