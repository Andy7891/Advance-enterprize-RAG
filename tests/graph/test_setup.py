"""Tests for the Neo4j setup check: a fake driver for every outcome, plus read-only
live tests against the real server (skipped, with the reason, when it is not running)."""

from __future__ import annotations

import json
import urllib.request

import pytest
from neo4j.exceptions import AuthError, ClientError, ServiceUnavailable, TransientError

import src.graph.setup as graph_setup
from src.core.config import settings
from src.graph.setup import build_driver, health_check
from tests.support import NEO4J_HTTP_URL, neo4j_down


class FakeResult:
    def __init__(self, record):
        self.record = record

    def single(self):
        return self.record


class FakeSession:
    def __init__(self, version="5.26.0", apoc="5.26.0"):
        self.version, self.apoc = version, apoc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def run(self, query, **parameters):
        if "apoc.version" in query:
            if self.apoc is None:
                raise ClientError("Unknown function 'apoc.version'")
            return FakeResult({"version": self.apoc})
        return FakeResult({"version": self.version, "edition": "community"})


class FakeDriver:
    def __init__(self, fail_with=None, **session_kwargs):
        self.fail_with = fail_with
        self.session_kwargs = session_kwargs
        self.databases: list[str | None] = []

    def verify_connectivity(self):
        if self.fail_with is not None:
            raise self.fail_with

    def session(self, *, database=None):
        self.databases.append(database)
        return FakeSession(**self.session_kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


# --- health_check -----------------------------------------------------------------


def test_ready_when_version_matches_and_apoc_is_loaded():
    driver = FakeDriver()
    status = health_check(driver)
    assert status.ready
    assert (status.version, status.edition, status.apoc_version) == (
        "5.26.0",
        "community",
        "5.26.0",
    )
    assert status.problem is None
    assert driver.databases == ["neo4j"]


def test_not_ready_without_apoc_and_says_how_to_enable_it():
    status = health_check(FakeDriver(apoc=None))
    assert not status.ready
    assert status.apoc_version is None
    assert "NEO4J_PLUGINS" in status.problem


def test_not_ready_on_an_unexpected_major_version():
    status = health_check(FakeDriver(version="2025.08.0"))
    assert not status.ready
    assert "expected Neo4j 5.x, server is 2025.08.0" in status.problem


def test_auth_failure_explains_the_volume_initialisation_trap():
    status = health_check(FakeDriver(fail_with=AuthError("The client is unauthorized")))
    assert not status.ready
    # INTERVIEW: the message names the Docker-specific cause (NEO4J_AUTH applies only
    # to a fresh volume), because "wrong password" alone sends people to the wrong fix.
    assert "only applies when the neo4j_data volume is first created" in status.problem


def test_unreachable_server_is_reported_not_raised():
    status = health_check(FakeDriver(fail_with=ServiceUnavailable("Connection refused")))
    assert not status.ready and "is the container running?" in status.problem


def test_other_neo4j_errors_are_reported_not_raised():
    status = health_check(FakeDriver(fail_with=TransientError("database unavailable")))
    assert not status.ready and status.problem.startswith("TransientError")


# --- build_driver and the CLI ---------------------------------------------------------


def test_build_driver_reads_settings_at_call_time(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        graph_setup.GraphDatabase,
        "driver",
        lambda uri, auth, **config: captured.update(uri=uri, auth=auth, **config) or "driver",
    )
    monkeypatch.setattr(settings, "neo4j_uri", "bolt://changed:7687")
    monkeypatch.setattr(settings, "neo4j_password", "from-settings")

    build_driver()

    # A default argument would have frozen the import-time values instead.
    assert captured["uri"] == "bolt://changed:7687"
    assert captured["auth"] == (settings.neo4j_user, "from-settings")
    assert captured["connection_timeout"] == 5


@pytest.mark.parametrize(
    ("driver", "exit_code", "prefix"),
    [(FakeDriver(), 0, "ready: Neo4j 5.26.0"), (FakeDriver(apoc=None), 1, "NOT ready:")],
)
def test_cli_exit_codes(monkeypatch, capsys, driver, exit_code, prefix):
    monkeypatch.setattr(graph_setup, "build_driver", lambda uri=None, user=None: driver)
    assert graph_setup.main([]) == exit_code
    assert capsys.readouterr().out.startswith(prefix)


def test_cli_has_no_password_flag():
    # A password on the command line lands in shell history.
    with pytest.raises(SystemExit) as exit_:
        graph_setup.main(["--password", "secret"])
    assert exit_.value.code == 2


# --- live: the real Neo4j container (read-only) ------------------------------------------

_NEO4J_DOWN = neo4j_down()
live = pytest.mark.skipif(_NEO4J_DOWN is not None, reason=_NEO4J_DOWN or "")


@pytest.mark.live
@live
def test_live_graph_is_ready_with_the_configured_credentials():
    with build_driver() as driver:
        status = health_check(driver)
    assert status.ready, status.problem
    assert status.version.startswith("5.") and status.apoc_version


@pytest.mark.live
@live
def test_live_old_default_password_is_rejected():
    # Proves the volume was initialised from .env, not left on the P1 default.
    with build_driver(password="changeme") as driver:
        status = health_check(driver)
    assert not status.ready and "authentication failed" in status.problem


@pytest.mark.live
@live
def test_live_http_endpoint_reports_the_pinned_version():
    with urllib.request.urlopen(NEO4J_HTTP_URL, timeout=5) as response:
        discovery = json.load(response)
    assert discovery["neo4j_version"] == "5.26.0"
    assert discovery["bolt_direct"] == "bolt://localhost:7687"


@pytest.mark.live
@live
def test_live_memory_settings_are_applied():
    with build_driver() as driver, driver.session() as session:
        values = {
            r["name"]: r["value"]
            for r in session.run(
                "SHOW SETTINGS YIELD name, value WHERE name STARTS WITH 'server.memory' "
                "RETURN name, value"
            )
        }
    # Neo4j reports sizes in its own display format (checked on the live server).
    assert values["server.memory.heap.initial_size"] == "512.00MiB"
    assert values["server.memory.heap.max_size"] == "1.00GiB"
    assert values["server.memory.pagecache.size"] == "512.00MiB"
