"""Rate limits for the vault's account and data endpoints.

The rates live in ``settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`` so they
can be retuned on telemetry without a code change. The values there are the
design's v1 starting point, not measurements.
"""

from rest_framework.throttling import UserRateThrottle

from workspace.common.throttling import IpRateThrottle


class AccountInitIpThrottle(IpRateThrottle):
    scope = "vault.account.init.ip"


class AccountInitUserThrottle(UserRateThrottle):
    scope = "vault.account.init.user"


class AccountFinalizeIpThrottle(IpRateThrottle):
    scope = "vault.account.finalize.ip"


class AccountEnvelopeBurstThrottle(UserRateThrottle):
    scope = "vault.account.envelope.burst"


class AccountEnvelopeUserThrottle(UserRateThrottle):
    scope = "vault.account.envelope.user"


class AccountEnvelopeIpThrottle(IpRateThrottle):
    """Not redundant with the per-user limit: it is what makes an exfiltration
    spread across several stolen session cookies visible."""

    scope = "vault.account.envelope.ip"


class AccountRotateUserThrottle(UserRateThrottle):
    scope = "vault.account.rotate.user"


class AccountConfirmUserThrottle(UserRateThrottle):
    scope = "vault.account.confirm.user"


class MigrateUserThrottle(UserRateThrottle):
    scope = "vault.migrate.user"
