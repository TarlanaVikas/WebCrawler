import socket

import pytest

from backend.security import (
    BlockedURLError, SafeResolver, ip_is_public, is_blocked_error, resolve_public, validate_url,
)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254",
    "0.0.0.0", "100.64.0.1", "224.0.0.1", "240.0.0.1", "255.255.255.255", "192.0.2.5", "198.18.0.1",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
    "64:ff9b::7f00:1", "2002:7f00:0001::1", "2002:a00:1::1",
])
def test_non_public_addresses_are_rejected(ip):
    assert not ip_is_public(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_accepted(ip):
    assert ip_is_public(ip)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://LOCALHOST:8000/", "http://foo.localhost/",
    "http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/computeMetadata/v1/",
    "http://10.1.2.3/", "http://192.168.0.1:8080/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[fe80::1]/", "http://0.0.0.0/", "http://printer.local/", "http://intranet/",
    "http://2130706433/", "http://0x7f.1/", "http://0x7f000001/", "http://017700000001/", "http://127.1/",
    "http://user:pass@example.com/", "ftp://example.com/", "file:///etc/passwd", "javascript:alert(1)",
    "gopher://example.com/", "http:///nohost", "http://example.com:99999/", "", "   ", "not a url",
    "http://example.com/" + "a" * 3000,
])
def test_blocked_urls(url):
    with pytest.raises(BlockedURLError):
        validate_url(url, allow_private=False)


@pytest.mark.parametrize("url,expected", [
    ("https://Example.COM", "https://example.com/"),
    ("http://example.com:80/path?q=1#frag", "http://example.com/path?q=1"),
    ("https://example.com:443/", "https://example.com/"),
    ("http://example.com:8080", "http://example.com:8080/"),
    ("  https://example.com/a b  ".strip(), "https://example.com/a b"),
    ("http://8.8.8.8/", "http://8.8.8.8/"),
    ("http://[2606:4700:4700::1111]/", "http://[2606:4700:4700::1111]/"),
])
def test_valid_urls_are_normalized(url, expected):
    assert validate_url(url, allow_private=False).url == expected


def test_idn_hosts_are_converted_to_ascii():
    assert validate_url("https://bücher.example/", allow_private=False).host == "xn--bcher-kva.example"


def test_private_allowed_only_when_enabled():
    assert validate_url("http://127.0.0.1:9/", allow_private=True).host == "127.0.0.1"


def _fake_getaddrinfo(mapping):
    async def getaddrinfo(host, port, family=0, type=0):
        ips = mapping[host]
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in ips
        ]
    return getaddrinfo


async def test_resolver_returns_public_addresses():
    resolver = SafeResolver(getaddrinfo=_fake_getaddrinfo({"ok.test": ["93.184.216.34"]}))
    records = await resolver.resolve("ok.test", 443)
    assert records[0]["host"] == "93.184.216.34" and records[0]["port"] == 443


@pytest.mark.parametrize("answer", [["127.0.0.1"], ["10.0.0.5"], ["169.254.169.254"], ["::1"], ["93.184.216.34", "10.0.0.5"]])
async def test_resolver_refuses_hosts_that_resolve_privately(answer):
    resolver = SafeResolver(getaddrinfo=_fake_getaddrinfo({"evil.test": answer}))
    with pytest.raises(BlockedURLError):
        await resolver.resolve("evil.test", 80)


async def test_dns_rebinding_second_answer_is_checked():
    """Each resolution is validated, so a host that starts answering privately is refused."""
    answers = [["93.184.216.34"], ["127.0.0.1"]]

    async def getaddrinfo(host, port, family=0, type=0):
        ip = answers.pop(0)[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    resolver = SafeResolver(getaddrinfo=getaddrinfo)
    assert (await resolver.resolve("rebind.test", 80))[0]["host"] == "93.184.216.34"
    with pytest.raises(BlockedURLError):
        await resolver.resolve("rebind.test", 80)


async def test_resolve_public_helper_reports_reason():
    with pytest.raises(BlockedURLError, match="private or reserved"):
        await resolve_public("evil.test", 80, getaddrinfo=_fake_getaddrinfo({"evil.test": ["192.168.1.10"]}))


def test_is_blocked_error_walks_the_cause_chain():
    try:
        try:
            raise BlockedURLError("x")
        except BlockedURLError as inner:
            raise OSError("wrapped") from inner
    except OSError as outer:
        assert is_blocked_error(outer)
    assert not is_blocked_error(ValueError("nope"))
