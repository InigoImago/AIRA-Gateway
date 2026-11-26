"""What a release is checked for, and what the check says (`FRD-154`).

What a reader relies on without looking:
- what is listed **to act on** is exactly what an update would remove or what somebody already
  exploits;
- an acceptance holds for the vulnerability, the package and the documents it names, until its
  date and no longer;
- the documents read are the ones the release published;
- **a check that was not made never reads as a clean one**.

Grype is replaced by a stand-in that answers in Grype's JSON (`grype/presenter/models` of the pinned
version). The real one was run by hand against a published release (`FRD-154` §7).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import release_vulnerabilities as vulns

ROOT = Path(__file__).resolve().parents[2]
VERSION = "v1.2.3"
TODAY = date(2026, 11, 26)
BUILT = "2026-11-26T04:12:34Z"


def _match(
    identifier: str,
    package: str,
    version: str = "1.0",
    *,
    severity: str = "High",
    state: str = "fixed",
    fixed: tuple[str, ...] = ("1.1",),
    kind: str = "python",
    exploited: bool = False,
    related: tuple[str, ...] = (),
    related_exploited: bool = False,
    link: str = "",
) -> dict[str, Any]:
    """One entry of Grype's `matches`, in the shape its JSON presenter writes."""
    vulnerability: dict[str, Any] = {
        "id": identifier,
        "dataSource": link or f"https://nvd.nist.gov/vuln/detail/{identifier}",
        "severity": severity,
        "urls": [],
        "cvss": [],
        "fix": {"versions": list(fixed), "state": state},
        "advisories": [],
        "risk": 1.0,
    }
    if exploited:
        vulnerability["knownExploited"] = [{"cve": identifier, "knownRansomwareCampaignUse": ""}]
    return {
        "vulnerability": vulnerability,
        "relatedVulnerabilities": [
            {
                "id": other,
                "dataSource": "",
                "urls": [],
                "cvss": [],
                **(
                    {"knownExploited": [{"cve": other, "knownRansomwareCampaignUse": ""}]}
                    if related_exploited
                    else {}
                ),
            }
            for other in related
        ],
        "matchDetails": [],
        "artifact": {
            "id": "x",
            "name": package,
            "version": version,
            "type": kind,
            "locations": [],
            "language": "",
            "licenses": [],
            "cpes": [],
            "purl": "",
            "upstreams": [],
        },
    }


class Grype:
    """Answers like Grype: a database it fetched, its version, and per document the matches given.
    Remembers what it was asked, in order."""

    def __init__(self, matches: dict[str, list[dict[str, Any]]] | None = None, **status: Any):
        self.matches = matches or {}
        self.status = {"schemaVersion": "v6.1.9", "built": BUILT, "valid": True, **status}
        self.calls: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> str:
        self.calls.append(arguments)
        if arguments[:2] == ["db", "update"]:
            return ""
        if arguments[:2] == ["db", "status"]:
            return json.dumps(self.status)
        if arguments[0] == "version":
            return json.dumps({"application": "grype", "version": "0.121.3"})
        assert arguments[0].startswith("sbom:"), arguments
        name = Path(arguments[0].removeprefix("sbom:")).name.removesuffix(f"-{VERSION}.cdx.json")
        return json.dumps({"matches": self.matches.get(name, []), "descriptor": {}})


