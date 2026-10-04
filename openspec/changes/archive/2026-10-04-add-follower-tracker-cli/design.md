# Design

## Context

The project is empty apart from OpenSpec scaffolding, so this is a greenfield Python package. See proposal.md (Why) for motivation and the specs under `specs/` for the required behaviour.

Constraints that shape the approach:

- Single user, run by hand against an existing local PostgreSQL instance. No servers, schedulers or paid services.
- The main host is Ubuntu, but the tool must not assume a particular OS or distribution. Paths, Firefox profile locations and the user agent are therefore resolved per platform or taken from configuration, never hard-coded. On Ubuntu, Firefox is installed as a snap by default, so its profile is not in the classic `~/.mozilla/firefox` location.
- One third-party scraping library, instaloader, which talks to Instagram's web API with browser cookies. Its built-in behaviour (retries, sleeps, login helpers) conflicts with the request-safety specs and has to be switched off or wrapped, and its profile and follower helpers send requests the current web app no longer makes (D4). instagrapi, which talks to the private mobile API, was evaluated and deferred (D5).
- The user edits `persons.tags` and `persons.is_marked` directly in their SQL client, so the schema is a user-facing interface, not an internal detail.
- Instagram documents none of the web endpoints, page sizes, cursor formats or error payloads. The spike (`spike/findings.md`) measured them before real use; the results are folded into D4 and D5.

## Goals / Non-Goals

**Goals:**
- Commands that must not touch Instagram (`list`, `first-seen`, `target list`) cannot do so by construction: they never create a fetcher.
- Every Instagram request passes through one safety gate. No code path can make a request without going through it.
- A scan can be killed at any moment (Ctrl-C, crash, power loss) and still leave the database consistent and resumable.
- Library-specific details stay inside one adapter module behind the `Fetcher` protocol, so upgrading instaloader, or adding a second backend in a later change, does not touch scanning, safety or storage.
- Core logic (early stop, cooldown and hold rules, "what counts as first seen") can be tested without network access or real delays.

**Non-Goals:**
- Supporting more than one research account at a time. Cooldowns and holds are global, not per account.
- Concurrent scans. Only one process may talk to Instagram at a time.
- Portability to databases other than PostgreSQL. Arrays, triggers and partial indexes are used deliberately.
- Official Windows support. The code avoids OS-specific assumptions, and Windows Firefox paths are covered by unit tests, but real use is expected on POSIX systems (Ubuntu first, macOS possible). The session-file permission check (D6) relies on POSIX file modes.
- Automatic scheduling. Wrapping the CLI in cron is left to the user.
- Making a scan complete or consistent. Instagram's follower list can reorder between page requests (the spike measured about 1 in 8 followers repeated or missed per pass), so a scan may repeat or miss followers. The tool accepts this: no extra passes, no deduplication, no consistency checks. Repeats are ordinary upserts, and a missed follower is simply first seen by a later scan.

## Decisions

### D1. Stack: Python 3.12, uv, Typer + Rich, SQLAlchemy 2.0 + psycopg 3, Alembic

- **Typer** gives typed subcommands (`session import|check`, `target add|list`, `scan`, `resume`, `list`, `first-seen`, `db upgrade`) and enum validation for `--format`, which covers the "unknown value lists valid values" scenario. **Rich** renders the terminal tables. *Alternative:* plain Click plus tabulate. It works, but needs more boilerplate for the same validation.
- **SQLAlchemy Core-style statements through the 2.0 API** with `postgresql.insert(...).on_conflict_do_update` for upserts. *Alternative:* raw psycopg SQL. It is simpler, but Alembic autogenerate and typed table metadata are worth the dependency.
- **uv** with a `pyproject.toml` and a `src/` layout. The console script is `igft`.
- **platformdirs** resolves the config and data directories for each OS, so no home-relative path is hard-coded (see D6, D12).
- **PostgreSQL** is a given, not a choice made here: the user already runs a local instance and wants to tag and mark followers in their own SQL client (proposal.md, Why). The design uses its arrays, triggers and partial indexes deliberately (see Non-Goals and D7, D8). The specs only say "the database".
- **instaloader** is the one scraping library (D4). instagrapi was evaluated and deferred (D5).

### D2. Layered package layout

