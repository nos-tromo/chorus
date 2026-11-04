"""social_network_around: the author ego network over the :FOLLOWS/:FRIENDS_WITH social graph."""

from __future__ import annotations

import io
from typing import Any

import pytest
from neo4j import Driver
from pydantic import ValidationError

from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_event import Actor
from tests._audit import operation_events


def _edge_tuples(out: Any) -> set[tuple[str, str, str, bool]]:
    """Return each edge as a ``(source, target, kind, directed)`` tuple."""
    return {(e.source, e.target, e.kind, e.directed) for e in out.edges}


def _node(out: Any, node_id: str) -> Any:
    """Return the single node with ``node_id`` (raises if absent)."""
    return next(n for n in out.nodes if n.id == node_id)


def _author_ids(out: Any) -> set[str]:
    """Return the set of node ids in the returned network."""
    return {n.id for n in out.nodes}


def test_depth_above_two_rejected() -> None:
    """``depth`` > 2 is not yet supported and fails input validation (-> 422)."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn

    with pytest.raises(ValidationError):
        SocialNetworkAroundIn(author="anyone", depth=3)


def test_empty_seed_returns_empty_network(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """A seed matching no author yields an empty network with no seed node."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="nobody", depth=2),
        audit=audit_ctx,
    )
    assert out.nodes == []
    assert out.edges == []
    assert out.seed_node_id is None
    assert out.truncated is False
    assert out.audit_result_count() == 0


def test_depth_one_star_covers_all_three_legs(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """depth=1 returns the seed plus its follows-out, follows-in, and friends ties.

    Each edge carries the right ``kind``/``directed``; the friends edge is emitted
    in canonical (lower-id-first) order regardless of how it is stored.
    """
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'seed'}) ON CREATE SET seed.handle = 'seedh'
            MERGE (fout:Author {id: 'fout'}) ON CREATE SET fout.handle = 'fouth'
            MERGE (fin:Author  {id: 'fin'})  ON CREATE SET fin.handle  = 'finh'
            MERGE (afriend:Author {id: 'afriend'}) ON CREATE SET afriend.handle = 'afriendh'
            MERGE (seed)-[:FOLLOWS]->(fout)          // seed follows fout
            MERGE (fin)-[:FOLLOWS]->(seed)           // fin follows seed
            MERGE (afriend)-[:FRIENDS_WITH]->(seed)  // stored canonical (afriend < seed)
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="seedh", depth=1),
        audit=audit_ctx,
    )

    assert out.seed_node_id == "author:seed"
    assert _author_ids(out) == {"author:seed", "author:fout", "author:fin", "author:afriend"}
    assert _node(out, "author:seed").is_seed is True
    assert _node(out, "author:seed").ring == 0
    assert all(_node(out, nid).ring == 1 for nid in ("author:fout", "author:fin", "author:afriend"))
    assert _edge_tuples(out) == {
        ("author:seed", "author:fout", "follows", True),  # seed -> fout (follows-out)
        ("author:fin", "author:seed", "follows", True),  # fin -> seed (follows-in)
        ("author:afriend", "author:seed", "friends", False),  # canonical, undirected
    }


def test_depth_two_expansion_keeps_radial_star(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """depth=2 adds ties-of-ties as ring-2 nodes, keeping the seed star (radial superset)."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (sseed:Author {id: 's_seed'}) ON CREATE SET sseed.handle = 'sseedh'
            MERGE (mone:Author  {id: 'm_one'})  ON CREATE SET mone.handle  = 'moneh'
            MERGE (ztwo:Author  {id: 'z_two'})  ON CREATE SET ztwo.handle  = 'ztwoh'
            MERGE (mone)-[:FRIENDS_WITH]->(sseed)   // ring 1 tie
            MERGE (mone)-[:FRIENDS_WITH]->(ztwo)    // ring 2 tie
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="sseedh", depth=2),
        audit=audit_ctx,
    )

    assert _node(out, "author:s_seed").ring == 0
    assert _node(out, "author:m_one").ring == 1
    assert _node(out, "author:z_two").ring == 2
    # seed is never demoted into ring 2
    assert _author_ids(out) == {"author:s_seed", "author:m_one", "author:z_two"}
    edges = _edge_tuples(out)
    assert ("author:m_one", "author:s_seed", "friends", False) in edges  # the star edge survives
    assert ("author:m_one", "author:z_two", "friends", False) in edges  # ring1 -> ring2


def test_depth_one_omits_second_ring(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """At depth=1 a friend-of-friend is not pulled in."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a:Author {id: 'a'}) ON CREATE SET a.handle = 'ah'
            MERGE (b:Author {id: 'b'}) ON CREATE SET b.handle = 'bh'
            MERGE (c:Author {id: 'c'}) ON CREATE SET c.handle = 'ch'
            MERGE (a)-[:FRIENDS_WITH]->(b)
            MERGE (b)-[:FRIENDS_WITH]->(c)
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="ah", depth=1),
        audit=audit_ctx,
    )
    assert _author_ids(out) == {"author:a", "author:b"}  # c (friend-of-friend) absent


