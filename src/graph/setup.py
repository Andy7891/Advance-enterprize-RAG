"""Neo4j setup check: can this code reach the graph database, log in, and use what
later GraphRAG steps rely on?

Readiness here means more than "the port answers": the Bolt protocol must accept
the configured credentials, the server must be the pinned major version, and the
APOC procedures must be loaded.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import AuthError, ClientError, DriverError, Neo4jError, ServiceUnavailable

from src.core.config import settings

# docker-compose.yml pins neo4j:5.26.0; a different major version means the image
# was changed without revisiting the queries and procedures written against 5.x.
EXPECTED_MAJOR_VERSION = "5"


@dataclass(frozen=True)
class GraphStatus:
    ready: bool
    version: str | None = None
    edition: str | None = None
    apoc_version: str | None = None
    problem: str | None = None


class GraphDriver(Protocol):
    """The part of `neo4j.Driver` the health check uses, so tests can pass a fake."""

    def verify_connectivity(self) -> None: ...

    # Returns Any: the real Session and a test fake only need to share `run` and
    # the context-manager methods, which is all the health check touches.
    def session(self, *, database: str | None = ...) -> Any: ...


def build_driver(
    uri: str | None = None, user: str | None = None, password: str | None = None
) -> Driver:
    """A driver for the configured Neo4j, reading settings at call time.

    Defaults are resolved inside the function rather than as default arguments,
    which would freeze the values at import and ignore later configuration.
    """
    return GraphDatabase.driver(
        uri or settings.neo4j_uri,
        auth=(user or settings.neo4j_user, password or settings.neo4j_password),
        # A health check that hangs for the driver's default 30 s tells the caller
        # nothing more than one that fails after a few seconds.
        connection_timeout=5,
    )


def health_check(driver: GraphDriver, database: str = "neo4j") -> GraphStatus:
    """Report whether the graph is usable. Never raises: the reason is in `problem`."""
    try:
        # INTERVIEW: verify_connectivity performs the Bolt handshake AND
        # authentication, so a reachable server with a wrong password fails here
        # rather than on the first real query.
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            component = session.run(
                "CALL dbms.components() YIELD versions, edition "
                "RETURN versions[0] AS version, edition"
            ).single()
            apoc_version = _apoc_version(session)
    except AuthError as exc:
        # The most likely cause is specific to Docker, so the message names it.
        return GraphStatus(
            ready=False,
            problem=(
                f"authentication failed ({exc.code}); check NEO4J_USER/NEO4J_PASSWORD in .env. "
                "NEO4J_AUTH only applies when the neo4j_data volume is first created, so a "
                "password changed in .env later is ignored by an existing volume"
            ),
        )
    except ServiceUnavailable as exc:
        return GraphStatus(
            ready=False, problem=f"cannot reach Neo4j ({exc}); is the container running?"
        )
    except (Neo4jError, DriverError) as exc:
        return GraphStatus(ready=False, problem=f"{type(exc).__name__}: {exc}")

    version, edition = component["version"], component["edition"]
    problems = []
    if not version.startswith(f"{EXPECTED_MAJOR_VERSION}."):
        problems.append(f"expected Neo4j {EXPECTED_MAJOR_VERSION}.x, server is {version}")
    if apoc_version is None:
        problems.append("APOC is not loaded; set NEO4J_PLUGINS='[\"apoc\"]' on the neo4j service")
    return GraphStatus(
        ready=not problems,
        version=version,
        edition=edition,
        apoc_version=apoc_version,
        problem="; ".join(problems) or None,
    )


def _apoc_version(session: Any) -> str | None:
    try:
        return session.run("RETURN apoc.version() AS version").single()["version"]
    except ClientError:
        # An unknown function is a syntax-level ClientError: APOC is simply absent.
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m src.graph.setup -- exit 0 if the graph is ready, 1 if not."""
    parser = argparse.ArgumentParser(description="Check the Neo4j graph database is ready.")
    parser.add_argument("--uri", default=None, help="defaults to NEO4J_URI from .env")
    parser.add_argument("--user", default=None, help="defaults to NEO4J_USER from .env")
    # No --password flag on purpose: a password typed on the command line ends up
    # in shell history; it is read from .env / the environment only.
    args = parser.parse_args(argv)

    uri = args.uri or settings.neo4j_uri
    with build_driver(uri, args.user) as driver:
        status = health_check(driver)

    if status.ready:
        print(
            f"ready: Neo4j {status.version} {status.edition} at {uri}, "
            f"APOC {status.apoc_version}, browser http://localhost:7474"
        )
        return 0
    print(f"NOT ready: {status.problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
