# request-safety Specification

## Purpose

Keeps the research account's Instagram traffic slow, bounded and quick to back off, so that block and challenge signals stop work at once and lead to a recorded cooldown or hold with clear next steps.

## Requirements

### Requirement: Randomised delay between pages
Between consecutive follower-page requests within a run, the tool SHALL wait a random time between 15 and 45 seconds. The minimum and maximum SHALL be configurable.

#### Scenario: Consecutive pages
- **WHEN** a scan fetches one page and then the next
- **THEN** the time between the two requests is at least the configured minimum and at most the configured maximum

#### Scenario: Invalid delay configuration
- **WHEN** the configured minimum delay is greater than the maximum, or either is negative
- **THEN** the tool exits with a configuration error before making any Instagram request

### Requirement: Page cap per run
A single `scan` or `resume` run SHALL fetch at most 300 pages. The cap SHALL be configurable. When the cap is reached, the scan SHALL stop, stay unfinished with its cursor saved, and the tool SHALL tell the user to resume it later.

#### Scenario: Cap reached
- **WHEN** a run fetches its 300th page and the follower list continues
- **THEN** the run stops without fetching another page, and the scan can be continued with `resume`

### Requirement: Stop immediately on a block signal
When any Instagram request returns a block or challenge signal, the tool SHALL stop the run at once and SHALL NOT retry the request. Pages saved before the signal SHALL be kept, and the scan SHALL stay resumable.

#### Scenario: Signal mid-scan
- **WHEN** Instagram returns a rate-limit response on the tenth page of a scan
- **THEN** no further request is made in that run, including no retry of the tenth page, and pages one to nine remain stored

### Requirement: Signal classification and recording
The tool SHALL classify every block signal as one of: rate limit (HTTP 429 or a "please wait" message), challenge or checkpoint, action block (`feedback_required`), or session rejected. The signal type and Instagram's raw message SHALL be recorded on the scan, when there is one, and on any cooldown it causes. The tool SHALL tell the user which signal occurred and what to do next.

#### Scenario: Action block
- **WHEN** Instagram responds with `feedback_required`
- **THEN** the scan records the signal as an action block with Instagram's message, and the tool shows the type, the message, and when scanning can resume

#### Scenario: Challenge guidance
- **WHEN** Instagram responds with a challenge or checkpoint
- **THEN** the tool tells the user to verify the account by hand (for example in Firefox), restore the session if needed, and then run `session check`

### Requirement: Errors that are not block signals
Any other failure of an Instagram request, such as a network error or an unrecognised response, SHALL stop the run without retrying and without setting a cooldown. Its raw message SHALL be recorded on the scan, and the scan SHALL stay resumable.

#### Scenario: Network failure
- **WHEN** a page request fails because the network is unavailable
- **THEN** the run stops, the error is recorded, no cooldown is set, and `resume` can continue the scan

#### Scenario: Follower list not visible
- **WHEN** a page request succeeds but returns no followers, as it does for a private target the research account does not follow
- **THEN** the run stops as defined by follower scanning, no cooldown is set, and the scan stays resumable

### Requirement: Cooldown after rate limit or action block
After a rate limit or an action block, the tool SHALL set a cooldown for the research account lasting 24 hours, or as long as Instagram states if that is longer. The default duration SHALL be configurable. The cooldown SHALL apply to the research account as a whole and SHALL persist across runs. While it is active, `scan`, `resume` and `target add` SHALL make no Instagram request and SHALL report when the cooldown ends. Commands that make no Instagram request SHALL keep working.

#### Scenario: Scan during cooldown
- **WHEN** a cooldown set 3 hours ago is active and the user runs `scan`
- **THEN** the tool makes no request and reports the time the cooldown ends

#### Scenario: Instagram states a longer wait
- **WHEN** a rate-limit message states that the account must wait 48 hours
- **THEN** the cooldown lasts 48 hours

#### Scenario: Instagram states a shorter wait
- **WHEN** a rate-limit message states a wait of 1 hour
- **THEN** the cooldown still lasts the configured 24 hours

#### Scenario: Cooldown over
- **WHEN** the cooldown has ended
- **THEN** `scan`, `resume` and `target add` are allowed again

### Requirement: Challenge hold
After a challenge or checkpoint, the tool SHALL set the same cooldown and SHALL also hold the research account. The hold SHALL lift only when the cooldown has ended and a `session check` has succeeded after the challenge was recorded. While the hold is active, `scan`, `resume` and `target add` SHALL make no Instagram request and SHALL state which of the two conditions is still missing.

#### Scenario: Cooldown ended but no session check
- **WHEN** 24 hours have passed since a challenge and no successful `session check` has run since
- **THEN** `scan` is refused and the tool tells the user to verify the account and run `session check`

#### Scenario: Session check during cooldown
- **WHEN** the user runs a successful `session check` 2 hours after a challenge
- **THEN** the check is recorded, and scanning is still refused until the cooldown ends

#### Scenario: Both conditions met
- **WHEN** the cooldown has ended and a successful `session check` ran after the challenge
- **THEN** the hold is lifted and `scan` is allowed

#### Scenario: Failed session check
- **WHEN** `session check` fails after a challenge
- **THEN** the hold stays in place

### Requirement: No cooldown for a rejected session
After a session rejected signal, the tool SHALL NOT set a cooldown. It SHALL stop and tell the user to log in to Instagram in Firefox and run `session import` again. Once the session is restored, `resume` SHALL be allowed straight away.

#### Scenario: Resume after restoring the session
- **WHEN** a scan stopped with a session rejected signal and the user has re-imported the session
- **THEN** `resume` is allowed immediately and continues the scan

### Requirement: Block signals from any command
The classification, recording, cooldown and hold rules SHALL apply to block signals raised by any command that talks to Instagram, including `target add` and `session check`, not only to scans.

#### Scenario: Rate limit on target add
- **WHEN** `target add` receives a rate-limit response
- **THEN** the target is not stored and a cooldown is set as for a scan
