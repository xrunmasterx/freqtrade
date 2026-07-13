# pragma pylint: disable=missing-docstring, protected-access, invalid-name
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from freqtrade.configuration.directory_operations import (
    copy_sample_files,
    create_datadir,
    create_userdata_dir,
    ensure_user_directory_access,
)
from freqtrade.exceptions import OperationalException
from tests.conftest import log_has, log_has_re


def test_create_datadir(mocker, default_conf, caplog) -> None:
    mocker.patch.object(Path, "is_dir", MagicMock(return_value=False))
    md = mocker.patch.object(Path, "mkdir", MagicMock())

    create_datadir(default_conf, "/foo/bar")
    assert md.call_args[1]["parents"] is True
    assert log_has("Created data directory: /foo/bar", caplog)


def test_create_userdata_dir(mocker, tmp_path, caplog) -> None:
    mocker.patch.object(Path, "is_dir", MagicMock(return_value=False))
    md = mocker.patch.object(Path, "mkdir", MagicMock())

    x = create_userdata_dir(tmp_path / "bar", create_dir=True)
    assert md.call_count == 10
    assert md.call_args[1]["parents"] is False
    assert log_has(f"Created user-data directory: {tmp_path / 'bar'}", caplog)
    assert isinstance(x, Path)
    assert str(x) == str(tmp_path / "bar")


def test_ensure_user_directory_access_ignores_non_docker(mocker, tmp_path) -> None:
    access_mock = mocker.patch("os.access", return_value=False)
    mocker.patch(
        "freqtrade.configuration.directory_operations.running_in_docker",
        return_value=False,
    )

    ensure_user_directory_access(tmp_path)

    access_mock.assert_not_called()


def test_ensure_user_directory_access_accepts_writable_docker_path(mocker, tmp_path) -> None:
    mocker.patch(
        "freqtrade.configuration.directory_operations.running_in_docker",
        return_value=True,
    )
    access_mock = mocker.patch("os.access", return_value=True)

    ensure_user_directory_access(tmp_path)

    access_mock.assert_called_once_with(tmp_path, os.R_OK | os.W_OK | os.X_OK)


def test_ensure_user_directory_access_rejects_inaccessible_docker_path(
    mocker, tmp_path
) -> None:
    mocker.patch(
        "freqtrade.configuration.directory_operations.running_in_docker",
        return_value=True,
    )
    mocker.patch("os.access", return_value=False)

    with pytest.raises(
        OperationalException,
        match="not readable, writable, and searchable by the container user",
    ):
        ensure_user_directory_access(tmp_path)


def test_ensure_user_directory_access_allows_missing_path(mocker, tmp_path) -> None:
    missing = tmp_path / "will-be-created"
    mocker.patch(
        "freqtrade.configuration.directory_operations.running_in_docker",
        return_value=True,
    )
    access_mock = mocker.patch("os.access")

    ensure_user_directory_access(missing)

    access_mock.assert_not_called()


def test_create_userdata_dir_exists(mocker, tmp_path) -> None:
    mocker.patch.object(Path, "is_dir", MagicMock(return_value=True))
    md = mocker.patch.object(Path, "mkdir", MagicMock())

    create_userdata_dir(f"{tmp_path}/bar")
    assert md.call_count == 0


def test_create_userdata_dir_exists_exception(mocker, tmp_path) -> None:
    mocker.patch.object(Path, "is_dir", MagicMock(return_value=False))
    md = mocker.patch.object(Path, "mkdir", MagicMock())

    with pytest.raises(OperationalException, match=r"Directory `.*.{1,2}bar` does not exist.*"):
        create_userdata_dir(f"{tmp_path}/bar", create_dir=False)
    assert md.call_count == 0


def test_copy_sample_files(mocker, tmp_path) -> None:
    mocker.patch.object(Path, "is_dir", MagicMock(return_value=True))
    mocker.patch.object(Path, "exists", MagicMock(return_value=False))
    copymock = mocker.patch("shutil.copy", MagicMock())

    copy_sample_files(Path(f"{tmp_path}/bar"))
    assert copymock.call_count == 3
    assert copymock.call_args_list[0][0][1] == str(tmp_path / "bar/strategies/sample_strategy.py")
    assert copymock.call_args_list[1][0][1] == str(
        tmp_path / "bar/hyperopts/sample_hyperopt_loss.py"
    )
    assert copymock.call_args_list[2][0][1] == str(
        tmp_path / "bar/notebooks/strategy_analysis_example.ipynb"
    )


def test_copy_sample_files_errors(mocker, tmp_path, caplog) -> None:
    mocker.patch.object(Path, "is_dir", MagicMock(return_value=False))
    mocker.patch.object(Path, "exists", MagicMock(return_value=False))
    mocker.patch("shutil.copy", MagicMock())
    with pytest.raises(OperationalException, match=r"Directory `.*.{1,2}bar` does not exist\."):
        copy_sample_files(Path(f"{tmp_path}/bar"))

    mocker.patch.object(Path, "is_dir", MagicMock(side_effect=[True, False]))

    with pytest.raises(
        OperationalException,
        match=r"Directory `.*.{1,2}bar.{1,2}strategies` does not exist\.",
    ):
        copy_sample_files(Path(f"{tmp_path}/bar"))
    mocker.patch.object(Path, "is_dir", MagicMock(return_value=True))
    mocker.patch.object(Path, "exists", MagicMock(return_value=True))
    copy_sample_files(Path(f"{tmp_path}/bar"))
    assert log_has_re(r"File `.*` exists already, not deploying sample file\.", caplog)
    caplog.clear()
    copy_sample_files(Path(f"{tmp_path}/bar"), overwrite=True)
    assert log_has_re(r"File `.*` exists already, overwriting\.", caplog)
