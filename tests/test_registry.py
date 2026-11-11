"""Testy zmian w rejestrze na sztucznym backendzie; nie ruszają Windows."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pulsepc import registry


class FakeKey:
    def __init__(self, store, path):
        self.store = store
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeWinReg:
    HKEY_CURRENT_USER = 1
    KEY_READ = 0x1
    KEY_QUERY_VALUE = 0x2
    KEY_SET_VALUE = 0x4
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self):
        self.store = {registry.RUN_KEY: {}, registry.DISABLED_KEY: {}}

    def OpenKey(self, hive, path, reserved=0, access=0):
        assert hive == self.HKEY_CURRENT_USER
        if path not in self.store:
            raise FileNotFoundError(path)
        return FakeKey(self.store, path)

    def CreateKeyEx(self, hive, path, reserved=0, access=0):
        assert hive == self.HKEY_CURRENT_USER
        self.store.setdefault(path, {})
        return FakeKey(self.store, path)

    def QueryValueEx(self, key, name):
        try:
            return self.store[key.path][name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    def EnumValue(self, key, index):
        values = list(self.store[key.path].items())
        if index >= len(values):
            raise OSError("Koniec")
        name, (command, kind) = values[index]
        return name, command, kind

    def SetValueEx(self, key, name, reserved, kind, command):
        self.store[key.path][name] = (command, kind)

    def DeleteValue(self, key, name):
        del self.store[key.path][name]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeWinReg()
        self.temp = tempfile.TemporaryDirectory(prefix="pulsepc-regtest-")
        self.patcher = patch.object(registry, "_winreg", return_value=self.fake)
        self.patcher.start()
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.patcher.stop()
        self.temp.cleanup()

    def create_entry(self, name="MyApp", command=r'"C:\Program Files\MyApp\myapp.exe"'):
        entry = registry.RunEntry(name, command, self.fake.REG_SZ, True)
        self.fake.store[registry.RUN_KEY][name] = (command, self.fake.REG_SZ)
        return entry

    def test_disabling_writes_backup_and_can_restore(self):
        entry = self.create_entry()
        filename = registry.disable_user_run(entry)
        self.assertFalse(entry.name in self.fake.store[registry.RUN_KEY])
        self.assertEqual(self.fake.store[registry.DISABLED_KEY][entry.name], (entry.command, entry.kind))
        self.assertTrue(filename.exists())
        backup = filename.read_text(encoding="utf-16")
        self.assertIn('"MyApp"=', backup)
        self.assertIn(r'C:\\Program Files\\MyApp\\myapp.exe', backup)
        disabled = registry.list_user_run()
        self.assertEqual(disabled, [registry.RunEntry(entry.name, entry.command, entry.kind, False)])
        registry.restore_user_run(disabled[0])
        self.assertEqual(self.fake.store[registry.RUN_KEY][entry.name], (entry.command, entry.kind))
        self.assertNotIn(entry.name, self.fake.store[registry.DISABLED_KEY])

    def test_does_not_disable_stale_entry(self):
        entry = self.create_entry()
        self.fake.store[registry.RUN_KEY][entry.name] = ("other.exe", self.fake.REG_SZ)
        with self.assertRaises(ValueError):
            registry.disable_user_run(entry)
        self.assertEqual(self.fake.store[registry.RUN_KEY][entry.name][0], "other.exe")
        self.assertFalse(self.fake.store[registry.DISABLED_KEY])

    def test_does_not_overwrite_existing_disabled(self):
        entry = self.create_entry()
        self.fake.store[registry.DISABLED_KEY][entry.name] = ("other.exe", self.fake.REG_SZ)
        with self.assertRaises(ValueError):
            registry.disable_user_run(entry)
        self.assertIn(entry.name, self.fake.store[registry.RUN_KEY])

    def test_does_not_overwrite_active_during_restore(self):
        disabled = registry.RunEntry("A", "old.exe", self.fake.REG_SZ, False)
        self.fake.store[registry.DISABLED_KEY]["A"] = ("old.exe", self.fake.REG_SZ)
        self.fake.store[registry.RUN_KEY]["A"] = ("new.exe", self.fake.REG_SZ)
        with self.assertRaises(ValueError):
            registry.restore_user_run(disabled)
        self.assertEqual(self.fake.store[registry.RUN_KEY]["A"][0], "new.exe")

    def test_export_of_expand_sz(self):
        text = registry.format_reg_backup("Start", r"%LOCALAPPDATA%\Test\app.exe", self.fake.REG_EXPAND_SZ)
        self.assertIn('"Start"=hex(2):', text)
        self.assertIn('25,00,4c,00', text)
        self.assertTrue(text.endswith("\r\n"))

    def test_rejects_unsupported_types(self):
        entry = self.create_entry()
        self.fake.store[registry.RUN_KEY][entry.name] = (entry.command, 3)
        with self.assertRaises(ValueError):
            registry.disable_user_run(registry.RunEntry(entry.name, entry.command, 3, True))
        self.assertTrue(self.fake.store[registry.RUN_KEY])

    def test_registry_backup_does_not_export_other_entries(self):
        self.create_entry("A", "a.exe")
        self.create_entry("B", "b.exe")
        filename = registry.disable_user_run(registry.RunEntry("A", "a.exe", self.fake.REG_SZ, True))
        text = filename.read_text(encoding="utf-16")
        self.assertNotIn('"B"=', text)
        self.assertIn('"A"=', text)
        self.assertIn("B", self.fake.store[registry.RUN_KEY])


if __name__ == "__main__":
    unittest.main()
