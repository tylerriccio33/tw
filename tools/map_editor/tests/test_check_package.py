"""check_package.py: validate-only CLI, no writes.

Delegates the actual checks to export.validate_package. These tests
cover the wiring. A valid package reports clean. An invalid one
surfaces its problems. A missing or malformed package directory names
itself instead of crashing.
"""

import check_package
import export
import mapfmt
import pytest
from tests.conftest import box, project_with


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


def test_check_reports_no_problems_for_a_valid_package(package):
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    assert check_package.check(package.root) == []


def bowtie_province():
    return [
        {
            "id": 1,
            "key": "west",
            "name": "West",
            "polygons": [[[10, 8], [30, 32], [30, 8], [10, 32]]],
        }
    ]


def test_check_reports_problems_for_an_invalid_package(package):
    project = project_with(package, provinces=bowtie_province())
    mapfmt.save_project(package.root, project)

    problems = check_package.check(package.root)
    assert problems


def test_check_raises_package_error_for_a_missing_directory(tmp_path):
    with pytest.raises(mapfmt.PackageError):
        check_package.check(tmp_path / "nowhere")


def test_main_exits_zero_and_prints_summary_for_a_valid_package(package, capsys):
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    import sys

    argv = sys.argv
    sys.argv = ["check_package.py", "--package-dir", str(package.root)]
    try:
        check_package.main()
    finally:
        sys.argv = argv

    out = capsys.readouterr().out
    assert "is valid" in out
    assert "not exported yet" in out


def test_main_reports_exported_province_count_when_a_table_exists(package, capsys):
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)
    export.export_package(project, package)

    import sys

    argv = sys.argv
    sys.argv = ["check_package.py", "--package-dir", str(package.root)]
    try:
        check_package.main()
    finally:
        sys.argv = argv

    out = capsys.readouterr().out
    assert "exported provinces: 2" in out


def test_main_exits_nonzero_and_lists_problems_for_an_invalid_package(package, capsys):
    project = project_with(package, provinces=bowtie_province())
    mapfmt.save_project(package.root, project)

    import sys

    argv = sys.argv
    sys.argv = ["check_package.py", "--package-dir", str(package.root)]
    try:
        with pytest.raises(SystemExit) as exc_info:
            check_package.main()
    finally:
        sys.argv = argv

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "problem(s)" in out


def test_main_exits_with_message_for_an_unreadable_package(tmp_path, capsys):
    import sys

    argv = sys.argv
    sys.argv = ["check_package.py", "--package-dir", str(tmp_path / "nowhere")]
    try:
        with pytest.raises(SystemExit) as exc_info:
            check_package.main()
    finally:
        sys.argv = argv

    assert "isn't a readable map package" in str(exc_info.value)
