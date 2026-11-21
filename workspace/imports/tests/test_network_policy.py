from django.test import SimpleTestCase, override_settings

from workspace.common.url_guard import UnsafeUrl, check_remote_url
from workspace.imports.services.network_policy import imports_network_policy


class ImportsNetworkPolicyTests(SimpleTestCase):
    @override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=False, IMPORTS_ALLOWED_HOSTS=[])
    def test_private_networks_are_refused_with_the_admin_hint(self):
        with self.assertRaisesRegex(UnsafeUrl, "allow private networks for imports"):
            check_remote_url("https://192.168.1.10/dav", imports_network_policy())

    @override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=True)
    def test_the_setting_opens_private_networks(self):
        check_remote_url("https://192.168.1.10/dav", imports_network_policy())

    @override_settings(IMPORTS_ALLOWED_HOSTS=["NAS.local"])
    def test_allowed_hosts_come_from_the_setting(self):
        self.assertEqual(imports_network_policy().allowed_hosts, {"nas.local"})