def _document(directory: Path, name: str, *, pypi: tuple[str, ...] = (), version: str = VERSION):
    """A document and its checksum, as the release writes them (`release_sbom`)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{version}.cdx.json"
    components = [{"type": "library", "name": p, "purl": f"pkg:pypi/{p}@1.0"} for p in pypi]
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": components}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n")
    return path


def _release(tmp_path: Path, *, pypi: tuple[str, ...] = ("urllib3",)) -> Path:
    sboms = tmp_path / "sboms"
    _document(sboms, "aira-gateway", pypi=pypi)
    _document(sboms, "aira-management", pypi=pypi)
    _document(sboms, "aira-source")
    return sboms


def _accepted(tmp_path: Path, *entries: str) -> Path:
    path = tmp_path / "accepted.toml"
    path.write_text("\n".join(f"[[accepted]]\n{entry}" for entry in entries))
    return path


def _entry(
    vulnerability: str = "CVE-2026-1",
    package: str = "urllib3",
    until: date = TODAY,
    reason: str = "Not reachable: AIRA never follows a redirect.",
    extra: str = "",
) -> str:
    return (
        f'vulnerability = "{vulnerability}"\npackage = "{package}"\nreason = "{reason}"\n'
        f"until = {until.isoformat()}\n{extra}"
    )


def _check(tmp_path: Path, grype: Grype, *entries: str, today: date = TODAY) -> vulns.Result:
    return vulns.check(
        VERSION,
        _release(tmp_path),
        accepted=_accepted(tmp_path, *entries),
        run=grype,
        today=today,
    )


def _ids(found: list[vulns.Finding]) -> set[tuple[str, str]]:
    return {(finding.vulnerability, finding.document) for finding in found}


# == the groups ==================================================================================


def test_a_fixable_critical_or_high_is_to_act_on_and_one_without_a_fix_is_to_watch(
    tmp_path: Path,
) -> None:
    grype = Grype(
        {
            "aira-gateway": [
                _match("CVE-2026-1", "urllib3", severity="High"),
                _match("CVE-2026-2", "openssl", severity="Critical", kind="deb"),
                _match("CVE-2026-3", "libc6", severity="Critical", state="not-fixed", fixed=()),
                _match("CVE-2026-4", "zlib1g", severity="High", state="wont-fix", fixed=()),
            ]
        }
    )
    result = _check(tmp_path, grype)

    assert _ids(result.act) == {("CVE-2026-1", "aira-gateway"), ("CVE-2026-2", "aira-gateway")}
    assert _ids(result.watch) == {("CVE-2026-3", "aira-gateway"), ("CVE-2026-4", "aira-gateway")}
    assert result.status == vulns.ACT


def test_a_fix_state_without_a_fixed_version_is_nothing_to_update_to(tmp_path: Path) -> None:
    grype = Grype({"aira-gateway": [_match("CVE-2026-1", "urllib3", fixed=())]})

    assert _ids(_check(tmp_path, grype).watch) == {("CVE-2026-1", "aira-gateway")}


def test_an_exploited_vulnerability_is_to_act_on_whatever_its_severity_and_fix(
    tmp_path: Path,
) -> None:
    """CISA's catalogue lists what somebody is using, which is not something to watch. It may be on
    the alias Grype related the finding to rather than on the finding's own id."""
    grype = Grype(
        {
            "aira-gateway": [
                _match(
                    "CVE-2026-1",
                    "a",
                    severity="Medium",
                    state="not-fixed",
                    fixed=(),
                    exploited=True,
                ),
                _match(
                    "GHSA-xxxx-yyyy-zzzz",
                    "b",
                    severity="Low",
                    state="not-fixed",
                    fixed=(),
                    related=("CVE-2026-2",),
                    related_exploited=True,
                ),
            ]
        }
    )
    result = _check(tmp_path, grype)

    assert _ids(result.act) == {
        ("CVE-2026-1", "aira-gateway"),
        ("GHSA-xxxx-yyyy-zzzz", "aira-gateway"),
    }
    assert all(finding.exploited for finding in result.act)


def test_medium_and_below_are_counted_and_raise_no_alarm(tmp_path: Path) -> None:
    grype = Grype(
        {
            "aira-gateway": [
                _match("CVE-2026-1", "a", severity="Medium"),
                _match("CVE-2026-2", "b", severity="Low"),
                _match("CVE-2026-3", "c", severity="Negligible"),
                _match("CVE-2026-4", "d", severity="Unknown"),
                _match("CVE-2026-5", "e", severity="something Grype added"),
            ]
        }
    )
    result = _check(tmp_path, grype)

    assert not result.act and not result.watch
    assert len(result.other) == 5
    assert result.status == vulns.CLEAN
    assert "| aira-gateway | 0 | 0 | 1 | 1 | 3 | 0 | 0 | 0 |" in vulns.report(result)


# == acceptances =================================================================================


def test_an_acceptance_holds_until_its_date_and_not_a_day_longer(tmp_path: Path) -> None:
    grype = Grype({"aira-gateway": [_match("CVE-2026-1", "urllib3")]})

    on_the_day = _check(tmp_path, grype, _entry(until=TODAY))
    after = _check(tmp_path, grype, _entry(until=TODAY - timedelta(days=1)))

    assert not on_the_day.act and [f.vulnerability for f, _ in on_the_day.accepted] == [
        "CVE-2026-1"
    ]
    assert _ids(after.act) == {("CVE-2026-1", "aira-gateway")} and not after.accepted
    assert [a.vulnerability for a in after.expired] == ["CVE-2026-1"]
    assert "## Acceptances that ran out" in vulns.report(after)


