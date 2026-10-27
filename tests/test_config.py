import pytest
from pydantic import ValidationError

from backend import config
from backend.config import CrawlerConfig, Settings


def test_dotenv_is_read_but_never_overrides_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nWEBCRAWLER_PORT=9100\nWEBCRAWLER_MAX_PAGES='77'\nWEBCRAWLER_HOST=0.0.0.0\n\nBAD LINE\n")
    monkeypatch.delenv("WEBCRAWLER_PORT", raising=False)
    monkeypatch.delenv("WEBCRAWLER_MAX_PAGES", raising=False)
    monkeypatch.setenv("WEBCRAWLER_HOST", "127.0.0.1")  # real env wins
    config._load_dotenv(env)
    import os
    assert os.environ["WEBCRAWLER_PORT"] == "9100"
    assert os.environ["WEBCRAWLER_MAX_PAGES"] == "77"
    assert os.environ["WEBCRAWLER_HOST"] == "127.0.0.1"
    for key in ("WEBCRAWLER_PORT", "WEBCRAWLER_MAX_PAGES"):
        monkeypatch.delenv(key)


def test_env_values_are_clamped_and_garbage_falls_back(monkeypatch):
    monkeypatch.setattr(config, "_load_dotenv", lambda path: None)
    monkeypatch.setenv("WEBCRAWLER_MAX_PAGES", "999999999")
    monkeypatch.setenv("WEBCRAWLER_PORT", "not-a-number")
    monkeypatch.setenv("WEBCRAWLER_ALLOW_PRIVATE_NETWORKS", "yes")
    monkeypatch.setenv("WEBCRAWLER_ALLOWED_HOSTS", "crawler.lan, other.lan")
    s = Settings.from_env()
    assert s.max_pages_cap == 10000 and s.port == 8000 and s.allow_private_networks is True
    assert {"crawler.lan", "other.lan", "localhost"} <= set(s.allowed_hosts)


def test_private_networks_are_off_by_default(monkeypatch):
    monkeypatch.setattr(config, "_load_dotenv", lambda path: None)
    monkeypatch.delenv("WEBCRAWLER_ALLOW_PRIVATE_NETWORKS", raising=False)
    assert Settings.from_env().allow_private_networks is False


def test_public_limits_expose_no_secrets_and_match_caps():
    limits = Settings().public_limits()
    assert limits["max_pages"]["max"] == Settings().max_pages_cap
    assert "db_path" not in limits and "password" not in str(limits).lower()


@pytest.mark.parametrize("field,value", [("max_pages", 0), ("concurrency", 0), ("max_depth", -1), ("crawl_delay", -0.1)])
def test_request_model_rejects_out_of_range(field, value):
    with pytest.raises(ValidationError):
        CrawlerConfig(start_url="https://example.com", **{field: value})


def test_limit_check_uses_server_caps():
    cfg = Settings(max_pages_cap=10, max_depth_cap=2, max_concurrency_cap=2, min_crawl_delay=0.5)
    with pytest.raises(ValueError) as err:
        CrawlerConfig(start_url="https://example.com", max_pages=11, max_depth=3, concurrency=3, crawl_delay=0.1).check_limits(cfg)
    for fragment in ("max_pages", "max_depth", "concurrency", "crawl_delay"):
        assert fragment in str(err.value)
    CrawlerConfig(start_url="https://example.com", max_pages=10, max_depth=2, concurrency=2, crawl_delay=0.5).check_limits(cfg)


def test_user_agent_override_must_be_printable_ascii():
    assert CrawlerConfig(start_url="https://example.com", user_agent="Mine/1.0").user_agent == "Mine/1.0"
    assert CrawlerConfig(start_url="https://example.com", user_agent="").user_agent is None
    for bad in ("x\r\nInjected: 1", "caf\u00e9", "a" * 300):
        with pytest.raises(ValidationError):
            CrawlerConfig(start_url="https://example.com", user_agent=bad)
