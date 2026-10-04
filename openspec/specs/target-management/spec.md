# target-management Specification

## Purpose

Lets the user register the Instagram profiles whose followers are tracked, checking that each one exists, and list the registered profiles.

## Requirements

### Requirement: Add a target
`target add <username>` SHALL resolve the profile with exactly one profile request and store it as a target with its numeric Instagram ID and username. The command SHALL NOT check whether the profile is private or whether the research account follows it, because the profile page does not say. Making sure the research account follows a private profile before it is added and scanned is the user's responsibility. The command SHALL obey the cooldown, challenge hold and block-signal handling defined by request safety.

#### Scenario: Public profile
- **WHEN** the user runs `target add` with the username of an existing public profile
- **THEN** the tool stores the target with its numeric ID and reports that it was added

#### Scenario: Private profile
- **WHEN** the user runs `target add` with the username of an existing private profile
- **THEN** the tool stores the target like any other and makes no check of whether the research account follows it

#### Scenario: Profile does not exist
- **WHEN** no profile exists with the given username
- **THEN** the tool stores nothing and exits with a non-zero status and a clear error

#### Scenario: Refused during cooldown
- **WHEN** a cooldown or challenge hold is active
- **THEN** `target add` makes no request and reports when the cooldown ends or what is needed to lift the hold

### Requirement: Targets are unique by numeric ID
A target SHALL be identified by its numeric Instagram ID. Adding a profile that is already a target SHALL NOT create a second target.

#### Scenario: Same username added twice
- **WHEN** the user runs `target add` for a profile that is already a target under the same username
- **THEN** no new target is created and the tool reports that the target already exists

#### Scenario: Target has changed username
- **WHEN** the user runs `target add` with a new username whose numeric ID matches an existing target
- **THEN** no new target is created, the stored username is updated, and the tool reports the rename

### Requirement: List targets
`target list` SHALL show every target with its username, numeric ID, the date it was added, and the time and status of its most recent scan, if any. It SHALL NOT make any Instagram request.

#### Scenario: Listing targets
- **WHEN** the user runs `target list` with two targets stored
- **THEN** both targets are shown with their details, and no Instagram request is made

#### Scenario: No targets
- **WHEN** the user runs `target list` and no targets are stored
- **THEN** the tool reports that there are no targets

### Requirement: Targets cannot be removed
The tool SHALL NOT provide any command that removes a target or its recorded followers.

#### Scenario: Attempting removal
- **WHEN** the user looks for a way to remove a target through the CLI
- **THEN** no such command exists, and the target and its data remain stored