def test_an_acceptance_names_the_package_as_well_as_the_vulnerability(tmp_path: Path) -> None:
    """One CVE is often in several packages, and accepting it in one says nothing about another."""
    grype = Grype(
        {
            "aira-gateway": [
                _match("CVE-2026-1", "libssl3", kind="deb"),
                _match("CVE-2026-1", "openssl", kind="deb"),
            ]
        }
    )
    result = _check(tmp_path, grype, _entry(package="libssl3"))

    assert [f.package for f in result.act] == ["openssl"]
    assert [f.package for f, _ in result.accepted] == ["libssl3"]


def test_an_acceptance_for_one_document_leaves_the_others_to_act_on(tmp_path: Path) -> None:
    grype = Grype(
        {
            "aira-gateway": [_match("CVE-2026-1", "urllib3")],
            "aira-management": [_match("CVE-2026-1", "urllib3")],
        }
    )
    result = _check(tmp_path, grype, _entry(extra='documents = ["aira-gateway"]'))

    assert _ids(result.act) == {("CVE-2026-1", "aira-management")}
    assert [f.document for f, _ in result.accepted] == ["aira-gateway"]


def test_an_acceptance_may_name_the_vulnerability_by_an_alias(tmp_path: Path) -> None:
    grype = Grype(
        {"aira-gateway": [_match("CVE-2026-1", "urllib3", related=("GHSA-aaaa-bbbb-cccc",))]}
    )

    result = _check(tmp_path, grype, _entry(vulnerability="GHSA-aaaa-bbbb-cccc"))

    assert not result.act and len(result.accepted) == 1


def test_an_acceptance_that_matches_nothing_is_named_so_it_can_go(tmp_path: Path) -> None:
    result = _check(tmp_path, Grype(), _entry(vulnerability="CVE-2025-9"))

    assert [a.vulnerability for a in result.unused] == ["CVE-2025-9"]
    assert "CVE-2025-9 in urllib3: no longer found" in vulns.report(result)


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        (_entry(reason=" "), "`reason` is required"),
        ('vulnerability = "CVE-2026-1"\npackage = "x"\nuntil = 2026-12-01', "`reason` is required"),
        (_entry().replace('package = "urllib3"\n', ""), "`package` is required"),
        (_entry().replace("until = 2026-11-26", 'until = "2026-12-01"'), "`until` is a date"),
        (
            _entry().replace("until = 2026-11-26", "until = 2026-12-01T00:00:00"),
            "`until` is a date",
        ),
        (_entry().replace("until", "untill"), "unknown keys ['untill']"),
        (_entry(until=TODAY + timedelta(days=366)), "more than a year"),
        (_entry(extra='documents = ["gateway"]'), "`documents` names documents"),
    ],
)
def test_an_acceptance_that_is_not_a_decision_a_reader_can_check_is_refused(
    tmp_path: Path, entry: str, says: str
) -> None:
    with pytest.raises(vulns.Unchecked, match=says.replace("[", r"\[").replace("]", r"\]")):
        vulns.acceptances(_accepted(tmp_path, entry), TODAY)


def test_an_acceptance_may_reach_a_year_ahead(tmp_path: Path) -> None:
    found = vulns.acceptances(_accepted(tmp_path, _entry(until=TODAY + timedelta(days=365))), TODAY)

    assert [a.until for a in found] == [TODAY + timedelta(days=365)]


def test_an_acceptance_file_with_anything_but_entries_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "accepted.toml"
    path.write_text('ignore = ["CVE-2026-1"]\n')

    with pytest.raises(vulns.Unchecked, match="unknown keys"):
        vulns.acceptances(path, TODAY)


def test_the_repository_s_acceptance_file_is_one_the_check_reads() -> None:
    """The nightly check reads it from `main`. A file it refuses fails every night, whatever the
    release contains."""
    vulns.acceptances(vulns.ACCEPTED, TODAY)


# == the documents read ==========================================================================


