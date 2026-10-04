# follower-records Specification

## Purpose

Defines what the tool keeps in its local database about followers, follow relationships and scans, and guarantees that the user's own tags and marks are never overwritten or lost.

## Requirements

### Requirement: Persons keyed by numeric ID
Each follower SHALL be stored once as a person in the `persons` table, identified by numeric Instagram ID. A person SHALL store the fields the follower list returns (username, full name, and the private and verified flags when provided) and SHALL NOT store profile picture URLs.

#### Scenario: Follower of two targets
- **WHEN** the same person follows two targets and both are scanned
- **THEN** there is exactly one stored person for them

#### Scenario: Profile picture URL in page data
- **WHEN** the follower list returns a profile picture URL for a follower
- **THEN** the URL is not stored anywhere

### Requirement: Latest values only
When a known person is seen again, the tool SHALL overwrite their stored fields with the latest values. The previous username SHALL NOT be kept. A field that the follower list does not return for a person SHALL keep its stored value.

#### Scenario: Username change
- **WHEN** a person stored as `old_name` appears in a later scan as `new_name`
- **THEN** the stored person has the username `new_name`, and `old_name` is not kept

#### Scenario: Field missing from the follower list
- **WHEN** the follower list does not include the verified flag for a person who already has one stored
- **THEN** the stored verified flag is unchanged

#### Scenario: Full name missing or empty in the follower list
- **WHEN** the follower list does not include a full name, or returns an empty one, for a person who already has one stored
- **THEN** the stored full name is unchanged

### Requirement: Follow relationships with first-seen and last-seen
For each person and target pair, the tool SHALL store one follow relationship recording the scan and time it was first seen and the scan and time it was last seen. First-seen SHALL be set once and never changed. Last-seen SHALL be updated each time the person appears in a scan of that target and is informational only.

#### Scenario: Seen in two scans
- **WHEN** a person appears in two scans of the same target
- **THEN** there is one follow relationship whose first-seen refers to the first scan and whose last-seen refers to the second

### Requirement: Unfollows and refollows are not tracked
The tool SHALL NOT remove or change a follow relationship when a person is missing from a later scan, and SHALL NOT record refollows.

#### Scenario: Person missing from a full scan
- **WHEN** a `--full` scan completes and a previously recorded follower is not in the list
- **THEN** their follow relationship stays as it was, with first-seen and last-seen unchanged

### Requirement: Scans are recorded
Every scan SHALL be stored with its target, mode, whether it is the baseline, start and end times, status, stop reason, pages fetched, followers seen, saved cursor, and any signal type and raw message.

#### Scenario: Inspecting an interrupted scan
- **WHEN** a scan stops because of a block signal
- **THEN** its stored record shows it as unfinished, with the signal type, Instagram's raw message, and the saved cursor

### Requirement: User-owned tags and marks
The `persons` table SHALL include a `tags` column holding any number of free-form text tags (empty by default) and an `is_marked` yes/no column (false by default). These columns belong to the user. The tool SHALL NOT write to them, and updating a person during a scan SHALL leave them unchanged.

#### Scenario: Tagged person seen again
- **WHEN** the user has tagged and marked a person in their SQL client and that person appears in a later scan
- **THEN** the person's tags and mark are unchanged after the scan

#### Scenario: New person
- **WHEN** the tool stores a person for the first time
- **THEN** the person has no tags and is not marked

### Requirement: Tagged or marked persons cannot be deleted
The database SHALL reject any `DELETE` of a person who has at least one tag or is marked. The tool SHALL contain no operation that deletes a person.

#### Scenario: Deleting a tagged person
- **WHEN** the user runs a `DELETE` on a person who has a tag
- **THEN** the database rejects the statement and the person remains

#### Scenario: Deleting a marked person
- **WHEN** the user runs a `DELETE` on a person whose `is_marked` is true
- **THEN** the database rejects the statement and the person remains

#### Scenario: Deleting an untagged, unmarked person
- **WHEN** the user runs a `DELETE` on a person with no tags who is not marked
- **THEN** the database guard does not block the statement

### Requirement: Versioned schema migrations
The database schema SHALL be created and upgraded only through versioned migrations. A migration SHALL NOT drop or overwrite existing tags or marks.

#### Scenario: Fresh database
- **WHEN** the user applies all migrations to an empty database
- **THEN** the schema, including the delete guard on persons, is created

#### Scenario: Upgrade with existing annotations
- **WHEN** the user applies a new migration to a database with tagged and marked persons
- **THEN** all tags and marks are preserved
