import importlib.util
import json
import tempfile
from importlib import metadata
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase


def _load_script():
    path = settings.BASE_DIR / "scripts" / "check_licenses.py"
    spec = importlib.util.spec_from_file_location("check_licenses", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_licenses = _load_script()


def _distribution(root, *, metadata_lines, notices=None):
    """An installed distribution on disk: METADATA, RECORD and its notices."""
    dist_info = Path(root) / "pkg-1.0.dist-info"
    (dist_info / "licenses").mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "\n".join(
            ["Metadata-Version: 2.4", "Name: pkg", "Version: 1.0", *metadata_lines]
        )
    )
    record = []
    for name, text in (notices or {}).items():
        (dist_info / "licenses" / name).write_text(text)
        record.append(f"pkg-1.0.dist-info/licenses/{name},,")
    (dist_info / "RECORD").write_text("\n".join(record))
    return metadata.PathDistribution(dist_info)


class EvaluateTests(SimpleTestCase):
    def test_permissive_and_weak_copyleft_licenses_are_allowed(self):
        for expression in (
            "MIT",
            "BSD-3-Clause",
            "Apache-2.0",
            "ISC",
            "Zlib",
            "HPND",
            "MIT-CMU",
            "LGPL-3.0-only",
            "LGPL-2.1-or-later",
            "MPL-2.0",
            "(MIT AND Zlib)",
            "Apache-2.0 AND CNRI-Python",
            "MPL-2.0 AND (Apache-2.0 OR MIT)",
        ):
            with self.subTest(expression=expression):
                self.assertTrue(check_licenses.evaluate(expression))

    def test_strong_copyleft_and_unusable_text_are_refused(self):
        for expression in (
            "GPL-2.0-only",
            "GPL-3.0-or-later",
            "AGPL-3.0-only",
            "GNU General Public License v3 (GPLv3)",
            "GPL-3.0-or-later WITH GCC-exception-3.1",
            "MIT AND GPL-2.0-only",
            "SSPL-1.0",
            "Proprietary",
            "UNKNOWN",
            "SEE LICENSE IN LICENSE.md",
            "MIT OR",
            "",
        ):
            with self.subTest(expression=expression):
                self.assertFalse(check_licenses.evaluate(expression))

    def test_a_dual_license_needs_one_allowed_side(self):
        for expression in (
            "MIT OR GPL-2.0-only",
            "GPL-3.0-or-later OR Apache-2.0",
            "MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later",
        ):
            with self.subTest(expression=expression):
                self.assertTrue(check_licenses.evaluate(expression))
        self.assertFalse(check_licenses.evaluate("GPL-2.0-only OR AGPL-3.0-only"))

    def test_free_text_names_of_the_same_licenses_are_recognised(self):
        for text in (
            "3-Clause BSD License",
            "BSD 3-Clause OR Apache-2.0",
            "Python Software Foundation License",
            "Apache Software License",
            "Mozilla Public License 2.0 (MPL 2.0)",
            "GNU Lesser General Public License v3 (LGPLv3)",
        ):
            with self.subTest(text=text):
                self.assertTrue(check_licenses.evaluate(text))


