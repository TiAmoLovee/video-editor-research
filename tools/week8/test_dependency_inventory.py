"""Regression checks for inventory evidence boundaries, without package installs."""
import unittest

from packaging.requirements import Requirement

from build_dependency_inventory import active, license_signal, npm_components, python_components


class InventoryTests(unittest.TestCase):
    def test_python_mismatched_version_does_not_borrow_license(self):
        records = [{"name": "sample", "version": "1.0", "requirement": "sample==1.0"}]
        snapshot = {"environment": {}, "packages": {"sample": {"version": "2.0", "license": {"declared_label": "MIT"}}}}
        rows = python_components(records, snapshot)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["license"]["declared_label"])
        self.assertFalse(rows[0]["metadata_matches_pinned_version"])
        self.assertEqual(rows[1]["version_basis"], "installed environment only; not a repository lock")

    def test_duplicate_pins_retain_each_source_and_do_not_duplicate_package(self):
        records = [{"name": "sample", "version": "1.0", "requirement": "sample==1.0", "source": p}
                   for p in ["runtime.txt", "dev.txt"]]
        snapshot = {"environment": {}, "packages": {"sample": {"version": "1.0", "license": {"declared_label": "MIT"}}}}
        rows = python_components(records, snapshot)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["declared_at"]), 2)

    def test_platform_and_extra_markers(self):
        req = Requirement("redis; extra == 'redis' and sys_platform == 'win32'")
        self.assertTrue(active(req, {"sys_platform": "win32"}, ["redis"]))
        self.assertFalse(active(req, {"sys_platform": "linux"}, ["redis"]))
        self.assertFalse(active(req, {"sys_platform": "win32"}))

    def test_npm_scoped_and_nested_versions_keep_install_identity(self):
        lock = {"packages": {"": {"dependencies": {"@a/x": "1"}},
                              "node_modules/@a/x": {"version": "1", "license": "MIT"},
                              "node_modules/y/node_modules/@a/x": {"version": "2"}}}
        rows = npm_components(lock)
        self.assertEqual([x["name"] for x in rows], ["@a/x", "@a/x"])
        self.assertEqual([x["role"] for x in rows], ["direct", "transitive"])
        self.assertEqual(rows[1]["status"], "license-pending")

    def test_composite_license_not_flattened_to_permissive(self):
        self.assertEqual(license_signal("MIT OR GPL-3.0-only"), "compound-or-multiple-labels-review")
        self.assertEqual(license_signal(None), "missing-license-metadata")
        self.assertEqual(license_signal("LGPL-2.1-or-later"), "contains-weak-copyleft-family-label-review")


if __name__ == "__main__":
    unittest.main()