def test_every_document_of_the_release_is_read_against_one_database_fetched_first(
    tmp_path: Path,
) -> None:
    grype = Grype()
    result = _check(tmp_path, grype)

    assert grype.calls[:3] == [
        ["db", "update"],
        ["db", "status", "--output", "json"],
        ["version", "--output", "json"],
    ]
    scans = grype.calls[3:]
    assert sorted(Path(call[0].removeprefix("sbom:")).name for call in scans) == [
        f"aira-gateway-{VERSION}.cdx.json",
        f"aira-management-{VERSION}.cdx.json",
        f"aira-source-{VERSION}.cdx.json",
    ]
    assert all(call[1:] == ["--by-cve", "--output", "json", "--quiet"] for call in scans)
    assert result.documents == ["aira-gateway", "aira-management", "aira-source"]
    assert result.database == BUILT and result.scanner == "0.121.3"


def test_grype_runs_against_the_database_it_fetched_and_asks_about_no_update(
    tmp_path: Path,
) -> None:
    """With auto-update on, each document would be read against whatever was newest at its turn."""
    script = tmp_path / "grype"
    script.write_text(
        f"#!{sys.executable}\nimport os\n"
        "print(os.environ['GRYPE_DB_AUTO_UPDATE'], os.environ['GRYPE_CHECK_FOR_APP_UPDATE'])\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    assert vulns._grype(str(script))(["db", "status"]).split() == ["false", "false"]


def test_a_document_whose_checksum_does_not_match_is_refused(tmp_path: Path) -> None:
    sboms = _release(tmp_path)
    (sboms / f"aira-gateway-{VERSION}.cdx.json").write_text('{"components": []}')

    with pytest.raises(vulns.Unchecked, match="not the file its checksum was written for"):
        vulns.documents(sboms, VERSION)


def test_a_document_without_its_checksum_is_refused(tmp_path: Path) -> None:
    sboms = _release(tmp_path)
    (sboms / f"aira-gateway-{VERSION}.cdx.json.sha256").unlink()

    with pytest.raises(vulns.Unchecked, match="has no .*sha256 beside it"):
        vulns.documents(sboms, VERSION)


def test_a_checksum_of_another_file_is_refused(tmp_path: Path) -> None:
    sboms = _release(tmp_path)
    checksum = sboms / f"aira-gateway-{VERSION}.cdx.json.sha256"
    checksum.write_text(checksum.read_text().replace("aira-gateway", "aira-management"))

    with pytest.raises(vulns.Unchecked, match="is not a checksum of"):
        vulns.documents(sboms, VERSION)


def test_a_document_of_another_version_is_refused(tmp_path: Path) -> None:
    sboms = _release(tmp_path)
    _document(sboms, "aira-frontend", version="v1.2.2")

    with pytest.raises(vulns.Unchecked, match="is not a document of v1.2.3"):
        vulns.documents(sboms, VERSION)


def test_a_release_without_its_source_document_is_refused(tmp_path: Path) -> None:
    """The console's npm packages are compiled into its bundle and listed only there."""
    sboms = _release(tmp_path)
    (sboms / f"aira-source-{VERSION}.cdx.json").unlink()

    with pytest.raises(vulns.Unchecked, match="no aira-source-v1.2.3.cdx.json"):
        vulns.documents(sboms, VERSION)


def test_a_release_without_an_image_s_document_is_refused(tmp_path: Path) -> None:
    sboms = tmp_path / "sboms"
    _document(sboms, "aira-source")

    with pytest.raises(vulns.Unchecked, match="no image's document"):
        vulns.documents(sboms, VERSION)


# == a check that was not made ===================================================================


def test_a_database_grype_calls_unusable_is_refused(tmp_path: Path) -> None:
    grype = Grype(valid=False, error="database does not exist")

    with pytest.raises(vulns.Unchecked, match="database does not exist"):
        _check(tmp_path, grype)
    assert not any(call[0].startswith("sbom:") for call in grype.calls)


def test_grype_failing_or_missing_is_a_check_not_made(tmp_path: Path) -> None:
    with pytest.raises(vulns.Unchecked, match="failed"):
        vulns._grype("false")(["db", "update"])
    with pytest.raises(vulns.Unchecked, match="failed"):
        vulns._grype(str(tmp_path / "no-such-grype"))(["db", "update"])


@pytest.mark.parametrize(
    "output",
    ["", "not json", "{}", '{"matches": {}}', '{"matches": [{"vulnerability": {}}]}', "[]"],
)
def test_a_report_that_is_not_grype_s_is_refused_rather_than_read_as_clean(output: str) -> None:
    with pytest.raises(vulns.Unchecked, match="could not be read"):
        vulns.findings("aira-gateway", output)


def test_an_empty_list_of_matches_is_a_clean_document() -> None:
    assert vulns.findings("aira-gateway", '{"matches": []}') == set()


# == what the report says ========================================================================


def test_the_report_names_what_to_act_on_with_the_version_that_removes_it(tmp_path: Path) -> None:
    grype = Grype(
        {
            "aira-gateway": [_match("CVE-2026-1", "urllib3", "2.5.0", fixed=("2.6.0", "2.6.1"))],
            "aira-management": [_match("CVE-2026-1", "urllib3", "2.5.0", fixed=("2.6.0", "2.6.1"))],
        }
    )
    text = vulns.report(_check(tmp_path, grype))

    assert f"# Known vulnerabilities in {VERSION}" in text
    assert f"against its vulnerability database built {BUILT}" in text
    assert "**1 to act on**, 0 to watch, 0 accepted." in text
    # One row for the vulnerability of the package version, naming both documents it is in.
    rows = [line for line in text.splitlines() if line.startswith("| [CVE-2026-1]")]
    assert rows == [
        "| [CVE-2026-1](https://nvd.nist.gov/vuln/detail/CVE-2026-1) | high | urllib3 (python) | "
        "2.5.0 | 2.6.0, 2.6.1 | — | aira-gateway, aira-management |"
    ]


def test_the_source_s_findings_say_which_part_of_aira_they_are_about(tmp_path: Path) -> None:
    """The source document lists the development tools with what ships, the console's packages that
    its bundle hides, and the actions the CI runs."""
    grype = Grype(
        {
            "aira-source": [
                _match("CVE-2026-1", "pytest"),
                _match("CVE-2026-2", "urllib3"),
                _match("CVE-2026-3", "@angular/core", kind="npm"),
                _match("CVE-2026-4", "actions/checkout", kind="github-action"),
            ]
        }
    )
    rows = {
        identifier: line
        for line in vulns.report(_check(tmp_path, grype)).splitlines()
        for identifier in ("CVE-2026-1", "CVE-2026-2", "CVE-2026-3", "CVE-2026-4")
        if line.startswith(f"| [{identifier}]")
    }

    assert rows["CVE-2026-1"].endswith("| aira-source (development tools only) |")
    assert rows["CVE-2026-2"].endswith("| aira-source |"), "urllib3 ships in the images"
    assert rows["CVE-2026-3"].endswith("| aira-source (the console's bundle) |")
    assert rows["CVE-2026-4"].endswith("| aira-source (the CI workflow) |")


def test_a_table_cell_cannot_be_broken_out_of(tmp_path: Path) -> None:
    grype = Grype(
        {
            "aira-gateway": [
                _match("CVE-2026-1", "evil|name\nnext", link="https://x.example/a)(javascript:b"),
            ]
        }
    )
    text = vulns.report(_check(tmp_path, grype))
    row = next(line for line in text.splitlines() if "CVE-2026-1" in line and "evil" in line)

    assert "evil\\|name next" in row
    assert "](" not in row, "a link that could close the Markdown link is written as one"


def test_the_notes_carry_the_counts_and_what_to_act_on(tmp_path: Path) -> None:
    grype = Grype({"aira-gateway": [_match("CVE-2026-1", "urllib3")]})
    section = vulns.notes(_check(tmp_path, grype))

    assert section.startswith("## Known vulnerabilities\n")
    assert "**1 to act on**" in section and "| [CVE-2026-1]" in section
    assert f"attached as `aira-vulnerabilities-{VERSION}.md`" in section


# == the command =================================================================================


def _stand_in(tmp_path: Path, matches: dict[str, list[dict[str, Any]]], *, valid: bool = True):
    """Grype as a program, for the command: the stand-in above, answering on standard output."""
    data = tmp_path / "grype.json"
    data.write_text(json.dumps({"matches": matches, "valid": valid}))
    script = tmp_path / "grype-stand-in"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"data = json.loads(pathlib.Path({str(data)!r}).read_text())\n"
        "arguments = sys.argv[1:]\n"
        "if arguments[:2] == ['db', 'update']:\n"
        "    sys.exit(0 if data['valid'] else 1)\n"
        "if arguments[:2] == ['db', 'status']:\n"
        f"    print(json.dumps({{'built': {BUILT!r}, 'valid': data['valid']}}))\n"
        "elif arguments[0] == 'version':\n"
        "    print(json.dumps({'version': '0.121.3'}))\n"
        "else:\n"
        "    name = pathlib.Path(arguments[0][5:]).name.split('-v')[0]\n"
        "    print(json.dumps({'matches': data['matches'].get(name, [])}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _main(tmp_path: Path, grype: Path, *extra: str) -> int:
    return vulns.main(
        [
            VERSION,
            "--sboms",
            str(_release(tmp_path)),
            "--out",
            str(tmp_path / "out"),
            "--notes",
            str(tmp_path / "notes.md"),
            "--summary",
            str(tmp_path / "summary.md"),
            "--accepted",
            str(_accepted(tmp_path)),
            "--grype",
            str(grype),
            *extra,
        ]
    )


def test_the_night_fails_on_something_to_act_on_and_passes_without(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    acting = _stand_in(tmp_path, {"aira-gateway": [_match("CVE-2026-1", "urllib3")]})
    assert _main(tmp_path, acting) == vulns.ACT
    assert "::error title=Known vulnerabilities::1 to act on" in capsys.readouterr().out

    watching = _stand_in(tmp_path, {"aira-gateway": [_match("CVE-2026-1", "x", fixed=())]})
    assert _main(tmp_path, watching) == vulns.CLEAN


def test_a_release_reports_what_it_would_act_on_and_is_not_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grype = _stand_in(tmp_path, {"aira-gateway": [_match("CVE-2026-1", "urllib3")]})

    assert _main(tmp_path, grype, "--report-only") == vulns.CLEAN
    assert "::warning title=Known vulnerabilities::1 to act on" in capsys.readouterr().out
    assert "**1 to act on**" in (tmp_path / "notes.md").read_text()


def test_the_report_is_written_beside_its_own_checksum_and_into_the_summary(tmp_path: Path) -> None:
    grype = _stand_in(tmp_path, {})

    assert _main(tmp_path, grype) == vulns.CLEAN
    report = tmp_path / "out" / f"aira-vulnerabilities-{VERSION}.md"
    checksum = report.with_name(f"{report.name}.sha256").read_text()
    assert checksum == f"{hashlib.sha256(report.read_bytes()).hexdigest()}  {report.name}\n"
    assert (tmp_path / "summary.md").read_text() == report.read_text()
    assert "**0 to act on**" in report.read_text()


def test_a_check_not_made_says_so_everywhere_and_fails_the_night(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grype = _stand_in(tmp_path, {}, valid=False)

    assert _main(tmp_path, grype) == vulns.UNCHECKED
    assert "::error title=Known vulnerabilities not checked::" in capsys.readouterr().err
    for written in (
        tmp_path / "out" / f"aira-vulnerabilities-{VERSION}.md",
        tmp_path / "notes.md",
        tmp_path / "summary.md",
    ):
        text = written.read_text()
        assert "**Not checked**" in text, written.name
        assert "to act on" not in text, f"{written.name} reads like a check that was made"


def test_a_release_whose_check_could_not_be_made_is_not_refused_and_says_so(
    tmp_path: Path,
) -> None:
    grype = _stand_in(tmp_path, {}, valid=False)

    assert _main(tmp_path, grype, "--report-only") == vulns.CLEAN
    assert "**Not checked** when the release was made" in (tmp_path / "notes.md").read_text()


def test_a_name_that_is_not_a_release_version_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    status = vulns.main(
        [
            "v1.2.3/../../x",
            "--sboms",
            str(tmp_path),
            "--out",
            str(tmp_path / "out"),
            "--report-only",
        ]
    )

    assert status == vulns.UNCHECKED
    assert not (tmp_path / "out").exists()


def test_the_stand_in_answers_in_the_shape_the_command_reads(tmp_path: Path) -> None:
    """The command runs the stand-in as a program; the same answers read in-process agree."""
    grype = _stand_in(tmp_path, {"aira-gateway": [_match("CVE-2026-1", "urllib3")]})
    run = vulns._grype(str(grype))

    assert vulns.database(run) == BUILT
    assert vulns.scanner(run) == "0.121.3"
    assert os.access(grype, os.X_OK)
