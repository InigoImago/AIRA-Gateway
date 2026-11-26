"""Check what a release contains for known vulnerabilities, against the data of the day it runs.

A release attaches one CycloneDX document per image and one of the source (`release_sbom.py`). This
reads each with Grype, against the vulnerability database **as it is on the day it runs**: once when
the release is made, so the release says what was known about it then, and **every night after**
for the newest release. A vulnerability is almost always published after the code that has it, so
an inventory that was clean when it shipped has to be read again every day.

Every finding is in one of four groups:

- **to act on**: an update removes it, because it is critical or high and a fixed version exists,
  or somebody is already using it, because CISA lists it as exploited, whatever its severity. The
  nightly check fails on any, which is what makes GitHub send its mail.
- **to watch**: critical or high with no fixed version yet. Listed, without an alarm: there is
  nothing to update to.
- **accepted**: named in `.github/accepted-vulnerabilities.toml` with a reason and a date. After
  that date it is back in its group.
- **the rest**: medium and below, counted.

**A check that did not run is never a clean report.** If Grype fails, the database cannot be
fetched, a document is missing or a checksum does not match, the report says *not checked* and
why, and the check exits with status 2. A release is not stopped by it (`--report-only`): its notes
say it was not checked, and the night's check says so again, with a failure.

    python tools/release_vulnerabilities.py v0.1.16 --sboms dist/ --out report/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from release_sbom import SOURCE_NAME
from release_version import parse

ROOT = Path(__file__).resolve().parents[1]

#: The vulnerabilities somebody accepted for a while, each with its reason, reviewed like code.
ACCEPTED = ROOT / ".github" / "accepted-vulnerabilities.toml"

#: Grype's severities, most severe first. A severity Grype writes that is not one of these counts
#: as `unknown`.
SEVERITIES = ("critical", "high", "medium", "low", "negligible", "unknown")

#: The severities that are acted on when fixable and watched when not.
SERIOUS = frozenset({"critical", "high"})

#: How far ahead of the day the check runs an acceptance may reach. A risk accepted for longer is
#: one nobody looks at again.
LONGEST_ACCEPTANCE = timedelta(days=365)

#: Exit statuses: nothing to act on; something to act on; the check could not be made.
CLEAN, ACT, UNCHECKED = 0, 1, 2

#: How Grype runs. The database is fetched once, before the first document, so every document is
#: read against the same data, and Grype does not ask the network whether a newer Grype exists.
GRYPE_ENVIRONMENT = {"GRYPE_DB_AUTO_UPDATE": "false", "GRYPE_CHECK_FOR_APP_UPDATE": "false"}

#: A document a release attaches: `aira-<word>-vX.Y.Z.cdx.json` (`release_sbom.name`).
_DOCUMENT = re.compile(r"(?P<name>aira-[a-z]+)-(?P<version>v\d+\.\d+\.\d+)\.cdx\.json")
_NAME = re.compile(r"aira-[a-z]+")

#: The only links the report writes: a plain https address, which Markdown cannot be broken out of.
_LINK = re.compile(r"https://[^\s()<>\[\]|]+")

_ACCEPTANCE_KEYS = frozenset({"vulnerability", "package", "documents", "reason", "until"})

Run = Callable[[list[str]], str]


class Unchecked(Exception):
    """The check could not be made. Says why, because "no report" sends a reader everywhere."""


@dataclass(frozen=True)
class Finding:
    """One vulnerability of one package in one document."""

    document: str
    package: str
    version: str
    kind: str
    vulnerability: str
    aliases: frozenset[str]
    severity: str
    fix_state: str
    fixed_in: tuple[str, ...]
    exploited: bool
    link: str

    @property
    def fixable(self) -> bool:
        return self.fix_state == "fixed" and bool(self.fixed_in)

    @property
    def group(self) -> str:
        """`act`, `watch` or `other`, before any acceptance."""
        if self.exploited or (self.severity in SERIOUS and self.fixable):
            return "act"
        if self.severity in SERIOUS:
            return "watch"
        return "other"


@dataclass(frozen=True)
class Acceptance:
    """A vulnerability somebody decided to live with until a date, and why."""

    vulnerability: str
    package: str
    reason: str
    until: date
    #: The documents it holds for; empty for every document.
    documents: frozenset[str] = frozenset()

    def covers(self, finding: Finding) -> bool:
        return (
            self.vulnerability in {finding.vulnerability, *finding.aliases}
            and self.package == finding.package
            and (not self.documents or finding.document in self.documents)
        )

    def describe(self) -> str:
        scope = f" in {', '.join(sorted(self.documents))}" if self.documents else ""
        return f"{self.vulnerability} in {self.package}{scope}"


@dataclass
class Result:
    """What one check found, grouped."""

    version: str
    checked: date
    database: str = ""
    scanner: str = ""
    documents: list[str] = field(default_factory=list)
    act: list[Finding] = field(default_factory=list)
    watch: list[Finding] = field(default_factory=list)
    accepted: list[tuple[Finding, Acceptance]] = field(default_factory=list)
    other: list[Finding] = field(default_factory=list)
    #: Acceptances that ran out while what they accepted is still found.
    expired: list[Acceptance] = field(default_factory=list)
    #: Acceptances that match nothing any more: the entry can go.
    unused: list[Acceptance] = field(default_factory=list)
    #: The Python packages the images ship, to tell a development tool in the source from one of
    #: their dependencies.
    shipped: set[str] = field(default_factory=set)

    @property
    def status(self) -> int:
        return ACT if self.act else CLEAN


def report_name(version: str) -> str:
    """The report's file name on the Release, beside the documents it reads."""
    return f"aira-vulnerabilities-{version}.md"


