"""Episode subtitle: what the list shows for played, partly played and unnumbered episodes."""

from types import SimpleNamespace

from mass.ui.podcast import episode_subtitle, latest_episode


def make_episode(**overrides):
    fields = {"fully_played": None, "resume_position_ms": None, "episode_number": None, "season": None}
    return SimpleNamespace(**{**fields, **overrides})


def test_fully_played_episode_says_played():
    assert episode_subtitle(make_episode(fully_played=True, resume_position_ms=5000)) == "Played"


def test_partly_played_episode_shows_resume_point():
    assert episode_subtitle(make_episode(resume_position_ms=125_000)) == "Resume at 2:05"


def test_numbered_episode_shows_season_and_number():
    assert episode_subtitle(make_episode(season=2, episode_number=7)) == "Season 2 · Episode 7"


def test_unnumbered_unplayed_episode_has_no_subtitle():
    assert episode_subtitle(make_episode()) == ""


def test_latest_is_highest_numbered_episode_even_when_listed_oldest_first():
    episodes = [make_episode(episode_number=1), make_episode(episode_number=3), make_episode(episode_number=2)]

    assert latest_episode(episodes).episode_number == 3


def test_latest_prefers_higher_season_over_higher_number():
    episodes = [make_episode(season=1, episode_number=12), make_episode(season=2, episode_number=1)]

    assert latest_episode(episodes).season == 2


def test_latest_without_numbers_is_the_first_listed():
    first, second = make_episode(), make_episode()

    assert latest_episode([first, second]) is first


def test_latest_of_no_episodes_is_none():
    assert latest_episode([]) is None
