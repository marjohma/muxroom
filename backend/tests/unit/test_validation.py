import pytest

from muxroom.errors import ERROR_SPECS, MuxroomError
from muxroom.validation import (
    expand_home,
    is_within,
    validate_filename,
    validate_relative_path,
    validate_session_id,
    validate_session_name,
    validate_terminal_size,
    validate_user_path,
)

INJECTIONS = ["a;b", "$(id)", "`id`", "a'b", 'a"b', "a\nb", "a b", "a.b", "a:b", "-x" * 40, ""]


@pytest.mark.parametrize("name", ["demo", "claude-proj_1", "A" * 64])
def test_valid_session_names(name):
    assert validate_session_name(name) == name


@pytest.mark.parametrize("name", INJECTIONS)
def test_invalid_session_names(name):
    with pytest.raises(MuxroomError) as err:
        validate_session_name(name)
    assert err.value.code == "invalid_name"


@pytest.mark.parametrize("sid", ["$0", "$123"])
def test_valid_session_ids(sid):
    assert validate_session_id(sid) == sid


@pytest.mark.parametrize("sid", ["3", "$", "$1;id", "=demo", "$1 ", "$-1"])
def test_invalid_session_ids(sid):
    with pytest.raises(MuxroomError):
        validate_session_id(sid)


@pytest.mark.parametrize("path", ["/", "/tmp/a b", "~", "~/proj", "/p/$(x)/`y`"])
def test_user_paths_accepted(path):
    assert validate_user_path(path) == path


@pytest.mark.parametrize("path", ["", "rel/dir", "~other/x", "/a\nb", "/a\x00b", "/" + "a" * 2000])
def test_user_paths_rejected(path):
    with pytest.raises(MuxroomError) as err:
        validate_user_path(path)
    assert err.value.code == "invalid_path"


def test_relative_paths():
    assert validate_relative_path("plots/a.pdf") == "plots/a.pdf"
    for bad in ["/etc/passwd", "~/x", "", "a\nb"]:
        with pytest.raises(MuxroomError):
            validate_relative_path(bad)


def test_expand_home():
    assert expand_home("~", "/home/u") == "/home/u"
    assert expand_home("~/a/../b", "/home/u") == "/home/u/b"
    assert expand_home("/x/./y/", "/home/u") == "/x/y"


def test_is_within_is_not_a_prefix_check():
    assert is_within("/home/a/proj", "/home/a/proj")
    assert is_within("/home/a/proj/x", "/home/a/proj")
    assert not is_within("/home/a/proj2", "/home/a/proj")
    assert is_within("/anything", "/")


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\x00", "x" * 300, ".muxroom-upload-1"])
def test_invalid_filenames(name):
    with pytest.raises(MuxroomError):
        validate_filename(name)


def test_terminal_size_bounds():
    assert validate_terminal_size(80, 24) == (80, 24)
    for cols, rows in [(1, 24), (80, 1), (5000, 24), (80, 9999)]:
        with pytest.raises(MuxroomError):
            validate_terminal_size(cols, rows)


def test_every_error_code_has_a_hint():
    assert all(spec.hint for spec in ERROR_SPECS.values())


def test_unknown_error_code_is_rejected():
    with pytest.raises(ValueError):
        MuxroomError("nope", "x")
