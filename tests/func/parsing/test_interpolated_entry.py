import os
from copy import deepcopy

import pytest

from dvc.dependency import _merge_params
from dvc.parsing import DEFAULT_PARAMS_FILE, DataResolver
from dvc.parsing.context import recurse_not_a_node
from dvc.parsing.interpolate import escape_str

from . import (
    CONTEXT_DATA,
    RESOLVED_DVC_YAML_DATA,
    TEMPLATED_DVC_YAML_DATA,
    USED_VARS,
)


def assert_stage_equal(d1, d2):
    """Keeps the params section in order, and then checks for equality."""
    for d in [d1, d2]:
        assert recurse_not_a_node(d)
        for _, stage_d in d.get("stages", {}).items():
            params = _merge_params(stage_d.get("params", []))
            for k in params:
                params[k] = sorted(params[k])
            if params:
                stage_d["params"] = params
    assert d1 == d2


def test_simple(tmp_dir, dvc):
    (tmp_dir / DEFAULT_PARAMS_FILE).dump(CONTEXT_DATA)
    resolver = DataResolver(
        dvc, tmp_dir.fs_path, deepcopy(TEMPLATED_DVC_YAML_DATA)
    )
    assert_stage_equal(resolver.resolve(), deepcopy(RESOLVED_DVC_YAML_DATA))
    assert resolver.tracked_vars == {
        "stage1": {DEFAULT_PARAMS_FILE: USED_VARS["stage1"]},
        "stage2": {DEFAULT_PARAMS_FILE: USED_VARS["stage2"]},
    }


def test_vars_import(tmp_dir, dvc):
    """
    Test that different file can be loaded using `vars`
    instead of default params.yaml.
    """
    (tmp_dir / "params2.yaml").dump(CONTEXT_DATA)
    d = deepcopy(TEMPLATED_DVC_YAML_DATA)
    d["vars"] = ["params2.yaml"]
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)

    resolved_data = deepcopy(RESOLVED_DVC_YAML_DATA)
    assert_stage_equal(resolver.resolve(), resolved_data)
    assert resolver.tracked_vars == {
        "stage1": {"params2.yaml": USED_VARS["stage1"]},
        "stage2": {"params2.yaml": USED_VARS["stage2"]},
    }


def test_vars_and_params_import(tmp_dir, dvc):
    """
    Test that vars and params are both merged together for interpolation,
    whilst tracking the "used" variables from params.
    """
    d = {
        "vars": [DEFAULT_PARAMS_FILE, {"dict": {"foo": "foobar"}}],
        "stages": {"stage1": {"cmd": "echo ${dict.foo} ${dict.bar}"}},
    }
    (tmp_dir / DEFAULT_PARAMS_FILE).dump({"dict": {"bar": "bar"}})
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)

    assert_stage_equal(
        resolver.resolve(), {"stages": {"stage1": {"cmd": "echo foobar bar"}}}
    )
    assert resolver.tracked_vars == {
        "stage1": {DEFAULT_PARAMS_FILE: {"dict.bar": "bar"}}
    }


def test_stage_with_wdir(tmp_dir, dvc):
    """
    Test that params file from wdir are also loaded
    """
    d = {
        "stages": {
            "stage1": {
                "cmd": "echo ${dict.foo} ${dict.bar}",
                "params": ["value1"],
                "wdir": "data",
                "vars": [DEFAULT_PARAMS_FILE],
            }
        }
    }

    data_dir = tmp_dir / "data"
    data_dir.mkdir()
    (tmp_dir / DEFAULT_PARAMS_FILE).dump({"dict": {"bar": "bar"}})
    (data_dir / DEFAULT_PARAMS_FILE).dump({"dict": {"foo": "foo"}})
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)

    assert_stage_equal(
        resolver.resolve(),
        {
            "stages": {
                "stage1": {
                    "cmd": "echo foo bar",
                    "wdir": "data",
                    "params": ["value1"],
                }
            }
        },
    )
    assert resolver.tracked_vars == {
        "stage1": {
            os.path.join("data", DEFAULT_PARAMS_FILE): {"dict.foo": "foo"},
            DEFAULT_PARAMS_FILE: {"dict.bar": "bar"},
        }
    }


def test_with_templated_wdir(tmp_dir, dvc):
    """
    Test that params from the resolved wdir are still loaded
    and is used in the interpolation.
    """
    d = {
        "stages": {
            "stage1": {
                "cmd": "echo ${dict.foo} ${dict.bar}",
                "params": ["value1"],
                "wdir": "${dict.ws}",
                "vars": [DEFAULT_PARAMS_FILE],
            }
        }
    }
    (tmp_dir / DEFAULT_PARAMS_FILE).dump(
        {"dict": {"bar": "bar", "ws": "data"}}
    )
    data_dir = tmp_dir / "data"
    data_dir.mkdir()
    (data_dir / DEFAULT_PARAMS_FILE).dump({"dict": {"foo": "foo"}})
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)

    assert_stage_equal(
        resolver.resolve(),
        {
            "stages": {
                "stage1": {
                    "cmd": "echo foo bar",
                    "wdir": "data",
                    "params": ["value1"],
                }
            }
        },
    )
    assert resolver.tracked_vars == {
        "stage1": {
            os.path.join("data", DEFAULT_PARAMS_FILE): {"dict.foo": "foo"},
            DEFAULT_PARAMS_FILE: {"dict.bar": "bar", "dict.ws": "data"},
        }
    }
    assert resolver.context.imports == {"params.yaml": None}
    assert resolver.context == {"dict": {"bar": "bar", "ws": "data"}}


