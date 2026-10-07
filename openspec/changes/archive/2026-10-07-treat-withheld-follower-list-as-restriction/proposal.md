# Proposal

## Why

After 200 pages of one scan, Instagram stopped serving the research account's follower lists: the next page request was answered with a redirect to the home page, and for about two days the followers popup in Firefox was empty for every profile (other accounts still saw the lists). The restriction lifted by itself. igft reported the redirect as a plain fetch error, set no cooldown and suggested `resume --restart`, so the user could have resumed straight into the restriction, which is what request safety exists to prevent.

## What Changes

- A redirect to Instagram's home page in answer to a follower-page request is treated as the follower list being withheld from the account. The run stops at once, no request is retried, a cooldown is set, and the tool says Instagram is withholding the list and the user should wait.
- A follower page that comes back empty after the scan has already saved followers is treated the same way, because an account that was reading the list a moment ago has most likely been restricted. An empty first page keeps the current meaning (private target not followed), with no cooldown.
- A failed `resume` no longer suggests `resume --restart`. Instagram does not reject cursors on this endpoint, so the hint was misleading; `--restart` stays available as a deliberate choice.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `request-safety`: "Signal classification and recording" classes a withheld follower list as a rate limit (so the existing 24 h cooldown applies), with scenarios for the redirect and the guidance; "Errors that are not block signals" excludes the home-page redirect.
- `follower-scanning`: "A page with no followers is not the end of the list" sets a cooldown when the page follows saved pages; "Restart an unfinished scan from page 1" no longer has the tool suggest `--restart` after a failed resume.

## Impact

- Code: response classification in the instaloader adapter (`src/igft/fetchers/instaloader_adapter.py`), the empty-page and resume-hint handling in `src/igft/scanning/service.py`, and, if needed, the cooldown handling in the safety gate.
- Tests: adapter classification, scan service (empty page mid-scan, no hint after a failed resume) and gate cooldown tests.
- Docs: `README.md` (request-safety table and the `--restart` description).
- No database, configuration or dependency changes; existing unfinished scans are untouched and stay resumable.
- Risks, each covered in design.md: a target that turns private mid-scan now also costs a 24 h cooldown, and the home-page redirect is recognised by its destination alone.