def test_limit_caps_ring_one(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """``limit`` caps the direct-tie ring deterministically and flips truncated."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'lseed'}) ON CREATE SET seed.handle = 'lseedh'
            WITH seed
            UNWIND ['f1','f2','f3','f4','f5'] AS fid
            MERGE (f:Author {id: fid})
            MERGE (f)-[:FRIENDS_WITH]->(seed)
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="lseedh", depth=1, limit=2),
        audit=audit_ctx,
    )
    ring1 = {n.id for n in out.nodes if n.ring == 1}
    assert len(ring1) == 2
    assert out.truncated is True

    full = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="lseedh", depth=1, limit=50),
        audit=audit_ctx,
    )
    assert len({n.id for n in full.nodes if n.ring == 1}) == 5
    assert full.truncated is False


def test_second_ring_limit_caps_ring_two(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """``second_ring_limit`` caps the ring-2 set and flips truncated; ring 1 is unaffected."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'ss'}) ON CREATE SET seed.handle = 'ssh'
            MERGE (m:Author {id: 'mm'}) ON CREATE SET m.handle = 'mmh'
            MERGE (m)-[:FRIENDS_WITH]->(seed)
            WITH m
            UNWIND ['b1','b2','b3'] AS bid
            MERGE (b:Author {id: bid})
            MERGE (m)-[:FRIENDS_WITH]->(b)
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="ssh", depth=2, second_ring_limit=1),
        audit=audit_ctx,
    )
    ring2 = {n.id for n in out.nodes if n.ring == 2}
    assert len(ring2) == 1
    assert out.truncated is True


def test_ambiguous_name_picks_lowest_id(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """Two authors sharing a display name resolve to one ego — the lowest id."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a1:Author {id: 'a1'}) ON CREATE SET a1.display_name = 'Alex'
            MERGE (a2:Author {id: 'a2'}) ON CREATE SET a2.display_name = 'Alex'
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="alex", depth=1),
        audit=audit_ctx,
    )
    assert out.seed_node_id == "author:a1"


def test_handle_match_preferred_over_display_name(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """A handle match wins over a display-name match even when the latter has a lower id."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (byhandle:Author {id: 'x9'}) ON CREATE SET byhandle.handle = 'sam'
            MERGE (bydisplay:Author {id: 'x1'}) ON CREATE SET bydisplay.display_name = 'sam'
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="sam", depth=1),
        audit=audit_ctx,
    )
    assert out.seed_node_id == "author:x9"  # handle match, despite x1 < x9


def test_thin_author_neighbor_appears(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """A neighbour that never posted (no :AUTHORED) still appears — the tool joins only the social graph."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'ts'}) ON CREATE SET seed.handle = 'tsh'
            MERGE (thin:Author {id: 'tt'}) ON CREATE SET thin.handle = 'tth'
            MERGE (seed)-[:FOLLOWS]->(thin)
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="tsh", depth=1),
        audit=audit_ctx,
    )
    assert "author:tt" in _author_ids(out)


def test_node_label_falls_back(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """Label prefers handle, then display_name, then the raw id."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'nlseed'}) ON CREATE SET seed.handle = 'nlseedh'
            MERGE (named:Author {id: 'named'}) ON CREATE SET named.display_name = 'Named Person'
            MERGE (bare:Author {id: 'bare_id'})
            MERGE (seed)-[:FOLLOWS]->(named)
            MERGE (seed)-[:FOLLOWS]->(bare)
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="nlseedh", depth=1),
        audit=audit_ctx,
    )
    assert _node(out, "author:named").label == "Named Person"
    assert _node(out, "author:bare_id").label == "bare_id"


def test_registered_in_tools(migrated_driver: Driver) -> None:
    """The tool self-registers into the global TOOLS registry."""
    from chorus.tools import TOOLS

    assert "social_network_around" in TOOLS


def test_audit_authors_are_the_network(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """audit_authors() records the author ids in the network (the persons touched)."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'auds'}) ON CREATE SET seed.handle = 'audsh'
            MERGE (friend:Author {id: 'audf'}) ON CREATE SET friend.handle = 'audfh'
            MERGE (friend)-[:FRIENDS_WITH]->(seed)
            """
        )
    out = social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="audsh", depth=1),
        audit=audit_ctx,
    )
    assert set(out.audit_authors()) == {"auds", "audf"}


def test_audit_event_written(migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """One query event per call: node count as the target count, every person by id, seed first."""
    from chorus.tools.social_network_around import SocialNetworkAroundIn, social_network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'rs'}) ON CREATE SET seed.handle = 'rsh'
            MERGE (friend:Author {id: 'rf'}) ON CREATE SET friend.handle = 'rfh'
            MERGE (friend)-[:FRIENDS_WITH]->(seed)
            """
        )
    social_network_around(
        migrated_driver,
        SocialNetworkAroundIn(author="rsh", depth=1),
        audit=audit_trail.context(Actor(user="alice", via="edge")),
    )
    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", "tool.social_network_around", "ok")
    assert event["actor"] == {"user": "alice", "via": "edge"}
    # seed + one friend = 2 nodes
    assert event["target"] == {"kind": "mixed", "ids": ["author:rs", "author:rf"], "count": 2}
