# Design

## Context

Two places decide what a failed or odd follower page means.

- `classify()` in `src/igft/fetchers/instaloader_adapter.py` maps a non-success response to a `BlockSignal` or a `FetchError`. The fetcher follows no redirects, so a 302 arrives as the response with its `Location`. A redirect to `/` matches no rule and becomes a `FetchError` (exit code 5, no cooldown).
- `ScanService._run` in `src/igft/scanning/service.py` stops with `list_unavailable` on an empty page, with no cooldown and exit code 5. A fetcher exception that is a `BlockSignal` is recorded by `GuardedFetcher` (scan, cooldown, guidance) and re-raised; the service then marks the scan `block_signal`.

The `--restart` suggestion comes from two places: the `FetchError.hint` set in `ScanService.resume`, and the "or start from page 1 with `--restart`" sentence in `_report_kept` in `src/igft/cli/scan.py`, which prints after every failed `scan` or `resume`.

The signal kind of a cooldown and of a scan is a database constraint (`SIGNAL_KINDS` in migration 0001). See proposal.md for the incident and specs/ for the required behaviour.

## Goals / Non-Goals

**Goals:**
- The redirect and the mid-scan empty page both end in the existing rate-limit path (record, 24 h cooldown, no hold, no retry), with their own guidance text.
- No new migration, setting or dependency.

**Non-Goals:**
- A new signal kind, a cooldown longer than the configured one, or a hold that needs `session check`. The user verified there was no challenge, so a hold would have nothing to verify.
- Detecting a restriction from anything but the two signs above. In particular an empty first page of a scan that has saved nothing keeps its private-target meaning (see Risks).
- Fixing the page-cap message, which also mentions `--restart` but is not a failure.

## Decisions

**D1. Record a withheld list as `SignalKind.RATE_LIMIT`.** It is a rate limit in effect (Instagram slows the account down and the remedy is to wait) and it already gets the 24 h cooldown, no hold, exit code 4 and the "not refused during session commands" rules. Alternatives: a new kind `list_withheld` needs a migration to change the check constraint and was ruled out for this change; `ACTION_BLOCK` implies Instagram's `feedback_required` message; `CHALLENGE` sets a hold that only a successful `session check` lifts, though nothing was asked of the user.

**D2. `BlockSignal` gets a `withheld_list` flag that only selects the guidance text.** `guidance_for(kind, ends_at, withheld_list=False)` returns a different message when the flag is set: Instagram is withholding the follower list from the account; wait until the cooldown ends; before resuming, open a followers list in Firefox and check it is not empty; and that starting over from page 1 does not get past it (the text does not contain the flag itself). The flag is not stored. The stored `raw_message` carries the explanation (the redirect target, or "empty follower page after N pages were saved"). Alternative: a subclass of `BlockSignal`; the flag is enough for one extra message.

**D3. Recognise the redirect only for follower-page requests, by destination.** `classify` and `_json` take a `follower_page` argument, set only by `fetch_followers_page`, so a redirect to `/` on the profile page or session check stays a plain error. The rule goes after the challenge, action block and login tests and before the 429 test: a 3xx status with a `Location` that is either relative or on `instagram.com`, and whose path is `/` (or empty). Query strings are ignored, as in the existing path tests. The host check keeps a captive portal or proxy redirect to `http://192.168.0.1/` from being read as Instagram withholding the list. Alternative considered: match any 3xx; rejected, since the adapter's rule is that an unrecognised response is an error and is never guessed to be a block.

**D4. The empty-page decision lives in `ScanService`, and the service records the signal itself.** On an empty page the service reads the scan's recorded page count. If it is zero, behaviour is unchanged. If it is above zero, the service builds `BlockSignal(RATE_LIMIT, "empty follower page after N pages ...", withheld_list=True)`, passes it to a new public `GuardedFetcher.record_block(signal)` (the existing record-and-set-guidance step, moved out of `_call`), marks the scan `list_unavailable`, and raises the signal. The generic `except BlockSignal` handler marks `block_signal` only for signals that came from the fetcher, which the service tracks with a local flag, so the stop reason stays `list_unavailable` as specified. The page count counts saved pages only (a page is saved after the empty check), so a count above zero means followers were saved, in this run or an earlier one. The exit code is 4, like any block signal. Alternatives: have the adapter raise on an empty page (it cannot know what the scan has saved); count `followers_seen` instead (equivalent, but the page count is not affected by how a restarted baseline counts "known").

**D5. Remove the `--restart` suggestions after failures.** Delete the `except FetchError` block with its hint in `ScanService.resume`, remove `FetchError.hint` and its use in `message_for`, since nothing else sets it, and drop the "or start from page 1 with `--restart`" sentence from `_report_kept`. `--restart` and its option help stay, as does the page-cap message.

**D6. Docs.** README: the request-safety table gets a row for the withheld list, the `--restart` description no longer says igft suggests it, and the exit-code line for 4 needs no change (it already says "block signal").

## Risks / Trade-offs

- [A target that turns private mid-scan now costs a 24 h cooldown, because its empty page looks the same] → Accepted: a false positive costs a day, a false negative costs account standing, as with `/auth_platform/`. The guidance text still tells the user to check the list in Firefox.
- [Any redirect to the home page of Instagram is read as "withheld"] → It was the only observed answer during the restriction; if Instagram sends the same redirect for something else, the cost is again one cooldown.
- [A new scan started during a restriction may get an empty first page with nothing saved, which keeps the private-target meaning and sets no cooldown] → Not observed; the observed signal was a redirect, which is caught. Revisit if it happens.
- [The restriction lasted about two days, longer than one 24 h cooldown] → After the cooldown the next `resume` makes one request and, if the list is still withheld, gets a new cooldown. `cooldown_hours` can be raised; the guidance asks the user to check Firefox first.
- [The cooldown and gate messages say "rate limit" for this case] → The guidance text and the recorded raw message carry the real explanation.

## Migration Plan

No database, configuration or dependency change. Existing unfinished scans are untouched; the user may resume them after checking in Firefox that a followers list opens. Rollback is reverting the code.