class PythonLicenseTests(SimpleTestCase):
    def _verdict(self, *metadata_lines):
        with tempfile.TemporaryDirectory() as root:
            dist = _distribution(root, metadata_lines=metadata_lines)
            return check_licenses.python_license(dist)

    def test_license_expression_decides(self):
        self.assertTrue(self._verdict("License-Expression: MIT")[0])
        self.assertFalse(self._verdict("License-Expression: GPL-3.0-only")[0])

    def test_expression_wins_over_a_classifier(self):
        allowed, _ = self._verdict(
            "License-Expression: GPL-3.0-only",
            "Classifier: License :: OSI Approved :: MIT License",
        )
        self.assertFalse(allowed)

    def test_classifiers_are_the_choices_of_a_dual_license(self):
        dual = self._verdict(
            "Classifier: License :: OSI Approved :: MIT License",
            "Classifier: License :: OSI Approved :: GNU General Public License (GPL)",
        )
        self.assertTrue(dual[0])
        gpl_only = self._verdict(
            "Classifier: License :: OSI Approved :: GNU General Public License (GPL)"
        )
        self.assertFalse(gpl_only[0])

    def test_a_license_blob_is_not_metadata(self):
        blob = "License: " + "Permission is hereby granted, free of charge " * 5
        allowed, declared = self._verdict(blob)
        self.assertFalse(allowed)
        self.assertEqual(declared, "no usable license metadata")

    def test_a_short_license_field_is_read(self):
        self.assertTrue(self._verdict("License: Apache License 2.0")[0])
        self.assertFalse(self._verdict("License: GNU GENERAL PUBLIC LICENSE")[0])


class BundledNoticeTests(SimpleTestCase):
    def _flagged(self, notices):
        with tempfile.TemporaryDirectory() as root:
            dist = _distribution(root, metadata_lines=[], notices=notices)
            return check_licenses.bundled_copyleft(dist)

    def test_a_gpl_library_named_in_a_bundled_notice_is_flagged(self):
        flagged = self._flagged(
            {
                "LICENSES_bundled.txt": (
                    "Name: libheif\nLicense: LGPLv3\n\nName: x265\nLicense: GPLv2\n"
                )
            }
        )
        self.assertEqual(flagged, ["License: GPLv2"])

    def test_lgpl_and_runtime_exceptions_are_not_flagged(self):
        flagged = self._flagged(
            {
                "LICENSES_bundled.txt": (
                    "License: LGPLv3\n"
                    "License: GPL-3.0-with-GCC-exception (libgcc, libstdc++)\n"
                )
            }
        )
        self.assertEqual(flagged, [])

    def test_the_license_file_of_the_package_itself_is_not_a_notice(self):
        flagged = self._flagged({"LICENSE": "the Lesser GPL, version 2.1\n"})
        self.assertEqual(flagged, [])


class NpmLockTests(SimpleTestCase):
    def _problems(self, packages):
        with tempfile.TemporaryDirectory() as root:
            lock = Path(root) / "package-lock.json"
            lock.write_text(json.dumps({"packages": {"": {}, **packages}}))
            with mock.patch.object(check_licenses, "FRONTEND_LOCK", lock):
                return check_licenses.check_npm()

    def test_a_bundled_gpl_package_is_a_problem_and_a_dev_one_is_not(self):
        count, problems = self._problems(
            {
                "node_modules/ok": {"version": "1.0.0", "license": "MIT"},
                "node_modules/weak": {"version": "1.0.0", "license": "LGPL-3.0"},
                "node_modules/bad": {"version": "2.0.0", "license": "GPL-3.0"},
                "node_modules/tool": {
                    "version": "3.0.0",
                    "license": "GPL-3.0",
                    "dev": True,
                },
                "node_modules/none": {"version": "4.0.0"},
            }
        )
        self.assertEqual(count, 4)
        self.assertEqual(problems, ["bad 2.0.0: GPL-3.0", "none 4.0.0: no license"])

    def test_a_nested_package_is_named_by_its_own_directory(self):
        _, problems = self._problems(
            {
                "node_modules/a/node_modules/b": {
                    "version": "1.0.0",
                    "license": "GPL-2.0",
                }
            }
        )
        self.assertEqual(problems, ["b 1.0.0: GPL-2.0"])

    def test_a_reviewed_package_passes(self):
        _, problems = self._problems(
            {
                "node_modules/rgbcolor": {
                    "version": "1",
                    "license": "MIT OR SEE LICENSE IN FEEL-FREE.md",
                }
            }
        )
        self.assertEqual(problems, [])

    def test_the_frontend_lockfile_of_the_repository_passes(self):
        count, problems = check_licenses.check_npm()
        self.assertGreater(count, 0)
        self.assertEqual(problems, [])
