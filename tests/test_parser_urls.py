from backend.parser import parse_html
from backend.urls import host_of, in_scope, looks_binary, normalize_url


def test_normalize_url():
    assert normalize_url("HTTP://Example.com:80") == "http://example.com/"
    assert normalize_url("https://example.com/a?x=1#top") == "https://example.com/a?x=1"
    assert normalize_url("mailto:a@b.c") is None
    assert normalize_url("http://[bad") is None
    assert normalize_url("http://") is None


def test_scope_treats_www_as_same_site_but_not_subdomains():
    assert in_scope("https://www.example.com/x", "https://example.com/")
    assert in_scope("http://example.com:8080/x", "https://example.com/")
    assert not in_scope("https://blog.example.com/", "https://example.com/")
    assert not in_scope("https://example.org/", "https://example.com/")
    assert host_of("not a url") == ""


def test_looks_binary():
    assert looks_binary("https://x.test/report.PDF")
    assert looks_binary("https://x.test/a/b/image.jpg?v=2")
    assert not looks_binary("https://x.test/about")
    assert not looks_binary("https://x.test/page.html")
    assert not looks_binary("https://x.test/v1.2/docs")


HTML = """
<html lang=en><head>
<title>
   Hello   World </title>
<meta name="description" content="A  test page">
<meta property="og:title" content="OG title">
<base href="https://cdn.example.com/base/">
<style>.x{content:"ignore me"}</style>
</head><body>
<h1>Main</h1><h2>One</h2><h2>Two</h2><h2></h2>
<script>var words = "do not count these";</script>
<p>Four words right here</p>
<a href="rel">relative</a>
<a href="/root">root</a>
<a href="https://other.test/Page#frag">abs</a>
<a href="https://other.test/Page">dup</a>
<a href="#top">anchor</a><a href="javascript:void(0)">js</a><a href="mailto:x@y.z">m</a><a href="tel:123">t</a>
<a href="http://[broken">bad</a><a href="">empty</a>
<noscript>hidden words</noscript>
</body></html>
"""


def test_parse_extracts_metadata_headings_and_links():
    out = parse_html("https://example.com/page", HTML)
    assert out["title"] == "Hello World"
    assert out["description"] == "A test page"
    assert out["meta_tags"]["og:title"] == "OG title"
    assert out["headings"] == {"h1": ["Main"], "h2": ["One", "Two"]}
    assert out["links"] == [
        "https://cdn.example.com/base/rel",
        "https://cdn.example.com/root",
        "https://other.test/Page",
    ]


def test_word_count_ignores_scripts_styles_and_noscript():
    out = parse_html("https://example.com/", HTML)
    # title(2) + h1(1) + h2s(2) + paragraph(4) + ten link labels(10); script/style/noscript excluded
    assert out["word_count"] == 19
    only = parse_html("https://e.test/", "<p>one two</p><script>three four five</script><style>six</style>")
    assert only["word_count"] == 2


def test_parse_accepts_bytes_with_declared_charset():
    raw = "<title>Café</title><a href='/x'>x</a>".encode("latin-1")
    out = parse_html("https://e.test/", raw, charset="latin-1")
    assert out["title"] == "Café"
    assert out["size_bytes"] == len(raw)


def test_link_cap():
    html = "".join(f'<a href="/p{i}">x</a>' for i in range(50))
    assert len(parse_html("https://e.test/", html, max_links=10)["links"]) == 10


def test_malformed_and_empty_documents_do_not_raise():
    for doc in ("", "<<<>>>", "<a href=", "\x00\x01", b"\xff\xfe\x00"):
        out = parse_html("https://e.test/", doc)
        assert out["links"] == [] and out["title"] == ""