# == reading ======================================================================================


def acceptances(path: Path, today: date) -> list[Acceptance]:
    """Every entry of the acceptance file, refusing one that is not a decision a reader can check:
    a missing reason, a date that is not one or reaches more than a year ahead, a key nobody reads.
    A misspelt `untill` is refused rather than ignored, so it does not accept for ever."""
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as problem:
        raise Unchecked(f"{path.name}: {problem}") from None
    if unknown := sorted(set(data) - {"accepted"}):
        raise Unchecked(f"{path.name}: unknown keys {unknown}; entries are [[accepted]] tables")
    entries = data.get("accepted", [])
    if not isinstance(entries, list):
        raise Unchecked(f"{path.name}: `accepted` is a list of [[accepted]] tables")
    found: list[Acceptance] = []
    for number, entry in enumerate(entries, start=1):
        where = f"{path.name}, entry {number}"
        if not isinstance(entry, dict):
            raise Unchecked(f"{where}: an entry is an [[accepted]] table")
        if unknown := sorted(set(entry) - _ACCEPTANCE_KEYS):
            raise Unchecked(f"{where}: unknown keys {unknown}")
        for key in ("vulnerability", "package", "reason"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise Unchecked(f"{where}: `{key}` is required and says something")
        until = entry.get("until")
        if not isinstance(until, date) or isinstance(until, datetime):
            raise Unchecked(f"{where}: `until` is a date, written like 2027-01-31")
        if until - today > LONGEST_ACCEPTANCE:
            raise Unchecked(
                f"{where}: accepted until {until}, more than a year after {today}; accept it for "
                "less and look at it again then"
            )
        documents = entry.get("documents", [])
        if not isinstance(documents, list) or not all(
            isinstance(name, str) and _NAME.fullmatch(name) for name in documents
        ):
            raise Unchecked(f'{where}: `documents` names documents, like ["aira-gateway"]')
        found.append(
            Acceptance(
                vulnerability=entry["vulnerability"].strip(),
                package=entry["package"].strip(),
                reason=" ".join(entry["reason"].split()),
                until=until,
                documents=frozenset(documents),
            )
        )
    return found


def _verified(path: Path) -> None:
    """Refuse a document that is not the file its checksum was written for."""
    checksum = path.with_name(f"{path.name}.sha256")
    try:
        recorded = checksum.read_text().split()
    except OSError:
        raise Unchecked(f"{path.name} has no {checksum.name} beside it") from None
    if len(recorded) != 2 or recorded[1].lstrip("*") != path.name:
        raise Unchecked(f"{checksum.name} is not a checksum of {path.name}")
    if recorded[0] != hashlib.sha256(path.read_bytes()).hexdigest():
        raise Unchecked(f"{path.name} is not the file its checksum was written for")


def documents(directory: Path, version: str) -> dict[str, Path]:
    """The release's documents, each beside a checksum that matches it, the source among them.

    Without the source document the console's npm packages, compiled into its bundle, would be read
    nowhere; without an image's document, nothing the release runs would be."""
    found: dict[str, Path] = {}
    for path in sorted(directory.glob("*.cdx.json")):
        named = _DOCUMENT.fullmatch(path.name)
        if named is None or named["version"] != version:
            raise Unchecked(f"{path.name} is not a document of {version}")
        _verified(path)
        found[named["name"]] = path
    if SOURCE_NAME not in found:
        raise Unchecked(f"no {SOURCE_NAME}-{version}.cdx.json in {directory}")
    if len(found) < 2:
        raise Unchecked(f"no image's document in {directory}")
    return found


def _normal(name: str) -> str:
    """A Python package's name as PyPI compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def shipped_python(document: Path) -> set[str]:
    """The Python packages an image's document lists, by normalised name."""
    try:
        components = json.loads(document.read_text()).get("components") or []
    except (OSError, ValueError, AttributeError) as problem:
        raise Unchecked(f"{document.name}: not a CycloneDX document ({problem})") from None
    return {
        _normal(str(component.get("name", "")))
        for component in components
        if str(component.get("purl", "")).startswith("pkg:pypi/")
    }


def findings(document: str, output: str) -> set[Finding]:
    """Grype's JSON report for one document, as findings. A report that is not one is refused, not
    read as empty: an empty list of matches is the one answer that must come from a real scan."""
    try:
        matches = json.loads(output)["matches"]
        if not isinstance(matches, list):
            raise TypeError("`matches` is not a list")
        return {_finding(document, match) for match in matches}
    except (ValueError, KeyError, TypeError, AttributeError) as problem:
        raise Unchecked(f"{document}: Grype's report could not be read ({problem!r})") from None


def _finding(document: str, match: Any) -> Finding:
    vulnerability, artifact = match["vulnerability"], match["artifact"]
    related = list(match.get("relatedVulnerabilities") or [])
    fix = vulnerability.get("fix") or {}
    severity = str(vulnerability.get("severity") or "unknown").lower()
    link = str(vulnerability.get("dataSource") or "")
    identifier = str(vulnerability["id"])
    return Finding(
        document=document,
        package=str(artifact["name"]),
        version=str(artifact["version"]),
        kind=str(artifact.get("type") or ""),
        vulnerability=identifier,
        aliases=frozenset(str(other["id"]) for other in related) - {identifier},
        severity=severity if severity in SEVERITIES else "unknown",
        fix_state=str(fix.get("state") or "unknown"),
        fixed_in=tuple(str(version) for version in fix.get("versions") or ()),
        # CISA's catalogue, on the vulnerability itself or on an alias Grype related it to.
        exploited=any(bool(entry.get("knownExploited")) for entry in [vulnerability, *related]),
        link=link if _LINK.fullmatch(link) else "",
    )


# == deciding =====================================================================================


def grouped(
    version: str,
    found: Iterable[Finding],
    accepted: list[Acceptance],
    *,
    today: date,
    database: str = "",
    scanner: str = "",
    documents: Iterable[str] = (),
    shipped: Iterable[str] = (),
) -> Result:
    """Each finding in its group. An acceptance holds while its date has not passed: on the day
    itself it still holds, the day after it does not."""
    result = Result(
        version=version,
        checked=today,
        database=database,
        scanner=scanner,
        documents=sorted(documents),
        shipped=set(shipped),
    )
    used: set[Acceptance] = set()
    expired: set[Acceptance] = set()
    for finding in sorted(found, key=_order):
        covering = [acceptance for acceptance in accepted if acceptance.covers(finding)]
        used.update(covering)
        holding = [acceptance for acceptance in covering if acceptance.until >= today]
        expired.update(acceptance for acceptance in covering if acceptance.until < today)
        if holding and finding.group != "other":
            result.accepted.append((finding, holding[0]))
        elif finding.group == "act":
            result.act.append(finding)
        elif finding.group == "watch":
            result.watch.append(finding)
        else:
            result.other.append(finding)
    result.expired = sorted(expired, key=Acceptance.describe)
    result.unused = sorted(set(accepted) - used, key=Acceptance.describe)
    return result


def _order(finding: Finding) -> tuple[int, str, str, str, str]:
    return (
        SEVERITIES.index(finding.severity),
        finding.vulnerability,
        finding.package,
        finding.version,
        finding.document,
    )


# == writing ======================================================================================


def _cell(text: str) -> str:
    """Text a table cell can hold: one line, no column separator."""
    return " ".join(text.split()).replace("|", "\\|") or "—"


def _vulnerability(finding: Finding) -> str:
    name = _cell(finding.vulnerability)
    return f"[{name}]({finding.link})" if finding.link else name


def _where(finding: Finding, shipped: set[str]) -> str:
    """Which part of AIRA a finding in the source document is about: the source lists more than the
    images run, and a reader should not have to know which."""
    if finding.document != SOURCE_NAME:
        return finding.document
    if finding.kind == "npm":
        return f"{SOURCE_NAME} (the console's bundle)"
    if finding.kind.startswith("github-action"):
        return f"{SOURCE_NAME} (the CI workflow)"
    if finding.kind == "python" and _normal(finding.package) not in shipped:
        return f"{SOURCE_NAME} (development tools only)"
    return finding.document


def _rows(findings: Iterable[Finding], shipped: set[str]) -> list[tuple[Finding, str]]:
    """One row per vulnerability of one package version, naming every document it is in."""
    rows: dict[tuple[str, str, str], tuple[Finding, list[str]]] = {}
    for finding in findings:
        key = (finding.vulnerability, finding.package, finding.version)
        _, where = rows.setdefault(key, (finding, []))
        where.append(_where(finding, shipped))
    return [(first, ", ".join(dict.fromkeys(where))) for first, where in rows.values()]


def _table(findings: list[Finding], shipped: set[str], *, watched: bool) -> list[str]:
    lines = [
        f"| Vulnerability | Severity | Package | Installed | {'Fix' if watched else 'Fixed in'} "
        "| Exploited | Found in |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for finding, where in _rows(findings, shipped):
        fix = finding.fix_state if watched else ", ".join(finding.fixed_in) or finding.fix_state
        lines.append(
            f"| {_vulnerability(finding)} | {finding.severity} | {_cell(finding.package)} "
            f"({_cell(finding.kind)}) | {_cell(finding.version)} | {_cell(fix)} | "
            f"{'yes' if finding.exploited else '—'} | {_cell(where)} |"
        )
    return lines


def _counts(result: Result) -> list[str]:
    everything = [*result.act, *result.watch, *(f for f, _ in result.accepted), *result.other]
    lines = [
        "| Document | Critical | High | Medium | Low | Other | To act on | To watch | Accepted |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for document in result.documents:
        severity = Counter(f.severity for f in everything if f.document == document)
        other = severity["negligible"] + severity["unknown"]
        lines.append(
            f"| {document} | {severity['critical']} | {severity['high']} | {severity['medium']} | "
            f"{severity['low']} | {other} | "
            f"{sum(f.document == document for f in result.act)} | "
            f"{sum(f.document == document for f in result.watch)} | "
            f"{sum(f.document == document for f, _ in result.accepted)} |"
        )
    return lines


def _when(result: Result) -> str:
    return (
        f"Checked {result.checked.isoformat()} with Grype {result.scanner}, against its "
        f"vulnerability database built {result.database}."
    )


def to_act_on(result: Result) -> int:
    """How many vulnerabilities of a package version are to be acted on, across documents."""
    return len(_rows(result.act, result.shipped))


def _headline(result: Result) -> str:
    accepted = len(_rows((finding for finding, _ in result.accepted), result.shipped))
    return (
        f"**{to_act_on(result)} to act on**, {len(_rows(result.watch, result.shipped))} to watch, "
        f"{accepted} accepted."
    )


def report(result: Result) -> str:
    """The whole report, for the Release and for the night's run."""
    lines = [
        f"# Known vulnerabilities in {result.version}",
        "",
        _when(result),
        "",
        _headline(result),
        "",
        *_counts(result),
        "",
        f"## To act on — {to_act_on(result)}",
        "",
        "Critical or high with a fixed version, or listed by CISA as exploited: an update to the "
        "fixed version removes it, and the release that carries the update is the answer.",
        "",
        *(_table(result.act, result.shipped, watched=False) if result.act else ["None."]),
        "",
        f"## To watch — {len(_rows(result.watch, result.shipped))}",
        "",
        "Critical or high, with no fixed version yet: nothing to update to. Each moves to *to act "
        "on* on the night a fix is published.",
        "",
        *(_table(result.watch, result.shipped, watched=True) if result.watch else ["None."]),
        "",
        f"## Accepted — {len(_rows((f for f, _ in result.accepted), result.shipped))}",
        "",
    ]
    if result.accepted:
        until = dict(result.accepted)
        lines += [
            "| Vulnerability | Severity | Package | Found in | Until | Reason |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for finding, where in _rows(until, result.shipped):
            acceptance = until[finding]
            lines.append(
                f"| {_vulnerability(finding)} | {finding.severity} | {_cell(finding.package)} | "
                f"{_cell(where)} | {acceptance.until.isoformat()} | {_cell(acceptance.reason)} |"
            )
    else:
        lines.append("None.")
    if result.expired:
        lines += ["", "## Acceptances that ran out", ""]
        lines += [
            f"- {_cell(a.describe())}, accepted until {a.until.isoformat()}: counted again above."
            for a in result.expired
        ]
    if result.unused:
        lines += ["", "## Acceptances that match nothing", ""]
        lines += [
            f"- {_cell(a.describe())}: no longer found, so the entry can go." for a in result.unused
        ]
    lines += [
        "",
        f"Medium and below: {len(result.other)} findings, counted in the table and not listed; "
        "`grype sbom:<document>` lists them.",
    ]
    return "\n".join(lines) + "\n"


def notes(result: Result) -> str:
    """The section the release notes carry: the headline, the counts, and what to act on."""
    lines = [
        "## Known vulnerabilities",
        "",
        f"{_when(result)} {_headline(result)}",
        "",
        *_counts(result),
    ]
    if result.act:
        lines += ["", *_table(result.act, result.shipped, watched=False)]
    lines += [
        "",
        f"The full list is attached as `{report_name(result.version)}`. The newest release is "
        "checked again every night, against that night's data.",
    ]
    return "\n".join(lines) + "\n"


def unchecked_report(version: str, reason: str, today: date) -> str:
    """What is written in place of a report when the check could not be made: never an empty list
    of findings, which would read as a clean release."""
    return (
        f"# Known vulnerabilities in {version}\n\n"
        f"**Not checked** on {today.isoformat()}: {' '.join(reason.split())}\n\n"
        "Nothing here says the release is free of known vulnerabilities. The newest release is "
        "checked again every night.\n"
    )


def unchecked_notes(version: str, reason: str, today: date) -> str:
    return (
        "## Known vulnerabilities\n\n"
        f"**Not checked** when the release was made ({today.isoformat()}): "
        f"{' '.join(reason.split())} The newest release is checked again every night.\n"
    )


# == running ======================================================================================


def _grype(command: str) -> Run:
    def run(arguments: list[str]) -> str:
        try:
            return subprocess.run(  # noqa: S603  — fixed argument list, no shell
                [command, *arguments],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, **GRYPE_ENVIRONMENT},
            ).stdout
        except (OSError, subprocess.CalledProcessError) as problem:
            said = " ".join(str(getattr(problem, "stderr", "") or "").split())
            raise Unchecked(f"grype {' '.join(arguments)} failed ({problem}) {said}") from None

    return run


def database(run: Run) -> str:
    """Fetch today's database and say when it was built. A database Grype calls invalid is refused,
    whatever `db update` answered."""
    run(["db", "update"])
    try:
        status = json.loads(run(["db", "status", "--output", "json"]))
    except ValueError as problem:
        raise Unchecked(f"grype db status wrote no JSON ({problem})") from None
    if status.get("valid") is not True or not status.get("built"):
        raise Unchecked(f"Grype's database is not usable: {status.get('error') or status}")
    return str(status["built"])


def scanner(run: Run) -> str:
    try:
        return str(json.loads(run(["version", "--output", "json"]))["version"])
    except (ValueError, KeyError, TypeError) as problem:
        raise Unchecked(f"grype version could not be read ({problem!r})") from None


def check(version: str, sboms: Path, *, accepted: Path, run: Run, today: date) -> Result:
    """Every document of `version` in `sboms`, read by Grype, each finding in its group."""
    if parse(version) is None:
        raise Unchecked(f"'{version}' is not a release version (vX.Y.Z)")
    entries = acceptances(accepted, today)
    found_documents = documents(sboms, version)
    built = database(run)
    used = scanner(run)
    found: set[Finding] = set()
    shipped: set[str] = set()
    for name, path in found_documents.items():
        found |= findings(name, run([f"sbom:{path}", "--by-cve", "--output", "json", "--quiet"]))
        if name != SOURCE_NAME:
            shipped |= shipped_python(path)
    return grouped(
        version,
        found,
        entries,
        today=today,
        database=built,
        scanner=used,
        documents=found_documents,
        shipped=shipped,
    )


def _written(out: Path, version: str, text: str) -> list[Path]:
    """The report and its checksum, in `sha256sum` format like the documents beside it."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / report_name(version)
    path.write_text(text)
    checksum = path.with_name(f"{path.name}.sha256")
    checksum.write_text(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    return [path, checksum]


def _annotation(level: str, title: str, message: str) -> str:
    """A GitHub workflow command, escaped so a message of several lines stays one annotation."""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{level} title={title}::{escaped}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the release, e.g. v0.1.16")
    parser.add_argument("--sboms", type=Path, required=True, help="the directory of its documents")
    parser.add_argument("--out", type=Path, required=True, help="where the report is written")
    parser.add_argument("--notes", type=Path, help="where the release notes' section is written")
    parser.add_argument("--summary", type=Path, help="a file the report is appended to")
    parser.add_argument("--accepted", type=Path, default=ACCEPTED, help="the accepted ones")
    parser.add_argument("--grype", default="grype", help="the Grype command to run")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="exit 0 whatever was found or left unchecked: a release reports, it is not refused",
    )
    arguments = parser.parse_args(argv)
    # Before anything is written: the version is part of the report's file name.
    if parse(arguments.version) is None:
        print(f"'{arguments.version}' is not a release version (vX.Y.Z)", file=sys.stderr)
        return UNCHECKED
    today = datetime.now(UTC).date()
    try:
        result = check(
            arguments.version,
            arguments.sboms,
            accepted=arguments.accepted,
            run=_grype(arguments.grype),
            today=today,
        )
    except Exception as problem:  # noqa: BLE001  — every failure is a check that was not made
        reason = str(problem) if isinstance(problem, Unchecked) else f"{problem!r}"
        print(_annotation("error", "Known vulnerabilities not checked", reason), file=sys.stderr)
        text = unchecked_report(arguments.version, reason, today)
        section = unchecked_notes(arguments.version, reason, today)
        status = UNCHECKED
    else:
        text, section, status = report(result), notes(result), result.status
        if result.act:
            print(
                _annotation(
                    "warning" if arguments.report_only else "error",
                    "Known vulnerabilities",
                    f"{to_act_on(result)} to act on in {result.version}; "
                    f"see {report_name(result.version)}",
                )
            )
    for path in _written(arguments.out, arguments.version, text):
        print(path)
    if arguments.notes is not None:
        arguments.notes.parent.mkdir(parents=True, exist_ok=True)
        arguments.notes.write_text(section)
    if arguments.summary is not None:
        with arguments.summary.open("a") as summary:
            summary.write(text)
    return CLEAN if arguments.report_only else status


if __name__ == "__main__":
    raise SystemExit(main())
