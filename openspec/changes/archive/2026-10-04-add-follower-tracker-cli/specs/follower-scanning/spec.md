# Spec Delta

## Purpose

Reads a target's follower list page by page and records who follows it, stopping early by default once it reaches followers it already knows, and continuing interrupted scans from where they stopped or, on request, from the first page.

## ADDED Requirements

### Requirement: One target per run
`scan <username>` SHALL scan exactly one target per run. The target MUST already have been added with `target add`. The scan SHALL obey the cooldown, challenge hold, delay, page cap and block-signal handling defined by request safety.

#### Scenario: Known target
- **WHEN** the user runs `scan` for an added target and no cooldown or hold is active
- **THEN** the tool scans that target's follower list and no other target

#### Scenario: Unknown target
- **WHEN** the user runs `scan` for a username that is not a target
- **THEN** the tool exits with a non-zero status, tells the user to run `target add` first, and makes no Instagram request

### Requirement: Follower list only
A scan SHALL fetch followers only through the paginated follower list. It SHALL NOT make a separate request for any individual follower.

#### Scenario: Page of followers
- **WHEN** a scan fetches a page of followers
- **THEN** every follower on that page is recorded from the page data alone, with no additional per-follower request

### Requirement: Save each page as it arrives
After each page is fetched, the tool SHALL save that page's followers and the pagination cursor for the next page before requesting another page. A scan that stops for any reason SHALL keep every page saved before it stopped.

#### Scenario: Interrupted after several pages
- **WHEN** a scan is interrupted, by the user or by an error, after its fifth page was saved
- **THEN** the followers from all five pages remain stored and the scan holds the cursor for the sixth page

### Requirement: Baseline first scan
A target's first scan SHALL be its baseline, including any resumed continuation of that scan. A baseline scan SHALL read the whole follower list, and followers first seen in it SHALL never be reported by `first-seen`.

#### Scenario: First scan of a target
- **WHEN** the user runs `scan` for a target that has never been scanned
- **THEN** the scan is recorded as the baseline and continues to the end of the follower list, unless it is interrupted

### Requirement: Early stop by default
Outside a baseline and without `--full`, a scan SHALL stop once it has fetched 2 pages in a row in which every follower was already recorded for this target in an earlier scan. The number of pages SHALL be configurable. A follower first recorded during the current scan SHALL NOT count as already known. A page that contains any follower not already known SHALL reset the count. The count SHALL also start again at zero when a scan is resumed.

#### Scenario: Two known pages in a row
- **WHEN** a non-baseline scan fetches one page with followers not seen before, then two pages with only already-known followers
- **THEN** the scan stops after the third page and is recorded as complete

#### Scenario: Known pages interrupted by a follower not seen before
- **WHEN** a scan fetches a fully known page, then a page with one follower not seen before, then a fully known page
- **THEN** the scan does not stop early at that point

#### Scenario: Resumed after one known page
- **WHEN** a scan is interrupted right after one page with only already-known followers, and the user runs `resume`
- **THEN** the scan stops after two more pages with only already-known followers, because the count started again at zero

#### Scenario: Last page reached first
- **WHEN** the follower list ends, on a page that contains followers and has no next cursor, before the early-stop condition is met
- **THEN** the scan stops at the end of the list and is recorded as complete

### Requirement: Full scan
`scan <username> --full` SHALL disable early stop and read the whole follower list.

#### Scenario: Full scan with known followers
- **WHEN** the user runs `scan --full` on a target whose followers are all already known
- **THEN** the scan continues to the end of the list

### Requirement: A page with no followers is not the end of the list
A page that contains no followers SHALL NOT be treated as the end of the follower list, whether it is the first page or a later one, and whether the scan is a baseline or not. When a page contains no followers, the scan SHALL stop, stay unfinished with its saved cursor, and record the stop reason `list_unavailable`. It SHALL NOT set a cooldown. The tool SHALL tell the user that the follower list came back empty and that, if the target is private, the research account must follow it. A target that really has no followers therefore cannot be scanned.

A target can turn private, or unfollow the research account, after it was added. Instagram then answers a follower request with a normal success and an empty list, which cannot be told apart from the end of a list by its shape.

#### Scenario: Private target the research account does not follow
- **WHEN** the first page of a baseline scan comes back successfully with no followers and no next cursor
- **THEN** the scan is not recorded as complete, it stays unfinished with the stop reason `list_unavailable`, nothing is stored for it, and the tool exits with a non-zero status and tells the user to check that the research account follows the target

#### Scenario: Access lost during a scan
- **WHEN** a scan has saved five pages and the sixth comes back with no followers
- **THEN** the five saved pages remain stored, the scan stays unfinished with its cursor, and the user can run `resume` once the research account follows the target again

#### Scenario: No cooldown
- **WHEN** a scan stops with the stop reason `list_unavailable`
- **THEN** no cooldown or hold is set, and `scan`, `resume` and `target add` are not refused because of it

### Requirement: Scan outcome
When a scan ends, the tool SHALL record its status (complete or unfinished), the reason it stopped, the number of pages fetched and followers seen, and the number of followers seen for the first time, and SHALL show a summary to the user. A baseline scan SHALL be shown as a baseline rather than with a count of followers seen for the first time.

#### Scenario: Completed scan summary
- **WHEN** a non-baseline scan completes
- **THEN** the tool shows the pages fetched, followers seen, and followers seen for the first time

### Requirement: One unfinished scan per target
While a target has an unfinished scan, `scan` for that target SHALL be refused, and the tool SHALL point the user to `resume`.

#### Scenario: Scan while unfinished
- **WHEN** the user runs `scan` for a target whose last scan is unfinished
- **THEN** the tool starts no new scan, makes no request, and tells the user to run `resume`

### Requirement: Resume an unfinished scan
`resume <username>` SHALL continue the target's unfinished scan from its saved cursor, with the same mode (default or `--full`) as the scan that started it. The resumed run SHALL belong to the same scan record.

#### Scenario: Normal resume
- **WHEN** the user runs `resume` for a target with an unfinished scan whose cursor is still valid
- **THEN** fetching continues from the saved cursor, and pages already saved are not fetched again

#### Scenario: Nothing to resume
- **WHEN** the user runs `resume` for a target with no unfinished scan
- **THEN** the tool reports that there is nothing to resume and makes no request

### Requirement: Restart an unfinished scan
`resume <username> --restart` SHALL continue the target's unfinished scan from the first page of the follower list instead of the saved cursor, keeping the followers saved so far. The tool SHALL NOT restart a scan on its own. If Instagram rejects a saved cursor, that SHALL be handled like any other failed request, as defined by request safety, and the tool SHALL point the user to `resume --restart`.

#### Scenario: Rejected cursor
- **WHEN** `resume` fails because Instagram does not accept the saved cursor
- **THEN** the run stops, the error is recorded, the scan stays unfinished, and the tool suggests `resume --restart`

#### Scenario: Restart on request
- **WHEN** the user runs `resume --restart` for a target with an unfinished scan
- **THEN** fetching starts from page 1 of the follower list, and previously saved followers remain stored

#### Scenario: Restarted baseline
- **WHEN** a baseline scan is resumed with `--restart`
- **THEN** the followers it saved before the restart do not count as already known, so the restart still reads the whole list