```
src/igft/
  cli/          Typer commands: parse args, call services, render output, map errors to exit codes
  config.py     load TOML + env, validate
  domain.py     dataclasses and errors shared by all layers
  fetchers/     Fetcher protocol, instaloader adapter, Firefox cookie reader, signal classifier
  safety/       SafetyGate (cooldown/hold checks, signal recording), Pacer (delays), duration parsing
  scanning/     ScanService (baseline, early stop, page cap, resume, restart on request)
  reporting/    read-only queries and table/CSV/JSON renderers
  db/           table metadata, engine/session setup, repositories
  migrations/   Alembic env and versions, shipped inside the package
```

Dependencies point downward only: `cli` → services → `fetchers`/`db` → `domain`. The reporting and `target list` code paths import nothing from `fetchers`, and instaloader is imported lazily inside the adapter. This makes "offline" a structural property, and it also keeps `list` fast.

### D3. One `Fetcher` protocol, normalised domain types

```python
class Fetcher(Protocol):
    backend: Backend                                        # only "instaloader" in this change
    def get_profile(self, username: str) -> ProfileInfo      # one request
    def fetch_followers_page(self, user_id: int, cursor: str | None, *, username: str) -> FollowerPage  # one request; the username is only for the Referer header (D4)
    def check_session(self) -> str                           # one request, returns account username
```

- `FollowerRecord(pk: int, username: str, full_name: str | None, is_private: bool | None, is_verified: bool | None)`. `None` means "the follower list did not provide it", which is what drives the keep-the-stored-value rule. The adapter maps a missing, non-string or empty `full_name` to `None`. The adapters drop profile picture URLs when they map the raw data, so these URLs never reach the storage layer.
- `FollowerPage(followers: list[FollowerRecord], next_cursor: str | None)`. `next_cursor is None` means the end of the list. Cursors are opaque strings, and the scan service never interprets them.
- `ProfileInfo(pk, username)` is what `target add` stores. It carries no private flag and no follow status, because the profile page does not provide them for the target (D4).
- Failures are raised as typed exceptions from `domain.py`: `BlockSignal(kind, raw_message, stated_wait: timedelta | None)`, `SessionMissing`, `ProfileNotFound`, and `FetchError(raw_message)` for everything else, including a rejected cursor. The adapter owns a `classify()` function that maps the library's exceptions and response bodies to these types. Anything it does not recognise becomes `FetchError`, never a silent retry.
- The `Backend` enum, the `backend` attribute and the `scans.backend` / `session_checks.backend` columns stay, with the single value `instaloader`, so that a later change can add a second backend without a data migration. There is no `--backend` option and no `default_backend` setting in this change.

*Alternative considered:* wrapping instaloader's own iterator (`Profile.get_followers()` with `NodeIterator.freeze()`). Rejected, because it hides page boundaries and fetches ahead, and because it uses a GraphQL query (`query_hash=37479f2b…`) the current web app no longer sends (D4). The spec needs "one call = one request = one page" so that it can save and pace each page.

### D4. instaloader adapter: Firefox cookies, the web app's own requests, no retries

