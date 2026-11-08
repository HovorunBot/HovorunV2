"""Public network destination classification and host address verification."""

import ipaddress
import socket
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

CGNAT_NETWORK = IPv4Network("100.64.0.0/10")
IETF_PROTOCOL_ASSIGNMENTS = IPv4Network("192.0.0.0/24")
TEST_NET_1 = IPv4Network("192.0.2.0/24")
TEST_NET_2 = IPv4Network("198.51.100.0/24")
TEST_NET_3 = IPv4Network("203.0.113.0/24")
NAT64_PREFIX = IPv6Network("64:ff9b::/96")


def _is_public_ipv4(address: IPv4Address) -> bool:
    if address.is_loopback or address.is_private or address.is_link_local:
        return False
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return False
    if address in CGNAT_NETWORK or address in IETF_PROTOCOL_ASSIGNMENTS:
        return False
    return not (address in TEST_NET_1 or address in TEST_NET_2 or address in TEST_NET_3)


def _is_public_ipv6(address: IPv6Address) -> bool:
    if address.ipv4_mapped is not None:
        return _is_public_ipv4(address.ipv4_mapped)
    if address.sixtofour is not None:
        return _is_public_ipv4(address.sixtofour)
    if address in NAT64_PREFIX:
        return _is_public_ipv4(IPv4Address(address.packed[-4:]))
    if address.is_site_local or address.is_loopback or address.is_private or address.is_link_local:
        return False
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return False
    return address.is_global


def is_public_address(address: IPv4Address | IPv6Address) -> bool:
    """Return whether the IP address is a safe, routable public destination.

    Rejects loopback, private (RFC 1918 / ULA), link-local, multicast,
    reserved, unspecified, CGNAT (100.64.0.0/10), documentation networks,
    and IPv4-mapped / 6to4 / NAT64 forms of these.

    Args:
        address: IPv4 or IPv6 address object to classify.

    Returns:
        True if the address is a public routable destination, False otherwise.
    """
    if isinstance(address, IPv4Address):
        return _is_public_ipv4(address)
    if isinstance(address, IPv6Address):
        return _is_public_ipv6(address)
    return False


def require_public_host(
    host: str,
    port: int = 80,
    *,
    allowed_hosts: frozenset[str] = frozenset(),
) -> tuple[IPv4Address | IPv6Address, ...]:
    """Require a host that resolves strictly to public network addresses.

    Args:
        host: Hostname or IP literal string to validate.
        port: Destination TCP port number.
        allowed_hosts: Optional test-only explicit allowlist of hostnames or IPs.

    Returns:
        Tuple of validated resolved IP addresses.

    Raises:
        ValueError: If host resolution fails or any resolved address is non-public.
    """
    cleaned_host = host.strip("[]")
    if cleaned_host in allowed_hosts or host in allowed_hosts:
        try:
            return (ipaddress.ip_address(cleaned_host),)
        except ValueError:
            return (ipaddress.ip_address("127.0.0.1"),)

    try:
        literal_ip = ipaddress.ip_address(cleaned_host)
    except ValueError:
        pass
    else:
        if not is_public_address(literal_ip):
            msg = f"Destination address {literal_ip} is not a public address."
            raise ValueError(msg)
        return (literal_ip,)

    try:
        addr_info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as error:
        msg = f"Host resolution failed for {host}: {error}"
        raise ValueError(msg) from None

    if not addr_info:
        msg = f"Host resolution yielded no addresses for {host}."
        raise ValueError(msg)

    resolved: list[IPv4Address | IPv6Address] = []
    for entry in addr_info:
        sockaddr = entry[4]
        ip = ipaddress.ip_address(sockaddr[0])
        if not is_public_address(ip):
            msg = f"Host {host} resolved to non-public address {ip}."
            raise ValueError(msg)
        resolved.append(ip)

    return tuple(resolved)
