from pathlib import Path

from scripts.check_junit_skips import main


def _write_report(path: Path, skipped: int) -> None:
    path.write_text(
        f'<testsuites><testsuite name="pytest" skipped="{skipped}" /></testsuites>',
        encoding="utf-8",
    )


def test_junit_skip_guard_accepts_zero_skips(tmp_path: Path, capsys) -> None:
    report = tmp_path / "zero-skips.xml"
    _write_report(report, skipped=0)

    assert main([str(report)]) == 0
    assert capsys.readouterr().out == "PostgreSQL integration tests skipped: 0\n"


def test_junit_skip_guard_rejects_nonzero_skips(tmp_path: Path, capsys) -> None:
    report = tmp_path / "skips.xml"
    _write_report(report, skipped=2)

    assert main([str(report)]) == 1
    assert capsys.readouterr().err == "PostgreSQL integration tests skipped: 2\n"


def test_junit_skip_guard_rejects_missing_report(tmp_path: Path, capsys) -> None:
    assert main([str(tmp_path / "missing.xml")]) == 2
    assert "JUnit report not found" in capsys.readouterr().err


def test_junit_skip_guard_rejects_malformed_report(tmp_path: Path, capsys) -> None:
    report = tmp_path / "malformed.xml"
    report.write_text("<testsuites>", encoding="utf-8")

    assert main([str(report)]) == 2
    assert "Invalid JUnit XML report" in capsys.readouterr().err