- **Import:** read `cookies.sqlite` from the Firefox profile. When `firefox_profile` is set in the config, that path is used. Otherwise the tool looks for `profiles.ini` in each known Firefox base directory for the current platform, in this order:
  - Linux: `~/snap/firefox/common/.mozilla/firefox` (the Ubuntu default snap), `~/.var/app/org.mozilla.firefox/.mozilla/firefox` (Flatpak), then `~/.mozilla/firefox`.
  - macOS: `~/Library/Application Support/Firefox`.
  - Windows: `%APPDATA%\Mozilla\Firefox`.

  It chooses the profile that `profiles.ini` marks as default (the `Install*` section's `Default=`, falling back to `Default=1`). If several base directories contain profiles, or none do, the tool stops and lists the candidates it found, asking the user to set `firefox_profile`, instead of guessing. The adapter first copies the file to a temporary location, because Firefox keeps it locked. It loads the `instagram.com` cookies into instaloader's session, calls `test_login()` to get the username, and saves the session with `save_session_to_file`. If no `sessionid` cookie is present, it raises `SessionMissing` with the "log in to Instagram in Firefox" guidance, before it makes any request. The import also needs the `csrftoken` cookie, which the follower request sends back as `X-CSRFToken`. The spike found the snap Firefox profile on Ubuntu automatically.
- **Requests the web app sends, not instaloader's helpers.** The spike compared instaloader's helpers with Firefox's Network panel (2026-10-02). `Profile.from_username()` calls `api/v1/users/web_profile_info/`, which the web app no longer sends; the spike got a 429 for it within 0.09 s, with an empty body, on its second request. `Profile.get_followers()` uses a GraphQL `query_hash` the web app does not send either. The adapter therefore sends the web app's own two requests through instaloader's cookie session (`context._session`), with the headers a browser would send for each:
  - **Profile lookup:** `GET https://www.instagram.com/<username>/`, a normal page load, with redirects not followed. The numeric id is the single distinct `"profile_id":"<digits>"` value in the HTML (one request, about 785k characters, exactly one value in the spike). The user id only has to be read once per target, at `target add`, and is stored. The page does not say whether the target is private or whether the research account follows it. The user checked the raw HTML with view-source for a public profile, a private profile the account follows and a private profile it does not follow (2026-10-03): each page has a single `is_private`, inside the object of the research account's own `profile_id`, and no `followed_by_viewer`, `friendship_status` or `following`. `target add` therefore stores only the id and username, and the researcher is responsible for following a private target before adding and scanning it. Losing access later is handled at scan time (D9).
  - **Follower page:** `GET https://www.instagram.com/api/v1/friendships/<id>/followers/?count=12&search_surface=follow_list_page`, plus `&max_id=<cursor>` after the first page, with `X-CSRFToken`, the web app's `x-ig-app-id` and `Referer: https://www.instagram.com/<username>/followers/`. This is exactly what the followers popup sends when scrolled. The response has `users`, `next_max_id`, `has_more`, `big_list` and `page_size`. The spike got 12 users per page; Firefox once got 11 for `count=12`, so the adapter does not assume a fixed page size.
  - **Cursor:** `next_max_id` is a decimal offset in a string (`"12"`, then `"24"`). The adapter passes it back unchanged as `max_id` and never interprets it. `FollowerPage.next_cursor` is `None` when `next_max_id` is missing or `has_more` is false; the end of a list was measured on two small targets (`spike/end_of_list_probe.py`, 2026-10-03): the last page has followers, no `next_max_id` key and `has_more: false`. The adapter does not try to recognise a target the account cannot see. For a private profile the research account does not follow, the probe (`spike/private_probe.py`, 2026-10-03) got HTTP 200 with `status: "ok"`, `users: []`, `has_more: false`, `use_clickable_see_more: false` and no `next_max_id` (74 bytes): a normal page with no followers, which the adapter maps to `FollowerPage([], None)`. `ScanService` decides what that means (D9).
  - **Session check:** `test_login()`, which sends one GraphQL request (`query_hash=d6f4427f…`) through instaloader's `get_json()`. The spike confirmed it is a single request and returns the account's username.
- **No retries or hidden waits on errors:** construct `Instaloader(max_connection_attempts=1, ...)` with a custom `RateController` subclass whose `handle_429` raises `BlockSignal(RATE_LIMIT)` instead of sleeping and retrying. This covers `test_login()`, which goes through `get_json()`. The two direct requests bypass `get_json()`, its sleeps and its redirect following, so the adapter checks their responses itself: any redirect or non-200 status goes to `classify()`, which maps 429 to `BlockSignal(RATE_LIMIT)` and anything unrecognised to `FetchError`. Nothing is retried.
- **User agent:** the instaloader user agent is set to a configurable string. By default it is a current desktop Firefox user agent for the host platform, built from `platform.system()` and `platform.machine()`, for example `X11; Ubuntu; Linux x86_64` on Ubuntu. This way the imported cookies are not presented with an obviously different browser or OS identity. The README tells the user to paste their Firefox's exact user agent into the config for the closest match. Both direct requests send this user agent too.
- **No password path:** the adapter never calls `login()`. `session import` is the only way a session is created.

### D5. instagrapi: evaluated and deferred

instagrapi (Instagram's private mobile API) was planned as a second backend, with a one-time manual password login and a pinned device fingerprint. It is not part of this change, because its login is blocked. The spike (`spike/findings.md`) tried it three times, each time by hand, with no retries:

- 2026-10-01 and 2026-10-02, about 19:45 UTC: research account, home network, instagrapi 3.0.16, its default device profile (Pixel 8 Pro, Android 14, US locale).
- 2026-10-02 21:28 UTC: a different account, a phone hotspot, instagrapi 3.0.18, and the owner's real phone model (Pixel 6a, Android 17) with matching locale, calling code and timezone.

Every attempt made the same six requests. The first four succeeded, `qe/sync/` returned 405, and the request that submits the credentials (`bloks…login.async.send_login_request/`) got a 429 within 0.01–0.08 s with an empty body. No checkpoint, two-factor or verification prompt ever appeared, so the credentials were never checked. Account, network, library version and claimed device all changed without effect. What stayed the same is instagrapi's login request itself. It reports the phone's hardware keystore as unavailable in `X-IG-Attest-Params` (error `-1013`, no signed nonce), and it is sent by curl rather than the app's own network stack. Neither can be changed from a computer.

Consequences for this change: there is no `session login` command, no password or code ever passes through the tool, and the credential-handling and device-fingerprint requirements are dropped. The `Fetcher` protocol and the `backend` columns stay (D3). A later change could add instagrapi or another backend if its login starts working. It would bring back `--backend`, a second session file and the login flow, and should be preceded by one manual login test.

### D6. Session files

- By default, the session file lives in the per-user data directory from `platformdirs`: `user_data_dir("igft")/sessions/instaloader.session`. On Ubuntu this is `~/.local/share/igft/sessions/`, and it respects `XDG_DATA_HOME`. The directory has mode `0700` and the file `0600`. The path can be overridden in the config (`instaloader_session_path`). The file name keeps the backend's name, so a later backend can add its own file beside it. *Alternative:* hard-coding XDG paths. Rejected, because it is wrong on macOS and ignores users who relocate their XDG directories.
- The file is written atomically: the tool creates a temporary file in the same directory with `os.open(..., 0o600)`, writes it, then `os.replace`s it over the old file. A crash during `session import` therefore never leaves a half-written session behind. On load, the tool warns if the permissions have become looser.

### D7. Data model

```
targets    id BIGINT PK (Instagram pk), username TEXT, added_at TIMESTAMPTZ

persons    id BIGINT PK (Instagram pk), username TEXT NOT NULL, full_name TEXT,
           is_private BOOL NULL, is_verified BOOL NULL,
           tags TEXT[] NOT NULL DEFAULT '{}', is_marked BOOL NOT NULL DEFAULT false,
           first_stored_at, updated_at TIMESTAMPTZ

follows    (target_id → targets, person_id → persons ON DELETE CASCADE) PK,
           first_seen_scan_id → scans, first_seen_at,
           last_seen_scan_id  → scans, last_seen_at

scans      id BIGSERIAL PK, target_id, backend, mode ('default'|'full'), is_baseline BOOL,
           status ('unfinished'|'complete'), stop_reason, started_at, ended_at,
           runs INT, pages_fetched INT, followers_seen INT, first_seen_followers INT,
           cursor TEXT NULL, signal_type NULL, signal_message NULL, error_message NULL
           UNIQUE (target_id) WHERE status = 'unfinished'   -- partial index

cooldowns  id, kind (signal type), raw_message, source_scan_id NULL, command,
           started_at, ends_at, requires_session_check BOOL

session_checks  id, backend, checked_at, succeeded BOOL, account_username NULL, message NULL
```

- **Why `tags TEXT[]`:** a native array is directly editable in common SQL clients (`'{lab,pilot}'`), is queryable with `'lab' = ANY(tags)`, and can take a GIN index later. *Alternatives:* JSONB is awkward to edit by hand, and a separate `person_tags` table contradicts the follower-records spec, which puts the `tags` column on `persons`.
- **Why the Instagram pk is the primary key** for `persons` and `targets`: it is stable across username changes, which the "latest values only" rule needs, and it is the same id any later backend would return.
- **Status is `unfinished` from the moment a scan row is created,** not `running`. A process killed with SIGKILL therefore leaves a scan that is correctly unfinished and resumable. `stop_reason` records why (`end_of_list`, `early_stop`, `page_cap`, `block_signal`, `error`, `list_unavailable`, `interrupted`). A scan killed hard has a null `stop_reason`, which `target list` shows as "interrupted". The partial unique index enforces "one unfinished scan per target" in the database, not only in code.
- **Migrations:** the schema is created and changed only through Alembic. `igft db upgrade` runs `alembic upgrade head` against the packaged migration scripts, so the user does not need an `alembic.ini` in their working directory.

### D8. Protecting user-owned columns

- **Writes:** there is exactly one function that writes `persons`. It is an `INSERT ... ON CONFLICT (id) DO UPDATE` whose `SET` list names only `username`, `updated_at`, and `full_name`, `is_private` and `is_verified` wrapped in `COALESCE(EXCLUDED.x, persons.x)`. A full name of `None` therefore keeps the stored name; the instaloader adapter maps an empty full name to `None`, so an empty name on the newest page is treated like a missing one. `tags` and `is_marked` appear nowhere in the tool's write statements. On a new insert they take their database defaults. A test scans the package source and fails if any write statement mentions either column. No repository function issues `DELETE` on `persons`.
- **Delete guard** (created in the initial migration):
  - A `BEFORE DELETE ... FOR EACH ROW` trigger raises an exception when `coalesce(cardinality(OLD.tags), 0) > 0 OR OLD.is_marked`. `coalesce` covers a user who sets `tags` to `NULL`.
  - A `BEFORE TRUNCATE` statement trigger raises if any tagged or marked person exists, because `TRUNCATE` bypasses row triggers.
  - `follows.person_id` uses `ON DELETE CASCADE`, so deleting an *untagged* person succeeds, as the spec requires, instead of failing on the foreign key.
- *Alternative considered:* a separate PostgreSQL role for the tool, with column-level `UPDATE` privileges that exclude `tags` and `is_marked`. It is a stronger guarantee, but it adds role setup to a single-user local install. It is left as an optional hardening step and not required.

### D9. Scan loop and page atomicity

For each page, `ScanService` does the following:

1. `pacer.wait()`. The first request of a run does not wait.
2. `safety.guard_request()`, then `fetcher.fetch_followers_page(target_id, scan.cursor)`. If the page has no followers, steps 3 and 4 do not run and the no-followers rule below applies.
3. **In one transaction:**
   - Find which of the page's person ids already have a `follows` row for this target with `first_seen_scan_id <> current_scan`. These are the "already known" followers.
   - Upsert the persons.
   - Upsert `follows`: insert with first-seen = last-seen = this scan, and on conflict update only `last_seen_*`.
   - Update the scan's `cursor = next_cursor`, `pages_fetched`, `followers_seen` and `first_seen_followers`.
   - Commit.
4. Evaluate the stop conditions: end of list, early stop (skipped for baseline and `--full` scans), or the page cap for this run.

- **No-followers rule:** a page with no followers is never the end of the list. Instagram answers a follower request for a private target the research account does not follow with a normal success and an empty list (D4), so an empty page cannot be told apart from an ended list by its shape. Treating it as `end_of_list` would record a baseline as complete with no followers, and the next scan would then be a non-baseline scan that reports every follower as first seen. `ScanService` therefore stops without committing a page, leaves the scan `unfinished` with its saved cursor, records `stop_reason = 'list_unavailable'` and the guidance "the follower list came back empty; if the target is private, make sure the research account follows it", sets no cooldown, and exits with code 5. The end of the list is a page that has followers and no next cursor. The rule needs no response parsing, so `FakeFetcher` tests cover it: an empty first page of a baseline scan, and an empty page after five saved pages. The same rule covers a target that was followed when added and later unfollowed, or that turned private. A target that really has no followers cannot be scanned, which costs nothing. `resume` after the follow is restored continues from the saved cursor, or from page 1 when the first page was the empty one.
- A page and its cursor therefore commit together or not at all. A crash between steps 2 and 3 loses only the uncommitted page, and the saved cursor still points at it, so `resume` refetches exactly that page.
- The "already known" check excludes the current scan, so followers first stored earlier in this scan, including before a `resume --restart`, never count toward early stop. This covers both the "known pages interrupted by a new follower" and "restarted baseline" scenarios.
- `first_seen_followers` is computed as a count of `follows.first_seen_scan_id = scan.id` rather than incremented, so it stays correct after a restart from page 1.
- The early-stop count lives in the running process only, so it starts at zero on every `resume`. A scan resumed after one fully known page therefore needs the full configured number of further known pages before it stops. This costs at most a few extra paced pages and avoids adding a column to `scans` just to carry the count across runs.
- **Baseline** is decided when a scan is created: the target has no scans at all. Baseline scans ignore early stop.
- **Resume** loads the unfinished scan and reuses `scan.mode`, increments `runs`, and continues from `scan.cursor`, or from page 1 with `--restart` (the service sets `cursor = NULL` first). The service never restarts on its own: a rejected cursor is a `FetchError` like any other, and the error message suggests `resume --restart`.
- **Ctrl-C:** `KeyboardInterrupt` is caught in the loop. The service records `stop_reason = 'interrupted'`, prints the resume hint, and exits non-zero.

### D10. Safety gate: one choke point for every request

- `SafetyGate.guard_command(cmd)` runs before a command's first request:
  - `scan`, `resume` and `target add` are refused if any cooldown has `ends_at > now()`.
  - They are also refused if any cooldown with `requires_session_check` has no successful `session_checks` row with `checked_at > cooldown.started_at`. The refusal message names whichever condition is still missing.
  - `session check` and `session import` are always allowed, because restoring and verifying a session is how a hold is lifted.
  - Cooldowns are global for the research account. They carry no backend, so a later second backend would be refused in the same way.
- `SafetyGate.record_signal(signal, scan)` runs in its own transaction, so it is saved even if the page transaction rolled back. It:
  - writes `signal_type`/`signal_message` on the scan, when there is one;
  - for rate limit and action block, inserts a cooldown with `ends_at = now + max(configured, stated_wait)`;
  - for challenge, inserts the same cooldown with `requires_session_check = true`;
  - for session rejected, inserts nothing;
  - returns the guidance text for the CLI to print.
- `stated_wait` is parsed from the raw message with a small set of patterns (for example "wait N minutes/hours/days"). If none match, the result is `None`, and the configured duration applies.
- **Single process:** every command that talks to Instagram takes a PostgreSQL session-level advisory lock (`pg_try_advisory_lock`). A second terminal gets "another igft command is using Instagram" instead of doubling the request rate.
- **No retries anywhere:** the gate, the scan loop and the adapters all let exceptions end the run. Retry-free behaviour is covered by adapter tests with mocked HTTP.

### D11. Pacer with injectable clock

`Pacer(min_s, max_s, rng, clock, sleep)` remembers when the previous request *started*. It sleeps until `last_start + uniform(min, max)`. Pacing from request start keeps the gap between requests within [min, max] even when the database commit takes time. It also means a slow commit is not added on top of the delay. `clock`, `sleep` and `rng` are injected, so the tests run instantly and can assert the bounds exactly. The config validator rejects `min > max` or negative values before any command that talks to Instagram starts.

### D12. Configuration

- A TOML file at `platformdirs.user_config_dir("igft")/config.toml` (on Ubuntu, `~/.config/igft/config.toml`, respecting `XDG_CONFIG_HOME`), read with the standard library `tomllib`. `--config <path>` or `IGFT_CONFIG` can point to another file. Environment variables prefixed `IGFT_` override it, for example `IGFT_DATABASE_URL`.
- The settings are: database URL, session path, Firefox profile path, instaloader user agent, delay minimum and maximum, page cap, early-stop page count, and cooldown hours.
- The values are validated into one frozen dataclass at startup. The config and sessions live outside the project directory, and the repository ships a `.gitignore` plus an example config with no secrets.

### D13. Reporting queries and time handling

- `list`: every `follows` row for the target, joined to `persons`.
- `first-seen` (default): rows with `first_seen_scan_id` = the target's latest scan. If that scan is the baseline, the result is empty with a "latest scan was the baseline" note. If it is unfinished, the output includes a "scan not complete" note.
- `--since <date>`: `first_seen_at >= <local midnight of date>`, excluding follows whose first-seen scan is the baseline.
- `--since-scan <id>`: first seen in a scan of the same target that started after the given scan. The tool errors if the scan belongs to another target.
- All timestamps are stored as `TIMESTAMPTZ` in UTC. Output shows local time, and JSON uses ISO 8601 with an offset. `--since` dates are interpreted in the local time zone, since that is how a user thinks about "since 1 September".
- The renderers take a list of row dataclasses. Table output uses Rich, CSV uses `csv.writer` with a header row, and JSON uses `json.dumps` with tags as a list and the mark as a boolean. The format is chosen by an enum, so an invalid `--format` fails in Typer before any query runs.

### D14. Exit codes

- `0`: success.
- `1`: user error, such as an unknown target, nothing to resume, or no saved session.
- `2`: usage error (Typer's default).
- `3`: refused by safety, because a cooldown or hold is active or another process holds the lock.
- `4`: the run stopped because of a block signal.
- `5`: the run stopped because of another fetch error, an empty follower list (`list_unavailable`, D9), or the page cap was reached, and it can be resumed.

Distinct codes let a user's own wrapper script react, for example by not retrying on 3 or 4.

### D15. Testing

- **Unit tests** cover the scan service, safety gate and pacer against a `FakeFetcher` that serves scripted pages, raises scripted signals, and counts requests. This covers early stop, baseline, page cap, `resume --restart`, "no retry", and "no request during cooldown" without any network access.
- **Database tests** run against a throwaway PostgreSQL database named by `IGFT_TEST_DATABASE_URL`, with the schema created by running the real Alembic migrations. They cover the upserts, the delete and truncate guards, and the partial unique index. SQLite is not an option, because arrays, triggers and `ON CONFLICT` behaviour differ.
- **Adapter tests** replay the spike's sanitised fixtures (`tests/fixtures/instaloader/`) and mocked HTTP responses through the adapter to check the classifier and the one-request-per-call guarantee.
- **Spike** (done before real scans): throwaway scripts in `spike/` ran each library against one small public target on the Ubuntu host. The instaloader spike recorded the endpoints, page size, cursor format, garbled-cursor behaviour and the request behind `session check`; the instagrapi spike recorded the blocked login. The findings are in `spike/findings.md` and are folded into D4, D5, Non-Goals and Risks.
- **Portability:** Firefox profile discovery and path resolution are unit-tested against fake directory trees for each platform layout (snap, Flatpak, classic Linux, macOS, Windows), with `sys.platform` and the home directory patched, so every layout is covered whichever OS runs the tests.

## Risks / Trade-offs

- **[Library internals change or conflict with the no-retry rule]** instaloader is unofficial and changes often, and the adapter uses its internal `context._session`. Mitigation: pin the exact version in `uv.lock`, keep all library calls in the adapter module, and cover retry-free behaviour with adapter tests that fail if a second HTTP call is made.
- **[The web app's requests change]** The profile page and follower request are copied from what the web app sent on 2026-10-02, and Instagram can change them with any web release. Mitigation: they are two small, isolated functions in the adapter. A changed response becomes a `FetchError` with the raw message, not a silent wrong result, and comparing against Firefox's Network panel is the documented way to update them. The GraphQL profile query the web app uses (`PolarisProfilePageContentQuery`, a `doc_id` plus page tokens) is deliberately not copied, because it changes more often.
- **[No fallback backend]** With instagrapi deferred (D5), a blocked or rejected web session stops all scanning until the user restores it. This is accepted: request safety already stops on the first signal, and a second account-wide session would not lower the risk to the research account.
- **[instaloader's cookie session presented with a mismatched user agent]** This can look like session theft and trigger a challenge. Mitigation: use a configurable user agent that matches Firefox by default, and re-import after any challenge.
- **[Firefox packaging varies by distribution]** Snap, Flatpak and distribution packages keep profiles in different places, and a user may have more than one installed. Mitigation: search all the known locations, refuse to guess when the result is ambiguous, and let `firefox_profile` in the config override discovery.
- **[A new Firefox login restricts the account for a few days]** When the research account first logged in from Firefox on the Ubuntu host, Instagram sent a "new login from a device you don't usually use" notice and restricted some security settings on that device for a few days. Follower requests were not affected. Mitigation: the README tells the user to confirm the login from a device the account already uses (the phone app) before the first import.
- **[Early stop misses followers when the list is not newest-first]** Mitigation: `--full` is available, and the README recommends a periodic `--full` scan. The early-stop page count can be configured higher.
- **[Long runs]** The web follower list returns 12 per page, so a 10k-follower target needs about 834 pages: 3 capped runs of up to 300 pages, roughly 7 hours of pacing at an average of 30 s. A baseline or `--full` scan of such a target therefore spans several runs, while an early-stopped scan usually needs only a few pages. This is accepted as the price of low volume. `resume` and page-level commits make multi-run scans routine rather than exceptional.
- **[A cursor stops working]** A rejected cursor stops the run as a `FetchError`, and the user resumes with `--restart`. On the web endpoint the cursor is an offset that is never rejected: an invalid one silently returns the start of the list, which costs extra pages but loses no data, because first-seen values are never overwritten.
- **[Access to a target's follower list is lost between scans]** A target can unfollow the research account or turn private after it was added, and the tool does not check this at `target add` (D4). Instagram then returns an empty list as a normal success, which looks like the end of a list. Mitigation: the no-followers rule in D9 stops the scan unfinished instead of recording it as complete, and the README tells the researcher to confirm the follow before adding a private target. Residual risk: if a real last page is ever empty, the rule stops a healthy scan at the end; two small targets (24 and 21 followers) did not show this (see Open Questions). That failure is visible and resumable, unlike a false complete scan.
- **[Clock skew or manual database edits confuse cooldowns]** Cooldown checks compare against the database's `now()`, not the local clock, and deleting a cooldown row by hand is the user's documented escape hatch.
- **[Protection of user columns relies on application code for updates]** Mitigation: a single write function and a source-scanning test. The optional column-privilege role (D8) is available if stronger guarantees are needed.

## Migration Plan

This is a new tool with no existing data. The setup order is:

1. `uv sync`
2. Create the database, then run `igft db upgrade`.
3. `igft session import` (Firefox logged in to Instagram with the research account).
4. `igft session check`
5. `igft target add <username>`. For a private target, make sure the research account follows it first, because the tool does not check.
6. `igft scan <username>`, which runs the baseline scan.

Rollback means uninstalling the package. The database and session files stay in place and belong to the user. Later migrations must be additive with respect to `persons.tags` and `persons.is_marked`, and any downgrade that would drop them must raise instead of running.

## Open Questions

Answered by the spike (`spike/findings.md`):

- ~~The exact follower-list endpoint and page size each backend gets today, and so how many capped runs a 10k-follower target needs.~~ The web endpoint `api/v1/friendships/<id>/followers/` with `count=12`, 12 per page, about 834 pages and 3 capped runs for 10k followers (D4, Risks). instagrapi was not measured, because its login is blocked (D5).
- ~~The exact response Instagram gives for an expired cursor on each backend.~~ No longer needed: the tool has no expired-cursor classifier. A rejected cursor is a `FetchError`, and the user restarts with `resume --restart` (D9). On the web endpoint an invalid cursor is not rejected at all: `max_id=12x` returned 200 with users from the start of the list.
- ~~Which single request instaloader's `test_login()` currently makes, to confirm that `session check` stays at one request.~~ One GraphQL request (`query_hash=d6f4427f…`), accepted, returning the account's username (D4).

Answered after the spike:

- ~~Private profile and follow status from the profile page.~~ The profile page has neither for the target (view-source check on three kinds of profile, 2026-10-03). `target add` stores only the id and username, and the researcher follows a private target before adding and scanning it (D4). A private profile the account does not follow returns a normal success with an empty list (`spike/private_probe.py`, 2026-10-03), which the scan stops on with `list_unavailable` (D9).
- ~~End of the follower list.~~ Measured with `spike/end_of_list_probe.py` on 2026-10-03, 2 requests after the profile page per target, on two targets the research account could see:
  - 24 followers: page 1 had 12 followers, `next_max_id` `"12"` and `has_more: true`; page 2 had 12 followers, no `next_max_id` key, `has_more: false` and `big_list: false`.
  - 21 followers: page 1 as above; page 2 had 9 followers, no `next_max_id` key, `has_more: false`, `big_list: false` and `page_size: 9`.
  - So the last page has followers and no next cursor, both when the count is an exact multiple of 12 (no trailing empty page) and when it is not. `FollowerPage.next_cursor` is `None` when `next_max_id` is missing or `has_more` is false, as D4 assumes, and the D9 no-followers rule holds for these two cases. A page can have fewer than `count` followers without being the last: the Firefox followers popup returned 11 users for `count=12` with `next_max_id: "12"` (spike/findings.md), so the adapter must not infer the end from a short page.
  - Limits: two small targets only. A long list could in principle end differently (for example an empty page after a cursor); task 14.4 records the last page of its scan as a check, and if a real last page is ever empty, the D9 rule has to be narrowed, for example to the first page only, and that is settled with the user.
  - Task 14.4 (2026-10-03, Ubuntu host, a third target with 77 followers and a page cap of 2, so 4 runs): 7 pages, the first 6 with 12 followers and the last with 5, and the scan stopped as `complete` with `end_of_list`. By D9 that means the last page had followers and no next cursor; a real empty last page would have stopped the scan as `list_unavailable`. The raw `next_max_id` and `has_more` were not logged, so which of the two ended the list is inferred from `FollowerPage.next_cursor`, not observed. The scan saw 77 followers but stored 71 distinct ones (6 repeats, about 1 in 13), within the reordering the Non-Goals accept. The cursor was null and `tags`/`is_marked` held their defaults (`{}`, false). The no-followers rule did not fire on a 7-page list, so it stays as it is.
