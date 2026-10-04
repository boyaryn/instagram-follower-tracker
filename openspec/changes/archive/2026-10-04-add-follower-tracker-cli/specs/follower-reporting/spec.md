# Spec Delta

## Purpose

Lets the user see a target's recorded followers and the followers first seen since a scan or date, as a terminal table, CSV or JSON, with the user's own tags and marks alongside.

## ADDED Requirements

### Requirement: Reporting works offline
The `list` and `first-seen` commands SHALL read only from the database and SHALL NOT make any Instagram request. They SHALL work while a cooldown or challenge hold is active.

#### Scenario: Report during cooldown
- **WHEN** a cooldown is active and the user runs `list`
- **THEN** the report is produced and no Instagram request is made

### Requirement: List all followers
`list <username>` SHALL show every person with a follow relationship to the target.

#### Scenario: Listing a scanned target
- **WHEN** the user runs `list` for a target with 120 recorded followers
- **THEN** the output contains 120 rows, one per follower

#### Scenario: Unknown target
- **WHEN** the user runs `list` for a username that is not a target
- **THEN** the tool exits with a non-zero status and a clear error

### Requirement: Followers first seen
`first-seen <username>` SHALL show the followers first seen in the target's most recent scan. `first-seen <username> --since <date>` SHALL show followers first seen at or after that date. `first-seen <username> --since-scan <scan-id>` SHALL show followers first seen in any scan of that target after the given scan. Only one of `--since` and `--since-scan` SHALL be accepted at a time.

#### Scenario: Default since previous scan
- **WHEN** the target's most recent scan first saw 3 followers
- **THEN** `first-seen` shows those 3 followers

#### Scenario: Since a date
- **WHEN** the user runs `first-seen --since 2026-09-01`
- **THEN** the output contains exactly the followers of that target first seen on or after 1 September 2026, excluding baseline followers

#### Scenario: Since a scan
- **WHEN** the user runs `first-seen --since-scan` with the ID of an earlier scan of the target
- **THEN** the output contains the followers first seen in later scans of that target

#### Scenario: Scan belongs to another target
- **WHEN** the given scan ID belongs to a different target
- **THEN** the tool exits with a non-zero status and a clear error

#### Scenario: Most recent scan unfinished
- **WHEN** the target's most recent scan is unfinished
- **THEN** `first-seen` shows the followers first seen in it so far and states that the scan is not complete

### Requirement: What counts as first seen
"First seen" SHALL mean the scan in which the tool first recorded the person as a follower of the target. The tool SHALL NOT claim when a person started following: a follower first seen in a later scan may have followed recently, or may have been missed by earlier scans because Instagram's list can reorder between page requests. A follower first seen in the target's baseline scan SHALL never be reported by `first-seen`. A person SHALL be reported as first seen at most once per target, in the scan where they were first seen for that target.

#### Scenario: Only a baseline exists
- **WHEN** the target's only scan is its baseline and the user runs `first-seen`
- **THEN** no followers are shown, and the tool states that the latest scan was the baseline

#### Scenario: Person seen again later
- **WHEN** a person was first seen in scan 2 and appears again in scan 3
- **THEN** `first-seen` after scan 3 does not show them

#### Scenario: Follower missed by earlier scans
- **WHEN** an existing follower of the target was not returned by any earlier scan and appears in scan 4
- **THEN** `first-seen` after scan 4 shows them like any other follower first seen in that scan

#### Scenario: Person first seen for a second target
- **WHEN** a person already follows target A and is first seen following target B in a non-baseline scan
- **THEN** they are reported as first seen for target B

### Requirement: Row contents
Each output row SHALL include the person's numeric Instagram ID, username, full name, first-seen time for the target, tags and mark.

#### Scenario: Tagged follower in a report
- **WHEN** a listed follower has the tags `lab` and `pilot` and is marked
- **THEN** their row shows both tags and shows them as marked

### Requirement: Output formats
Reports SHALL be printed to standard output as a terminal table by default. `--format csv` SHALL print CSV with a header row, and `--format json` SHALL print a JSON array of objects, with tags as a list and the mark as a boolean. An empty result SHALL produce a message in table format, a header-only CSV, or an empty JSON array.

#### Scenario: CSV output
- **WHEN** the user runs `list --format csv`
- **THEN** the output is valid CSV with one header row and one row per follower

#### Scenario: JSON output
- **WHEN** the user runs `first-seen --format json`
- **THEN** the output is a valid JSON array with one object per follower first seen

#### Scenario: Empty JSON result
- **WHEN** `first-seen --format json` finds no followers
- **THEN** the output is `[]`

#### Scenario: Unknown format
- **WHEN** the user passes `--format xml`
- **THEN** the tool exits with a non-zero status and lists the valid formats
