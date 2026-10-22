from importlib.metadata import version


def test_installed_distribution_matches_package_version():
    import soba_matchups

    assert version("soba_matchups") == soba_matchups.__version__
