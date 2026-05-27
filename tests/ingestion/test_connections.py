"""connections (social-graph edges) DTO parsing and graph writes."""

from __future__ import annotations

from datetime import UTC, datetime

from neo4j import Driver

# A row modeled on the real production sample: target is
# ``afdimbundestag`` (id ``6321049697``), constant per file; this row
# describes a connected user who only follows the target (Follower=Yes).
_SAMPLE_ROW = {
    "Account Linking": "",
    "Name": "Stephan Bothe",
    "Vanity Name": "stephan_bothe_mdl",
    "Groups": "",
    "Postings": "125",
    "Co Author of Postings": "0",
    "Quoted in Postings": "0",
    "Chat Messages": "0",
    "Media Items": "215",
    "Comments": "0",
    "Friends": "163",
    "All Connected Users": "720",
    "Tags": "",
    "Connections": "1",
    "Vanity Name selected conn. User": "afdimbundestag",
    "Network Object ID selected conn. User": "6321049697",
    "Posting Conn.": "0",
    "Comment Conn.": "0",
    "Reaction Conn.": "0",
    "React. Like": "0",
    "React. Love": "0",
    "React. Haha": "0",
    "React. Wow": "0",
    "React. Sad": "0",
    "React. Angry": "0",
    "ChatMessage Conn.": "0",
    "Media Conn.": "0",
    "Friend": "No",
    "Follower": "Yes",
    "Following": "No",
    "Network Object ID": "49828621614",
    "Crawled at": "2026-05-26 02:31:43+00",
    "Profile Type": "user",
    "Url": "https://www.instagram.com/stephan_bothe_mdl/",
    "Network": "Instagram",
    "Target-Profile?": "Yes",
    "Hometown": "",
    "Current City": "",
    "Date of Birth": "",
    "Place of Work/Education": "",
    "Bio": "",
    "Additional Details": "",
}


def test_from_row_maps_identity_fields() -> None:
    """Row user + target identity columns land on the DTO unchanged."""
    from chorus.ingestion.connections import from_row

    dto = from_row(_SAMPLE_ROW)
    assert dto is not None
    assert dto.row_user_id == "49828621614"
    assert dto.row_user_handle == "stephan_bothe_mdl"
    assert dto.row_user_display_name == "Stephan Bothe"
    assert dto.row_user_url == "https://www.instagram.com/stephan_bothe_mdl/"
    assert dto.row_user_platform == "Instagram"
    assert dto.row_user_profile_type == "user"
    assert dto.target_id == "6321049697"
    assert dto.target_handle == "afdimbundestag"
    assert dto.crawled_at == datetime(2026, 5, 26, 2, 31, 43, tzinfo=UTC)


def test_from_row_follower_only_flags() -> None:
    """``Follower=Yes`` alone produces a one-direction follows-only DTO."""
    from chorus.ingestion.connections import from_row

    dto = from_row(_SAMPLE_ROW)
    assert dto is not None
    assert dto.is_follower is True
    assert dto.is_following is False
    assert dto.is_friend is False


def test_from_row_mutual_follow() -> None:
    """``Follower=Yes`` + ``Following=Yes`` coexist on one row.

    Modeled on the Queckemeyer row in the real production sample.
    """
    from chorus.ingestion.connections import from_row

    row = dict(_SAMPLE_ROW, **{"Follower": "Yes", "Following": "Yes"})
    dto = from_row(row)
    assert dto is not None
    assert dto.is_follower is True
    assert dto.is_following is True
    assert dto.is_friend is False


def test_from_row_friend_yes() -> None:
    """``Friend=Yes`` is captured independently of the follows flags."""
    from chorus.ingestion.connections import from_row

    row = dict(_SAMPLE_ROW, **{"Friend": "Yes", "Follower": "No", "Following": "No"})
    dto = from_row(row)
    assert dto is not None
    assert dto.is_friend is True
    assert dto.is_follower is False
    assert dto.is_following is False


def test_from_row_self_loop_returns_none() -> None:
    """A row where row_user and target share an id is dropped."""
    from chorus.ingestion.connections import from_row

    row = dict(_SAMPLE_ROW, **{"Network Object ID": "6321049697"})
    assert from_row(row) is None


def test_from_row_no_flags_returns_none() -> None:
    """A row with Friend/Follower/Following all ``No`` carries no edge signal."""
    from chorus.ingestion.connections import from_row

    row = dict(_SAMPLE_ROW, **{"Friend": "No", "Follower": "No", "Following": "No"})
    assert from_row(row) is None


