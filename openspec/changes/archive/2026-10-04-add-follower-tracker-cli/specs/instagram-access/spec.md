# Spec Delta

## Purpose

Gives the tool authenticated, read-only access to Instagram through a dedicated research account, using the research account's web session imported from Firefox, and never logging in without the user.

## ADDED Requirements

### Requirement: Session import from Firefox
`session import` SHALL import the research account's existing Instagram web session from the local Firefox profile and save it as the tool's session. The tool SHALL never ask for or use a password.

#### Scenario: Successful import
- **WHEN** Firefox holds a logged-in Instagram session and the user runs `session import`
- **THEN** the tool saves the session and reports the username of the account it belongs to

#### Scenario: No Instagram session in Firefox
- **WHEN** Firefox holds no logged-in Instagram session
- **THEN** `session import` exits with a non-zero status, tells the user to log in to Instagram in Firefox with the research account, and saves nothing

#### Scenario: Re-import replaces the session
- **WHEN** a session already exists and the user runs `session import` again
- **THEN** the new session replaces the old one

### Requirement: No saved session
A command that talks to Instagram SHALL check that a saved session exists before making any request.

#### Scenario: Session never imported
- **WHEN** no session has been imported and the user runs a scan
- **THEN** the tool exits with a non-zero status and tells the user to run `session import`, without making any Instagram request

### Requirement: No automatic login
The tool SHALL NOT log in by itself under any circumstances. When the saved session is missing, expired or rejected, the command SHALL stop and tell the user to log in to Instagram in Firefox and run `session import` again.

#### Scenario: Session rejected mid-scan
- **WHEN** Instagram rejects the saved session during a scan
- **THEN** the scan stops without attempting to log in, and the tool tells the user how to restore the session

### Requirement: Session check
`session check` SHALL make exactly one lightweight authenticated request with the saved session and report whether the session works and which account it belongs to. `session check` SHALL be allowed while a cooldown or challenge hold is active.

#### Scenario: Working session
- **WHEN** the user runs `session check` and the saved session is accepted
- **THEN** the tool reports success and the account username, having made one request

#### Scenario: Rejected session
- **WHEN** the user runs `session check` and Instagram rejects the session
- **THEN** the tool reports failure with guidance on restoring the session and exits with a non-zero status

### Requirement: Session file protection
The saved session file SHALL be stored at a configurable path outside version control and SHALL be readable and writable only by the user who owns it.

#### Scenario: New session file
- **WHEN** the tool saves the session file
- **THEN** the file's permissions allow access only by its owner
