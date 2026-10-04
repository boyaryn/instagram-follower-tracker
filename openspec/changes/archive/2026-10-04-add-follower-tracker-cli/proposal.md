# Proposal

## Why

For research, I need a reliable local record of who follows a small set of Instagram profiles (up to 10, each under ~10k followers), and of when each follower first appeared. Instagram's official APIs do not return follower lists, and paid data providers are out of scope. That leaves a careful, low-volume, manually run tool that reads follower lists through a dedicated research account. It must be conservative enough to minimise the risk of action blocks or bans, and it must store results in PostgreSQL, where I can tag and mark followers in my own SQL client.

## What Changes

The exact behaviour is defined in the specs under `specs/`; the technical approach is in design.md.

- New local, single-user Python CLI, run manually, with no paid services.
- **Instagram access** through a dedicated research account:
  - `session import` takes the research account's web session from Firefox. The tool never asks for a password and never logs in automatically. If the saved session expires or is rejected, it stops and asks you to log in to Instagram in Firefox and import the session again.
  - The scraping library is **instaloader**. **instagrapi** was planned as a second library but is out of scope for this change, because its login is blocked by Instagram (design.md D5).
- **Target management**: `target add <username>` registers a profile (one profile request), and `target list` shows the targets. Targets cannot be removed. The tool does not check whether a private target is followed by the research account, so you make sure it is before adding and scanning one (design.md D4).
- **Follower scanning** of one target per run, using only the paginated follower list:
  - The first scan of a target is a baseline. Later scans stop early after a few pages of already-known followers, unless `--full` is given.
  - Followers are saved page by page, so an interrupted scan keeps its progress. `resume` continues it, and `resume --restart` starts it over from page 1.
  - An empty page is never treated as the end of the list, because Instagram answers the same way for a private target the account does not follow.
- **Request safety**: randomised delays, a cap on pages per run, and an immediate stop with no retry on any block or challenge signal. Each signal is classified and recorded and comes with guidance. After a rate limit or action block the research account has a 24 h cooldown, and after a challenge it stays on hold until the cooldown has passed and `session check` succeeds. A rejected session sets no cooldown. The durations are configurable.
- **Follower records** in PostgreSQL:
  - A person is stored once by numeric Instagram ID, with the latest values of the fields the follower list returns (no profile picture URL).
  - Each follow relationship between a person and a target records when it was first and last seen. Unfollows and refollows are not tracked.
  - **User-owned annotations**: free-form tags and a yes/no mark on each person, which you edit in your SQL client. The tool never writes them, and the database refuses to delete a tagged or marked person.
- **Reporting**: `list` shows all followers of a target, and `first-seen` shows the followers the tool saw for the first time in the latest scan, or since a date or scan. "First seen" means the first scan that recorded the person, not when they started following. Output is a terminal table, CSV or JSON, and each row includes the person's tags and mark.

## Capabilities

### New Capabilities
- `instagram-access`: importing the research account's web session from Firefox, no password and no automatic login, checking the session, and protecting the saved session file.
- `target-management`: adding and listing the profiles whose followers are tracked.
- `follower-scanning`: scanning one target per run, with a baseline first scan, early stop, `--full`, page-by-page saving, and `resume` / `resume --restart`.
- `request-safety`: delays, the page cap, stopping on block signals, signal classification, the cooldown and challenge hold, and the no-cooldown rule for a rejected session.
- `follower-records`: what is stored about persons, follow relationships and scans, and the protection of user-owned tags and marks.
- `follower-reporting`: the `list` and `first-seen` commands, what counts as first seen, output formats and row contents.

### Modified Capabilities
<!-- None: the project has no existing specs. -->

## Impact

- **New codebase**: a Python package with a CLI entry point, a configuration file plus environment variables (the database URL and the saved session file stay out of version control), and Alembic migrations.
- **Dependencies**: `instaloader`, a PostgreSQL driver and SQLAlchemy/Alembic, plus a CLI and table-output library, all free and open-source.
- **External systems**:
  - The user's existing local PostgreSQL instance.
  - Instagram, accessed with the dedicated research account's session. Scraping breaks Instagram's Terms of Service. The research account carries the risk of an action block or ban, which the request-safety measures reduce but cannot remove.
- **Data protection**: the tool stores personal data of third parties. Collection is limited to the fields the follower list returns, excluding profile picture URLs, and whoever runs the tool is responsible for meeting the research ethics and data protection rules that apply to them (for example GDPR).
- **Risks** (mitigations and measurements in design.md, Risks / Trade-offs):
  - Instagram's web requests are unofficial and can change at any time, and there is no fallback library: if the web session stops working, scanning stops until it is restored.
  - The follower list returns 12 followers per page, so a 10k-follower target needs about 834 pages, or 3 capped runs.
  - Early stop assumes follower lists come roughly newest-first, which is not guaranteed.
  - The list can reorder between page requests, so a scan may repeat or miss followers. This is accepted, not corrected.
  - A target can turn private, or unfollow the research account, between scans. The scan then stops unfinished instead of recording a false end of the list.