def test_from_row_strips_padded_timestamp() -> None:
    """``Crawled at`` with trailing whitespace (upstream fixed-width artifact) still parses."""
    from chorus.ingestion.connections import from_row

    padded = "2026-05-26 02:31:43+00" + " " * 80
    row = dict(_SAMPLE_ROW, **{"Crawled at": padded})
    dto = from_row(row)
    assert dto is not None
    assert dto.crawled_at == datetime(2026, 5, 26, 2, 31, 43, tzinfo=UTC)


def test_from_row_handles_missing_optional_fields() -> None:
    """Optional columns missing or empty become ``None`` on the DTO."""
    from chorus.ingestion.connections import from_row

    sparse = {
        "Network Object ID": "row-1",
        "Network Object ID selected conn. User": "target-1",
        "Friend": "No",
        "Follower": "Yes",
        "Following": "No",
    }
    dto = from_row(sparse)
    assert dto is not None
    assert dto.row_user_id == "row-1"
    assert dto.target_id == "target-1"
    assert dto.row_user_handle is None
    assert dto.row_user_display_name is None
    assert dto.row_user_url is None
    assert dto.target_handle is None
    assert dto.crawled_at is None


def test_write_batch_creates_thin_authors(migrated_driver: Driver) -> None:
    """Both endpoints of every edge are MERGEd as ``:Author`` nodes."""
    from chorus.ingestion.connections import from_row, write_batch

    dto = from_row(_SAMPLE_ROW)
    assert dto is not None
    write_batch(migrated_driver, [dto])

    with migrated_driver.session() as s:
        row_user = s.run("MATCH (a:Author {id: '49828621614'}) RETURN a").single()
        target = s.run("MATCH (a:Author {id: '6321049697'}) RETURN a").single()
        assert row_user is not None
        assert target is not None
        assert row_user["a"]["handle"] == "stephan_bothe_mdl"
        assert row_user["a"]["display_name"] == "Stephan Bothe"
        assert row_user["a"]["platform"] == "Instagram"
        assert target["a"]["handle"] == "afdimbundestag"


def test_write_batch_creates_follows_in_correct_direction(migrated_driver: Driver) -> None:
    """``Follower=Yes`` writes ``(row_user)-[:FOLLOWS]->(target)``."""
    from chorus.ingestion.connections import from_row, write_batch

    dto = from_row(_SAMPLE_ROW)
    assert dto is not None
    write_batch(migrated_driver, [dto])

    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (u:Author)-[r:FOLLOWS]->(t:Author) RETURN u.id AS src, t.id AS dst, r.crawled_at AS at"
        ).single()
        assert rec is not None
        assert rec["src"] == "49828621614"
        assert rec["dst"] == "6321049697"
        assert rec["at"].year == 2026


def test_write_batch_mutual_follow_writes_both_edges(migrated_driver: Driver) -> None:
    """A row with both flags writes two ``:FOLLOWS`` edges (one per direction)."""
    from chorus.ingestion.connections import from_row, write_batch

    row = dict(_SAMPLE_ROW, **{"Follower": "Yes", "Following": "Yes"})
    dto = from_row(row)
    assert dto is not None
    write_batch(migrated_driver, [dto])

    with migrated_driver.session() as s:
        count = s.run("MATCH ()-[r:FOLLOWS]->() RETURN count(r) AS c").single()["c"]  # type: ignore[index]
        assert count == 2
        # Confirm both directions present.
        fwd = s.run(
            "MATCH (u:Author {id: '49828621614'})-[:FOLLOWS]->(t:Author {id: '6321049697'}) RETURN count(*) AS c"
        ).single()["c"]  # type: ignore[index]
        bwd = s.run(
            "MATCH (u:Author {id: '6321049697'})-[:FOLLOWS]->(t:Author {id: '49828621614'}) RETURN count(*) AS c"
        ).single()["c"]  # type: ignore[index]
        assert fwd == 1
        assert bwd == 1


def test_write_batch_friendship_canonical_direction(migrated_driver: Driver) -> None:
    """``:FRIENDS_WITH`` is stored with ``a.id < b.id`` regardless of row orientation."""
    from chorus.ingestion.connections import from_row, write_batch

    # Pick ids where target ('6321049697') would sort BEFORE row user
    # ('99999999999') so the canonical direction is target → row_user,
    # not the row's natural orientation.
    row = dict(
        _SAMPLE_ROW,
        **{
            "Friend": "Yes",
            "Follower": "No",
            "Following": "No",
            "Network Object ID": "99999999999",
        },
    )
    dto = from_row(row)
    assert dto is not None
    write_batch(migrated_driver, [dto])

    with migrated_driver.session() as s:
        rec = s.run("MATCH (a:Author)-[r:FRIENDS_WITH]->(b:Author) RETURN a.id AS lo, b.id AS hi").single()
        assert rec is not None
        assert rec["lo"] == "6321049697"
        assert rec["hi"] == "99999999999"
        assert rec["lo"] < rec["hi"]


