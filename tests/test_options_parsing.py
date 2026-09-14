import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from run import solve
from run.solve import load_config_files


@pytest.fixture
def temp_config_files():
    """Create temporary config files for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_config = {"solve_name": "test", "horizon": 5, "iterations": 100}
        base_path = Path(tmpdir) / "base_config.json"
        with open(base_path, "w") as f:
            json.dump(base_config, f)

        override_config = {"iterations": 200, "export_image": True}
        override_path = Path(tmpdir) / "override_config.json"
        with open(override_path, "w") as f:
            json.dump(override_config, f)

        yield {"base_path": str(base_path), "override_path": str(override_path)}


def test_load_single_config(temp_config_files):
    """Test loading a single configuration file."""
    config = load_config_files(temp_config_files["base_path"])
    assert config["solve_name"] == "test"
    assert config["horizon"] == 5
    assert config["iterations"] == 100


def test_load_multiple_configs(temp_config_files):
    """Test loading multiple configuration files with overrides."""
    config_paths = f"{temp_config_files['base_path']};{temp_config_files['override_path']}"
    config = load_config_files(config_paths)

    assert config["solve_name"] == "test"
    assert config["horizon"] == 5

    assert config["iterations"] == 200
    assert config["export_image"]


def test_load_nonexistent_config():
    """Test handling of nonexistent configuration file."""
    config = load_config_files("nonexistent.json")
    assert config == {}


def test_load_invalid_json(tmp_path):
    """Test handling of invalid JSON configuration file."""
    invalid_config = tmp_path / "invalid.json"
    invalid_config.write_text("{invalid json")
    config = load_config_files(str(invalid_config))
    assert config == {}


def test_empty_config_path():
    """Test handling of empty configuration path."""
    config = load_config_files("")
    assert config == {}


def test_semicolon_only_config_path():
    """Test handling of config path with only semicolons."""
    config = load_config_files(";;")
    assert config == {}


def test_whitespace_only_config_path(capsys):
    """Test that whitespace-only config paths are skipped silently."""
    config = load_config_files(" ; ")
    captured = capsys.readouterr()
    assert config == {}
    assert captured.out == ""


@pytest.fixture
def mock_argv(monkeypatch):
    """Fixture to temporarily replace sys.argv"""

    def _mock_argv(args):
        monkeypatch.setattr(sys.argv, args)

    return _mock_argv


def create_arg_parser():
    """Helper function to create argument parser similar to solve_regular.py"""
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument("--config", type=str, help="Path to one or more configuration files (semicolon-delimited)")
    return base_parser


def test_cli_no_config_argument():
    """Test CLI parsing with no config argument."""
    parser = create_arg_parser()
    args = parser.parse_known_args(["--other-arg", "value"])[0]
    assert args.config is None


def test_cli_single_config():
    """Test CLI parsing with single config path."""
    parser = create_arg_parser()
    args = parser.parse_known_args(["--config", "path/to/config.json"])[0]
    assert args.config == "path/to/config.json"


def test_cli_multiple_configs():
    """Test CLI parsing with multiple semicolon-separated config paths."""
    parser = create_arg_parser()
    args = parser.parse_known_args(["--config", "config1.json;config2.json"])[0]
    assert args.config == "config1.json;config2.json"


def test_config_priority_order(temp_config_files, monkeypatch):
    """Test that configuration priority is respected: base -> override configs -> CLI args"""

    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument("--config", type=str)

    base_config = {"solve_name": "base", "horizon": 5, "iterations": 100}
    override_config = {"horizon": 7, "export_image": True}

    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir) / "base.json"
        with open(base_path, "w") as f:
            json.dump(base_config, f)

        override_path = Path(tmpdir) / "override.json"
        with open(override_path, "w") as f:
            json.dump(override_config, f)

        test_args = ["script.py", "--config", f"{base_path};{override_path}", "--horizon", "10"]
        monkeypatch.setattr(sys, "argv", test_args)

        base_args, remaining = base_parser.parse_known_args()

        options = base_config.copy()
        if base_args.config:
            config_options = load_config_files(base_args.config)
            options.update(config_options)

        parser = argparse.ArgumentParser(parents=[base_parser])
        for key, value in options.items():
            if not isinstance(value, list | dict):
                parser.add_argument(f"--{key}", type=type(value), default=value)

        args = parser.parse_args(remaining)
        cli_options = {k: v for k, v in vars(args).items() if v is not None and k != "config"}
        options.update(cli_options)

        assert options["solve_name"] == "base"
        assert options["horizon"] == 10
        assert options["iterations"] == 100
        assert options["export_image"]


def test_partial_cli_override(temp_config_files, monkeypatch):
    """Test that CLI arguments only override specified values"""
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument("--config", type=str)

    with tempfile.TemporaryDirectory() as tmpdir:
        config = {"solve_name": "test", "horizon": 5, "iterations": 100}
        config_path = Path(tmpdir) / "config.json"
        with open(config_path, "w") as f:
            json.dump(config, f)

        test_args = ["script.py", "--config", str(config_path), "--horizon", "7"]
        monkeypatch.setattr(sys, "argv", test_args)

        base_args, remaining = base_parser.parse_known_args()
        options = load_config_files(base_args.config)

        parser = argparse.ArgumentParser(parents=[base_parser])
        for key, value in options.items():
            if not isinstance(value, list | dict):
                parser.add_argument(f"--{key}", type=type(value), default=value)

        args = parser.parse_args(remaining)
        cli_options = {k: v for k, v in vars(args).items() if v is not None and k != "config"}
        options.update(cli_options)

        assert options["solve_name"] == "test"
        assert options["horizon"] == 7
        assert options["iterations"] == 100  # Unchanged from config


@pytest.fixture
def capture_solve_options(monkeypatch):
    """Run the real CLI parsing and stop before data preparation or optimization."""

    def capture(arguments, runtime_options=None):
        monkeypatch.setattr(sys, "argv", ["solve.py", *arguments])
        monkeypatch.setattr(
            solve,
            "load_settings",
            lambda: {
                "preseason": True,
                "team_data": "id",
                "team_id": 12345,
                "export_image": True,
                "randomized": False,
            },
        )
        monkeypatch.setattr(solve, "generate_team_json", Mock(return_value={"picks": [{"element": 1}]}))
        prep_data = Mock(side_effect=StopIteration)
        monkeypatch.setattr(solve, "prep_data", prep_data)

        with pytest.raises(StopIteration):
            solve.solve_regular(runtime_options)

        return prep_data.call_args.args

    return capture


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_solve_cli_boolean_values(capture_solve_options, value, expected):
    _, options = capture_solve_options(["--export_image", value, "--randomized", value])
    assert options["export_image"] is expected
    assert options["randomized"] is expected


@pytest.mark.parametrize("value", ["false", "0"])
def test_solve_cli_false_preseason_keeps_existing_squad(capture_solve_options, value):
    my_data, options = capture_solve_options(["--preseason", value])
    assert options["preseason"] is False
    assert my_data["picks"] == [{"element": 1}]


def test_solve_cli_omitted_booleans_keep_defaults(capture_solve_options):
    my_data, options = capture_solve_options([])
    assert options["export_image"] is True
    assert options["randomized"] is False
    assert options["preseason"] is True
    assert my_data["picks"] == []


def test_solve_cli_boolean_overrides_config(capture_solve_options, tmp_path):
    config_path = tmp_path / "booleans.json"
    config_path.write_text(json.dumps({"export_image": False, "randomized": True}))

    _, options = capture_solve_options(["--config", str(config_path), "--randomized", "false"])
    assert options["export_image"] is False
    assert options["randomized"] is False


def test_solve_runtime_boolean_overrides_cli(capture_solve_options):
    _, options = capture_solve_options(["--export_image", "false"], runtime_options={"export_image": True})
    assert options["export_image"] is True


@pytest.mark.parametrize("value", ["maybe", "2", ""])
def test_solve_cli_rejects_invalid_boolean(capture_solve_options, capsys, value):
    with pytest.raises(SystemExit) as exc:
        capture_solve_options(["--export_image", value])

    assert exc.value.code == 2
    assert "--export_image" in capsys.readouterr().err
