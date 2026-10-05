# Design

## Context

`classify()` in `src/igft/fetchers/instaloader_adapter.py` decides whether a non-success response is a block signal. It recognises a challenge by the words `challenge_required` / `checkpoint_required` in the message, or by a redirect whose path starts with `/challenge` or `/checkpoint`. The fetcher follows no redirects, so Instagram's 302 arrives as the response and only its `Location` is available. Anything unrecognised becomes a `FetchError`: exit code 5, no cooldown, no hold. See proposal.md - Why for the incident.

Everything downstream of a `SignalKind.CHALLENGE` (recording on the scan, cooldown, hold, guidance, exit code 4, lifting the hold through `session check`) already exists and needs no change.

## Goals / Non-Goals

**Goals:**
- A redirect to `/auth_platform/` takes the same path as every other challenge.

**Non-Goals:**
- Classifying other unknown redirects as blocks. The adapter's rule stays: an unrecognised response is a `FetchError`, never guessed to be a block.
- Recording a cooldown or hold for the scan that already failed. The user has verified the account by hand and can simply wait before resuming.
- Detecting the verification page by its HTML or by the `apc` query value. Nothing is fetched or parsed beyond the redirect target.

## Decisions

**D1. Extend the existing path match with `auth_platform`.** The challenge test becomes `re.match(r"/(challenge|checkpoint|auth_platform)", path)`. The match is on the redirect's path only, so the login redirect `/accounts/login/?next=/auth_platform/...` still classifies as a rejected session, as the existing `next=/challenge/` test requires. Alternatives: a substring match on the whole `Location` would misclassify that login redirect; a separate `SignalKind` for "human verification" would need a migration (the kinds are a database constraint) and new guidance, for the same remedy.

**D2. Keep the challenge guidance text.** It already says to verify the account by hand in Firefox, restore the session if needed and run `session check`. That fits a tick-box verification too. Only the README gains a sentence saying the verification can be as small as a "confirm you are human" box, so that users do not wait for an email or SMS that never comes.

## Risks / Trade-offs

- [`/auth_platform/` also serves flows that are not a challenge] → Any redirect there means Instagram is asking the account for something before serving data, so stopping with a 24-hour cooldown and a hold is the safe response; a false positive costs a day, a false negative costs account standing.
- [Instagram renames the verification path again] → It would surface as a `FetchError` carrying the full redirect, as this one did, and gets a one-line addition.