def test_write_batch_friendship_dedupes_from_either_side(migrated_driver: Driver) -> None:
    """The same pair friended from row A→B and row B→A produces one edge."""
    from chorus.ingestion.connections import from_row, write_batch

    forward = dict(
        _SAMPLE_ROW,
        **{
            "Friend": "Yes",
            "Follower": "No",
            "Following": "No",
            "Network Object ID": "aaaa",
            "Network Object ID selected conn. User": "bbbb",
        },
    )
    reverse = dict(
        _SAMPLE_ROW,
        **{
            "Friend": "Yes",
            "Follower": "No",
            "Following": "No",
            "Network Object ID": "bbbb",
            "Network Object ID selected conn. User": "aaaa",
        },
    )
    fwd_dto = from_row(forward)
    rev_dto = from_row(reverse)
    assert fwd_dto is not None and rev_dto is not None
    write_batch(migrated_driver, [fwd_dto, rev_dto])

    with migrated_driver.session() as s:
        count = s.run("MATCH ()-[r:FRIENDS_WITH]->() RETURN count(r) AS c").single()["c"]  # type: ignore[index]
        assert count == 1


def test_write_batch_does_not_overwrite_profile_data(migrated_driver: Driver) -> None:
    """Connections write must not clobber identity fields the profiles stage set.

    Per ADR 0006 the profiles table is the authoritative source for
    ``:Author`` identity. Connections rows carry denormalized profile
    data, so the connections write must use ``ON CREATE SET`` (not
    ``SET``) to leave already-enriched fields intact.
    """
    from chorus.ingestion.connections import from_row, write_batch
    from chorus.ingestion.profiles import from_row as profile_from_row
    from chorus.ingestion.profiles import write as profile_write

    # Seed the row user as the profiles stage would, with rich personal data.
    profile_write(
        migrated_driver,
        profile_from_row(
            {
                "ID": "49828621614",
                "UUID": "prof-bothe",
                "Name": "Stephan Bothe (canonical)",
                "Bio": "Landtagsabgeordneter",
                "Hometown": "Lüneburg",
            }
        ),
    )

    dto = from_row(_SAMPLE_ROW)
    assert dto is not None
    write_batch(migrated_driver, [dto])

    with migrated_driver.session() as s:
        a = s.run("MATCH (a:Author {id: '49828621614'}) RETURN a").single()["a"]  # type: ignore[index]
        assert a["display_name"] == "Stephan Bothe (canonical)"
        assert a["bio"] == "Landtagsabgeordneter"
        assert a["hometown"] == "Lüneburg"


def test_write_batch_is_idempotent(migrated_driver: Driver) -> None:
    """Re-running with the same batch produces no new nodes or edges."""
    from chorus.ingestion.connections import from_row, write_batch

    dto = from_row(_SAMPLE_ROW)
    assert dto is not None
    write_batch(migrated_driver, [dto])
    write_batch(migrated_driver, [dto])

    with migrated_driver.session() as s:
        a_count = s.run("MATCH (a:Author) RETURN count(a) AS c").single()["c"]  # type: ignore[index]
        e_count = s.run("MATCH ()-[r:FOLLOWS]->() RETURN count(r) AS c").single()["c"]  # type: ignore[index]
        assert a_count == 2
        assert e_count == 1


def test_write_batch_updates_crawled_at_on_recrawl(migrated_driver: Driver) -> None:
    """A later crawl of the same edge advances ``crawled_at`` (latest wins)."""
    from chorus.ingestion.connections import from_row, write_batch

    first = from_row(dict(_SAMPLE_ROW, **{"Crawled at": "2026-05-01T00:00:00+00:00"}))
    later = from_row(dict(_SAMPLE_ROW, **{"Crawled at": "2026-06-15T12:00:00+00:00"}))
    assert first is not None and later is not None
    write_batch(migrated_driver, [first])
    write_batch(migrated_driver, [later])

    with migrated_driver.session() as s:
        rec = s.run("MATCH ()-[r:FOLLOWS]->() RETURN r.crawled_at AS at").single()
        assert rec is not None
        assert rec["at"].month == 6
        assert rec["at"].day == 15


def test_write_batch_empty_input_is_a_noop(migrated_driver: Driver) -> None:
    """``write_batch([])`` writes nothing and returns zero counts."""
    from chorus.ingestion.connections import write_batch

    result = write_batch(migrated_driver, [])
    assert result == {"authors": 0, "follows": 0, "friends_with": 0}

    with migrated_driver.session() as s:
        assert s.run("MATCH (a:Author) RETURN count(a) AS c").single()["c"] == 0  # type: ignore[index]
