"""The blob storage backend is chosen from the environment."""

import importlib
import os
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

import workspace.settings.storage as storage_settings

_VARIABLES = ("STORAGE_BACKEND", "S3_BUCKET", "S3_ENDPOINT_URL", "S3_PREFIX")


class StorageSettingsTests(SimpleTestCase):
    def _load(self, **env):
        """The settings module as it reads with exactly *env* set."""
        cleared = {name: "" for name in _VARIABLES}
        with mock.patch.dict(os.environ, {**cleared, **env}):
            for name in _VARIABLES:
                if name not in env:
                    del os.environ[name]
            try:
                return importlib.reload(storage_settings)
            finally:
                self.addCleanup(importlib.reload, storage_settings)

    def test_the_filesystem_is_the_default(self):
        module = self._load()

        self.assertEqual(module.STORAGE_BACKEND, "local")
        self.assertEqual(module.STORAGES["default"]["OPTIONS"], {"backend": "local"})
        self.assertEqual(
            module.STORAGES["files"]["OPTIONS"],
            {"backend": "local", "allow_overwrite": True, "verbatim_names": True},
        )

    def test_object_storage_from_the_environment(self):
        module = self._load(
            STORAGE_BACKEND="S3",
            S3_BUCKET="workspace-media",
            S3_ENDPOINT_URL="http://minio:9000",
            S3_PREFIX="tenant",
        )

        default = module.STORAGES["default"]["OPTIONS"]
        self.assertEqual(default["backend"], "s3")
        self.assertEqual(default["bucket"], "workspace-media")
        self.assertEqual(default["endpoint_url"], "http://minio:9000")
        self.assertEqual(default["prefix"], "tenant")
        self.assertNotIn("allow_overwrite", default)
        self.assertEqual(
            module.STORAGES["files"]["OPTIONS"],
            {**default, "allow_overwrite": True, "verbatim_names": True},
        )

    def test_a_configured_bucket_is_reachable_while_the_disk_is_in_use(self):
        module = self._load(S3_BUCKET="workspace-media")

        self.assertEqual(module.STORAGE_BACKEND, "local")
        self.assertEqual(module.STORAGES["default"]["OPTIONS"], {"backend": "local"})
        self.assertEqual(module.BLOB_BACKENDS["s3"]["bucket"], "workspace-media")

    def test_object_storage_needs_a_bucket(self):
        with self.assertRaises(ImproperlyConfigured):
            self._load(STORAGE_BACKEND="s3")

    def test_an_unknown_backend_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            self._load(STORAGE_BACKEND="ftp")
