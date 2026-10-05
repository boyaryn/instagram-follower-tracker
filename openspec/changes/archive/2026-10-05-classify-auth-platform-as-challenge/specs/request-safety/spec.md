## MODIFIED Requirements

### Requirement: Signal classification and recording
The tool SHALL classify every block signal as one of: rate limit (HTTP 429 or a "please wait" message), challenge or checkpoint (including a redirect to Instagram's verification page, which may ask for nothing more than a confirmation that the user is human), action block (`feedback_required`), or session rejected. The signal type and Instagram's raw message SHALL be recorded on the scan, when there is one, and on any cooldown it causes. The tool SHALL tell the user which signal occurred and what to do next.

#### Scenario: Action block
- **WHEN** Instagram responds with `feedback_required`
- **THEN** the scan records the signal as an action block with Instagram's message, and the tool shows the type, the message, and when scanning can resume

#### Scenario: Challenge guidance
- **WHEN** Instagram responds with a challenge or checkpoint
- **THEN** the tool tells the user to verify the account by hand (for example in Firefox), restore the session if needed, and then run `session check`

#### Scenario: Redirect to the verification page
- **WHEN** Instagram answers a follower-page request with a redirect to its verification page (`/auth_platform/`)
- **THEN** the scan records the signal as a challenge with the redirect as Instagram's message, no further request is made, a cooldown and a challenge hold are set, and the tool gives the challenge guidance