def test_resolve_local_tries_to_load_globally_used_files(tmp_dir, dvc):
    iterable = {"bar": "bar", "foo": "foo"}
    (tmp_dir / "params.json").dump(iterable)

    d = {
        "vars": ["params.json"],
        "stages": {
            "build": {
                "cmd": "command --value ${bar}",
                "params": [{"params.json": ["foo"]}],
                "vars": ["params.json"],
            }
        },
    }
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)
    assert_stage_equal(
        resolver.resolve(),
        {
            "stages": {
                "build": {
                    "cmd": "command --value bar",
                    "params": [{"params.json": ["foo"]}],
                }
            }
        },
    )
    assert resolver.tracked_vars == {"build": {"params.json": {"bar": "bar"}}}


def test_resolve_local_tries_to_load_globally_used_params_yaml(tmp_dir, dvc):
    iterable = {"bar": "bar", "foo": "foo"}
    (tmp_dir / "params.yaml").dump(iterable)

    d = {
        "stages": {
            "build": {
                "cmd": "command --value ${bar}",
                "params": [{"params.yaml": ["foo"]}],
                "vars": ["params.yaml"],
            }
        }
    }
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)
    assert_stage_equal(
        resolver.resolve(),
        {
            "stages": {
                "build": {
                    "cmd": "command --value bar",
                    "params": [{"params.yaml": ["foo"]}],
                }
            }
        },
    )
    assert resolver.tracked_vars == {"build": {"params.yaml": {"bar": "bar"}}}


def test_vars_relpath_overwrite(tmp_dir, dvc):
    iterable = {"bar": "bar", "foo": "foo"}
    (tmp_dir / "params.yaml").dump(iterable)
    d = {
        "vars": ["params.yaml"],
        "stages": {
            "build": {
                "wdir": "data",
                "cmd": "echo ${bar}",
                "vars": ["../params.yaml"],
            }
        },
    }
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)
    resolver.resolve()
    assert resolver.context.imports == {"params.yaml": None}


@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize(
    "vars_",
    [
        ["test_params.yaml:bar", "test_params.yaml:foo"],
        ["test_params.yaml:foo,bar"],
        ["test_params.yaml"],
        ["test_params.yaml", "test_params.yaml"],
    ],
)
def test_vars_load_partial(tmp_dir, dvc, local, vars_):
    iterable = {"bar": "bar", "foo": "foo"}
    (tmp_dir / "test_params.yaml").dump(iterable)
    d = {"stages": {"build": {"cmd": "echo ${bar}"}}}
    if local:
        d["stages"]["build"]["vars"] = vars_
    else:
        d["vars"] = vars_
    resolver = DataResolver(dvc, tmp_dir.fs_path, d)
    resolver.resolve()


@pytest.mark.parametrize(
    "bool_config, list_config",
    [(None, None), ("store_true", "nargs"), ("boolean_optional", "append")],
)
def test_cmd_dict(tmp_dir, dvc, bool_config, list_config):
    with dvc.config.edit() as conf:
        if bool_config:
            conf["parsing"]["bool"] = bool_config
        if list_config:
            conf["parsing"]["list"] = list_config

    string = "spaced string"
    mixed_quote_string = "quote\"'d"
    data = {
        "dict": {
            "foo": "foo",
            "bar": 2,
            "string": string,
            "mixed_quote_string": mixed_quote_string,
            "bool": True,
            "bool-false": False,
            "list": [1, 2, "foo", mixed_quote_string],
            "nested": {"foo": "foo"},
        }
    }
    (tmp_dir / DEFAULT_PARAMS_FILE).dump(data)
    resolver = DataResolver(
        dvc,
        tmp_dir.fs_path,
        {"stages": {"stage1": {"cmd": "python script.py ${dict}"}}},
    )

    if bool_config is None or bool_config == "store_true":
        bool_resolved = " --bool"
    else:
        bool_resolved = " --bool --no-bool-false"

    if list_config is None or list_config == "nargs":
        list_resolved = f" --list 1 2 foo {escape_str(mixed_quote_string)}"
    else:
        list_resolved = " --list 1 --list 2 --list foo"
        list_resolved += f" --list {escape_str(mixed_quote_string)}"

    assert_stage_equal(
        resolver.resolve(),
        {
            "stages": {
                "stage1": {
                    "cmd": "python script.py"
                    " --foo foo --bar 2"
                    f" --string {escape_str(string)}"
                    f" --mixed_quote_string {escape_str(mixed_quote_string)}"
                    f"{bool_resolved}"
                    f"{list_resolved}"
                    " --nested.foo foo"
                }
            }
        },
    )
