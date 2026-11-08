"""Tests for public network destination classification and address validation."""

import ipaddress

import pytest

from hovorunv2.utils.network_destinations import is_public_address, require_public_host


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ip_str", "expected_public"),
    [
        # Public IPv4 & IPv6
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("2606:4700:4700::1111", True),
        # Loopback
        ("127.0.0.1", False),
        ("127.0.0.2", False),
        ("::1", False),
        # Private RFC 1918 & ULA
        ("10.0.0.1", False),
        ("172.16.0.1", False),
        ("172.31.255.255", False),
        ("192.168.1.1", False),
        ("fc00::1", False),
        ("fd12:3456:789a::1", False),
        # Link-local & Cloud metadata
        ("169.254.169.254", False),
        ("169.254.1.1", False),
        ("fe80::1", False),
        # CGNAT
        ("100.64.0.1", False),
        ("100.127.255.255", False),
        # Multicast & Broadcast
        ("224.0.0.1", False),
        ("255.255.255.255", False),
        ("ff02::1", False),
        # Unspecified
        ("0.0.0.0", False),  # noqa: S104
        ("::", False),
        # IPv4-mapped IPv6
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("::ffff:169.254.169.254", False),
        ("::ffff:8.8.8.8", True),
        # NAT64 well-known prefix (64:ff9b::/96)
        ("64:ff9b::127.0.0.1", False),
        ("64:ff9b::10.0.0.1", False),
        ("64:ff9b::8.8.8.8", True),
    ],
)
def test_is_public_address(ip_str: str, expected_public: bool) -> None:
    """Verify classification of public and non-public IPv4 and IPv6 addresses."""
    ip = ipaddress.ip_address(ip_str)
    assert is_public_address(ip) is expected_public


@pytest.mark.unit
def test_require_public_host_rejects_private_and_resolves_public() -> None:
    """Validate that host resolution fails for private or loopback destinations."""
    with pytest.raises(ValueError, match=r"non-public address|not a public address"):
        require_public_host("127.0.0.1")

    with pytest.raises(ValueError, match=r"non-public address|not a public address"):
        require_public_host("169.254.169.254")

    with pytest.raises(ValueError, match=r"non-public address|not a public address"):
        require_public_host("localhost")

    # Allowed hosts bypass
    assert require_public_host("127.0.0.1", allowed_hosts=frozenset({"127.0.0.1"})) == (
        ipaddress.ip_address("127.0.0.1"),
    )
