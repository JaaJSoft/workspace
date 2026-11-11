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


def _distribution(root, *, metadata_lines, notices=None, files=None):
    """An installed distribution on disk: METADATA, RECORD, its notices under
    ``licenses/`` and any other *files*, keyed by their path in site-packages."""
    dist_info = Path(root) / "pkg-1.0.dist-info"
    (dist_info / "licenses").mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "\n".join(
            ["Metadata-Version: 2.4", "Name: pkg", "Version: 1.0", *metadata_lines]
        ),
        encoding="utf-8",
    )
    record = []
    for name, text in (notices or {}).items():
        (dist_info / "licenses" / name).write_text(text, encoding="utf-8")
        record.append(f"pkg-1.0.dist-info/licenses/{name},,")
    for relative, text in (files or {}).items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        record.append(f"{relative},,")
    (dist_info / "RECORD").write_text("\n".join(record), encoding="utf-8")
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

    def test_only_the_mpl_version_the_policy_names_is_allowed(self):
        self.assertTrue(check_licenses.evaluate("MPL-2.0"))
        self.assertFalse(check_licenses.evaluate("MPL-1.1"))
        self.assertFalse(check_licenses.evaluate("MPL-1.0"))

    def test_unbalanced_parentheses_are_refused(self):
        for expression in ("(MIT", "MIT)", ")", "(MIT OR Apache-2.0", "()"):
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
        self.assertTrue(declared.startswith("unusable license text: "), declared)

    def test_no_license_and_a_license_text_are_told_apart(self):
        # A package reviewed for having no license metadata must be checked
        # again the day it ships a license text, whatever that text says.
        _, empty = self._verdict()
        _, blob = self._verdict(
            "License: " + "GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007 " * 4
        )
        self.assertEqual(empty, "no license metadata")
        self.assertNotEqual(blob, empty)
        self.assertTrue(check_licenses.is_reviewed("pypi", "py-rust-stemmers", empty))
        self.assertFalse(check_licenses.is_reviewed("pypi", "py-rust-stemmers", blob))

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

    def test_the_spelled_out_gpl_and_agpl_names_are_flagged(self):
        flagged = self._flagged(
            {
                "LICENSES_bundled.txt": (
                    "Name: libfoo\nLicense: GNU General Public License v2\n"
                    "Name: libbar\nLicense: GNU Affero General Public License v3\n"
                    "Name: libbaz\nLicense: GNU Lesser General Public License v3\n"
                    "Name: libqux\nLicense: GNU Library General Public License v2\n"
                )
            }
        )
        self.assertEqual(
            flagged,
            [
                "License: GNU General Public License v2",
                "License: GNU Affero General Public License v3",
            ],
        )

    def test_a_license_text_quoting_the_gpl_is_not_a_gpl_library(self):
        # Both lines of onnxruntime's ThirdPartyNotices.txt: a wrapped LGPL
        # name, and the MPL-2.0 text defining its Secondary Licenses.
        flagged = self._flagged(
            {
                "ThirdPartyNotices.txt": (
                    "extent required to debug changes to any libraries licensed "
                    "under the GNU Lesser\n"
                    "General Public License.\n"
                    '1.12. "Secondary License"\n'
                    "    means either the GNU General Public License, Version 2.0, "
                    "the GNU\n"
                )
            }
        )
        self.assertEqual(flagged, [])

    def test_a_notice_outside_the_licenses_directory_is_read(self):
        with tempfile.TemporaryDirectory() as root:
            dist = _distribution(
                root,
                metadata_lines=[],
                files={"pkg.libs/LICENSES_bundled.txt": "Name: x265\nLicense: GPLv2\n"},
            )
            self.assertEqual(check_licenses.bundled_copyleft(dist), ["License: GPLv2"])

    def test_a_package_license_file_outside_licenses_is_not_a_notice(self):
        with tempfile.TemporaryDirectory() as root:
            dist = _distribution(
                root,
                metadata_lines=[],
                files={"pkg/LICENSE.txt": "the GNU General Public License\n"},
            )
            self.assertEqual(check_licenses.bundled_copyleft(dist), [])

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
            lock.write_text(
                json.dumps({"packages": {"": {}, **packages}}), encoding="utf-8"
            )
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

    def test_a_reviewed_package_whose_license_changed_is_reviewed_again(self):
        _, problems = self._problems(
            {"node_modules/rgbcolor": {"version": "2", "license": "GPL-3.0"}}
        )
        self.assertEqual(problems, ["rgbcolor 2: GPL-3.0"])

    def test_the_frontend_lockfile_of_the_repository_passes(self):
        count, problems = check_licenses.check_npm()
        self.assertGreater(count, 0)
        self.assertEqual(problems, [])
