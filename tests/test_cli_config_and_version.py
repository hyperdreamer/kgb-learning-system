"""Focused tests for launcher config selection and displayed version."""

import json
import os


def test_config_option_is_normalized_and_used_by_launcher(tmp_path, monkeypatch):
    import main

    selected = tmp_path / "nested" / "alternate.json"
    started_with = []
    monkeypatch.setattr(main, "run_application", started_with.append)

    assert main.main(["-c", str(selected)]) is None
    assert started_with == [str(selected.resolve())]
    assert main.parse_args(["--config", str(selected)]).config == str(
        selected.resolve()
    )


def test_selected_config_file_is_used_for_load_and_save(tmp_path):
    from kgb_srs.config import load_settings, save_settings

    selected = tmp_path / "alternate.json"
    selected.write_text(json.dumps({"font_size": 19}), encoding="utf-8")
    settings = load_settings(selected)
    assert settings["font_size"] == 19

    settings["font_size"] = 21
    save_settings(settings, selected)
    assert json.loads(selected.read_text(encoding="utf-8"))["font_size"] == 21


def test_version_marks_development_branches_with_a_single_hyphenated_suffix(
    monkeypatch,
):
    from kgb_srs import __version__
    from kgb_srs.version import get_app_version, is_development_branch

    assert is_development_branch("dev")
    assert is_development_branch("dev-feature")
    assert not is_development_branch("develop")
    assert not is_development_branch("feature/dev-tools")
    assert not is_development_branch(None)
    expected_dev_version = f"{__version__.removesuffix('-dev')}-dev"
    assert get_app_version(lambda: "dev-feature") == expected_dev_version
    assert get_app_version(lambda: None) == __version__

    import kgb_srs.version as version

    monkeypatch.setattr(version, "get_git_branch", lambda: "main")
    assert version.get_app_version() == __version__


def test_git_branch_reads_normal_and_linked_worktree_metadata(tmp_path):
    from kgb_srs.version import get_git_branch

    repository = tmp_path / "repository"
    nested_directory = repository / "src" / "module"
    git_directory = repository / ".git"
    nested_directory.mkdir(parents=True)
    git_directory.mkdir()
    (git_directory / "HEAD").write_text(
        "ref: refs/heads/dev-feature\n", encoding="utf-8"
    )

    assert get_git_branch(nested_directory) == "dev-feature"

    worktree = tmp_path / "linked-worktree"
    worktree.mkdir()
    worktree_git_directory = tmp_path / "git-metadata" / "worktrees" / "lesson"
    worktree_git_directory.mkdir(parents=True)
    (worktree_git_directory / "HEAD").write_text(
        "ref: refs/heads/dev-worktree\n", encoding="utf-8"
    )
    gitdir_reference = os.path.relpath(worktree_git_directory, worktree)
    (worktree / ".git").write_text(f"gitdir: {gitdir_reference}\n", encoding="utf-8")

    assert get_git_branch(worktree) == "dev-worktree"
