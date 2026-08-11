import importlib

import config as config_module


def test_project_root_resolves_same_regardless_of_cwd(tmp_path, monkeypatch):
    """PROJECT_ROOT is Path(__file__).resolve().parent — it must come out the
    same whether the process's cwd is the project root, some unrelated temp
    directory, or anything else (important once this runs under Windows Task
    Scheduler, whose default cwd has nothing to do with this project)."""
    expected_root = config_module.PROJECT_ROOT

    monkeypatch.chdir(tmp_path)
    try:
        reloaded = importlib.reload(config_module)
        assert reloaded.PROJECT_ROOT == expected_root
        assert reloaded.DATA_DIR == expected_root / "data"
        assert reloaded.LOG_DIR == expected_root / "logs"
        assert reloaded.DB_PATH == expected_root / "data" / "super_news.db"
        assert reloaded.TOKEN_STORE_PATH == expected_root / "data" / "kakao_token.json"
        assert reloaded.ENV_PATH == expected_root / ".env"
    finally:
        # Restore module state before cwd reverts, so later tests in this
        # process see a config module in its normal, un-reloaded shape.
        importlib.reload(config_module)


def test_paths_are_absolute():
    assert config_module.PROJECT_ROOT.is_absolute()
    assert config_module.DATA_DIR.is_absolute()
    assert config_module.LOG_DIR.is_absolute()
    assert config_module.DB_PATH.is_absolute()
    assert config_module.TOKEN_STORE_PATH.is_absolute()
