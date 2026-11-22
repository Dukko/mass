from types import SimpleNamespace

from music_assistant_models.enums import PlayerType

from mass.ui.group_members import apply_toggle, group_candidates, is_locked


def player(pid: str, name: str = "", **kw):
    base = dict(player_id=pid, name=name or pid, provider="airplay", type=PlayerType.PLAYER,
                available=True, active_group=None, group_members=[], static_group_members=[],
                can_group_with=set())
    base.update(kw)
    return SimpleNamespace(**base)


def ids(players) -> list[str]:
    return [p.player_id for p in players]


def test_provider_wide_can_group_with_is_honoured():
    leader = player("a", can_group_with={"airplay"})
    other = player("b")
    cast = player("c", provider="cast")

    assert ids(group_candidates(leader, [leader, other, cast])) == ["b"]


def test_group_type_player_is_excluded():
    leader = player("a", can_group_with={"b"})
    group = player("b", type=PlayerType.GROUP)

    assert group_candidates(leader, [leader, group]) == []


def test_player_in_another_active_group_is_excluded():
    leader = player("a", can_group_with={"b", "c"})
    taken = player("b", active_group="x")
    mine = player("c", active_group="a")

    assert ids(group_candidates(leader, [leader, taken, mine])) == ["c"]


def test_current_member_stays_when_unavailable():
    leader = player("a", group_members=["a", "b"])
    gone = player("b", available=False)

    assert ids(group_candidates(leader, [leader, gone])) == ["b"]


def test_candidates_sorted_by_name_casefold():
    leader = player("a", can_group_with={"airplay"})
    ps = [leader, player("b", "zed"), player("c", "Alpha")]

    assert ids(group_candidates(leader, ps)) == ["c", "b"]


def test_static_members_and_leader_are_locked():
    leader = player("a", static_group_members=["b"])

    assert is_locked(leader, "b")
    assert is_locked(leader, "a")
    assert not is_locked(leader, "c")


def test_toggling_back_to_current_membership_cancels_out():
    add, remove = set(), set()

    apply_toggle(add, remove, "b", want=True, is_member=False)
    apply_toggle(add, remove, "b", want=False, is_member=False)
    apply_toggle(add, remove, "c", want=False, is_member=True)
    apply_toggle(add, remove, "c", want=True, is_member=True)

    assert add == set()
    assert remove == set()


def test_toggle_records_add_and_remove():
    add, remove = set(), set()

    apply_toggle(add, remove, "b", want=True, is_member=False)
    apply_toggle(add, remove, "c", want=False, is_member=True)

    assert add == {"b"}
    assert remove == {"c"}


def test_player_without_group_attributes_has_no_candidates_and_is_unlocked():
    bare_leader = SimpleNamespace(player_id="a", name="a")
    other = SimpleNamespace(player_id="b", name="b", provider="airplay", type=PlayerType.PLAYER,
                            available=True)

    assert group_candidates(bare_leader, [bare_leader, other]) == []
    assert not is_locked(bare_leader, "b")
