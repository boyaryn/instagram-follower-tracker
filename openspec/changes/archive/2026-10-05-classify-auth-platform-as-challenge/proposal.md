# Proposal

## Why

During a scan Instagram redirected a follower-page request to `/auth_platform/`, its human-verification page (the user only had to tick an "I'm human" box, no email or SMS). igft did not recognise this as a challenge, so it reported a plain fetch error and recorded no cooldown and no hold. The user could have resumed straight away and hit the same verification again, which is what request safety exists to prevent.

## What Changes

- A redirect to Instagram's `/auth_platform/` verification page is classified as a challenge, like the existing challenge and checkpoint redirects.
- The scan stops with the challenge guidance, exits with the block-signal code, and records a cooldown and a challenge hold, as for any challenge.
- The README's description of the challenge signal mentions that verification can be as small as a "confirm you are human" box.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `request-safety`: "Signal classification and recording" names the verification page as a challenge, with a scenario for it.

## Impact

- Code: the classification of responses in the instaloader adapter (`src/igft/fetchers/instaloader_adapter.py`).
- Tests: adapter classification tests (the cooldown and hold for any challenge are already covered by the gate tests).
- Docs: `README.md` ("Request safety" table).
- No database, configuration or dependency changes. The already-failed scan is untouched and stays resumable; no cooldown is recorded for it retroactively (see design.md).
