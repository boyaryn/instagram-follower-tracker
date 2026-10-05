# Tasks

## 1. Classify the verification redirect

- [x] 1.1 Add a `redirect to the verification page` case (302, `Location: https://www.instagram.com/auth_platform/?apc=abc`, expected `SignalKind.CHALLENGE`) to `RESPONSES` in `tests/test_instaloader_adapter.py`, and verify it fails before the fix (`uv run pytest tests/test_instaloader_adapter.py -k verification`).
- [x] 1.2 Add a test that a login redirect carrying `next=/auth_platform/` is still a rejected session (D1), next to the existing `next=/challenge/` test, and verify it passes.
- [x] 1.3 Extend the challenge path match in `classify()` to include `auth_platform` (D1) and verify tasks 1.1 and 1.2 pass and the whole `tests/test_instaloader_adapter.py` still passes.
- [x] 1.4 Add a sentence to the challenge row of the README "Request safety" table saying the verification can be as small as a "confirm you are human" box with no email or SMS (D2), and verify `uv run pytest tests/test_readme_commands.py tests/test_readme_sql.py` still passes.

## 2. Integration check

- [x] 2.1 Run the full suite (`uv run pytest`) and verify it passes, then run `openspec validate classify-auth-platform-as-challenge` and verify it reports the change valid.
