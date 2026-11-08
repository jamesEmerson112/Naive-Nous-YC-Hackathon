"""Contract version and the skew rule (BUILD §2 principle 7, APIs §1).

Every device request carries `contract_version`. The server accepts the current
version and the one before it, so a device one release behind keeps working.

When to bump (D28): only breaking changes bump `CONTRACT_VERSION` (a removed or
renamed field, a new required field, or a changed meaning); set
`PREVIOUS_CONTRACT_VERSION` to the old value so the server keeps accepting it.
Optional, additive changes don't bump it. Every contract change, bump or not,
updates docs/cloud-platform-planning/cloud-platform-apis.md and gets a note to
the device teammate.
"""

CONTRACT_VERSION = "0.1"

# v0.1 is the first contract, so there is no previous version yet.
PREVIOUS_CONTRACT_VERSION: str | None = None

SUPPORTED_VERSIONS: tuple[str, ...] = tuple(
    v for v in (CONTRACT_VERSION, PREVIOUS_CONTRACT_VERSION) if v is not None
)


def is_supported(version: object) -> bool:
    """True when the server should accept a request sent with this `contract_version`.

    Exact string match only: "0.1" is supported, "0.1.0", " 0.1" and 0.1 (a float) are not.
    """
    return isinstance(version, str) and version in SUPPORTED_VERSIONS
