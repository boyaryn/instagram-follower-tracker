# Spec Delta

## MODIFIED Requirements

### Requirement: A page with no followers is not the end of the list
A page that contains no followers SHALL NOT be treated as the end of the follower list, whether it is the first page or a later one, and whether the scan is a baseline or not. When a page contains no followers, the scan SHALL stop, stay unfinished with its saved cursor, and record the stop reason `list_unavailable`. A target that really has no followers therefore cannot be scanned.

If the scan has not yet saved any followers (in this run or an earlier one), the tool SHALL NOT set a cooldown. It SHALL tell the user that the follower list came back empty and that, if the target is private, the research account must follow it.

If the scan has already saved followers, the empty page means the research account has most likely been restricted, and the tool SHALL treat it as a withheld follower list, a block signal defined by request safety: it SHALL set a cooldown and tell the user as for any withheld list, while the stop reason stays `list_unavailable`.

A target can turn private, or unfollow the research account, after it was added. Instagram then answers a follower request with a normal success and an empty list, which cannot be told apart from the end of a list, or from a restriction of the research account, by its shape.

#### Scenario: Private target the research account does not follow
- **WHEN** the first page of a baseline scan comes back successfully with no followers and no next cursor
- **THEN** the scan is not recorded as complete, it stays unfinished with the stop reason `list_unavailable`, nothing is stored for it, and the tool exits with a non-zero status and tells the user to check that the research account follows the target

#### Scenario: Access lost during a scan
- **WHEN** a scan has saved five pages and the sixth comes back with no followers
- **THEN** the five saved pages remain stored, the scan stays unfinished with its cursor and the stop reason `list_unavailable`, the signal is recorded as a rate limit, a cooldown is set, and the tool tells the user that Instagram is withholding the follower list and to wait before running `resume`

#### Scenario: Empty page at the start of a resumed run
- **WHEN** a scan that saved 200 pages in an earlier run is resumed and its first page in the new run comes back with no followers
- **THEN** it is handled as an empty page after saved pages, with a cooldown

#### Scenario: Cooldown applies after a withheld list
- **WHEN** a scan stopped with the stop reason `list_unavailable` after saved pages and the user runs `scan`, `resume` or `target add` during the cooldown
- **THEN** the tool makes no request and reports when the cooldown ends

#### Scenario: No cooldown
- **WHEN** a scan stops with the stop reason `list_unavailable` and it has saved no followers
- **THEN** no cooldown or hold is set, and `scan`, `resume` and `target add` are not refused because of it

### Requirement: One unfinished scan per target
While a target has an unfinished scan, `scan` for that target SHALL be refused, and the tool SHALL point the user to `resume`, without suggesting `--restart`.

#### Scenario: Scan while unfinished
- **WHEN** the user runs `scan` for a target whose last scan is unfinished
- **THEN** the tool starts no new scan, makes no request, and tells the user to run `resume`, and the message does not suggest `resume --restart`

### Requirement: Restart an unfinished scan
`resume <username> --restart` SHALL continue the target's unfinished scan from the first page of the follower list instead of the saved cursor, keeping the followers saved so far. The tool SHALL NOT restart a scan on its own, and SHALL NOT suggest `--restart` when a run fails: Instagram does not reject a saved cursor on the follower endpoint, so a failed `resume` is handled like any other failed request, as defined by request safety, and a restart is the user's own choice.

#### Scenario: Rejected cursor
- **WHEN** `resume` fails because Instagram does not accept the saved cursor, or for any other reason
- **THEN** the run stops, the error is recorded, the scan stays unfinished, and the output does not suggest `resume --restart`

#### Scenario: Restart on request
- **WHEN** the user runs `resume --restart` for a target with an unfinished scan
- **THEN** fetching starts from page 1 of the follower list, and previously saved followers remain stored

#### Scenario: Restarted baseline
- **WHEN** a baseline scan is resumed with `--restart`
- **THEN** the followers it saved before the restart do not count as already known, so the restart still reads the whole list
