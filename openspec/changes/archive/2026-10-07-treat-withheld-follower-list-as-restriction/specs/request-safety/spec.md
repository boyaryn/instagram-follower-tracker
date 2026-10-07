# Spec Delta

## MODIFIED Requirements

### Requirement: Signal classification and recording
The tool SHALL classify every block signal as one of: rate limit (HTTP 429, a "please wait" message, or a follower list withheld from the research account, which Instagram signals by answering a follower-page request with a redirect to its home page or, after followers were already saved, with an empty page), challenge or checkpoint (including a redirect to Instagram's verification page, which may ask for nothing more than a confirmation that the user is human), action block (`feedback_required`), or session rejected. The signal type and Instagram's raw message SHALL be recorded on the scan, when there is one, and on any cooldown it causes. The tool SHALL tell the user which signal occurred and what to do next.

#### Scenario: Action block
- **WHEN** Instagram responds with `feedback_required`
- **THEN** the scan records the signal as an action block with Instagram's message, and the tool shows the type, the message, and when scanning can resume

#### Scenario: Challenge guidance
- **WHEN** Instagram responds with a challenge or checkpoint
- **THEN** the tool tells the user to verify the account by hand (for example in Firefox), restore the session if needed, and then run `session check`

#### Scenario: Redirect to the verification page
- **WHEN** Instagram answers a follower-page request with a redirect to its verification page (`/auth_platform/`)
- **THEN** the scan records the signal as a challenge with the redirect as Instagram's message, no further request is made, a cooldown and a challenge hold are set, and the tool gives the challenge guidance

#### Scenario: Redirect to the home page
- **WHEN** Instagram answers a follower-page request with a redirect to its home page
- **THEN** the scan records the signal as a rate limit with the redirect as Instagram's message, no further request is made and the request is not retried, a cooldown is set, and the scan stays unfinished with its cursor

#### Scenario: Withheld list guidance
- **WHEN** a scan stops because the follower list is being withheld from the research account
- **THEN** the tool says that Instagram is withholding the follower list from the account, shows when the cooldown ends, and tells the user to wait, to check in Firefox that a followers list opens before resuming, and not to restart the scan from page 1 to get past it

### Requirement: Errors that are not block signals
Any other failure of an Instagram request, such as a network error or an unrecognised response, SHALL stop the run without retrying and without setting a cooldown. Its raw message SHALL be recorded on the scan, and the scan SHALL stay resumable. A redirect to Instagram's home page in answer to a follower-page request is not such a failure; it is a block signal.

#### Scenario: Network failure
- **WHEN** a page request fails because the network is unavailable
- **THEN** the run stops, the error is recorded, no cooldown is set, and `resume` can continue the scan

#### Scenario: Follower list not visible
- **WHEN** a page request succeeds but returns no followers, as it does for a private target the research account does not follow, and the scan has saved no followers yet
- **THEN** the run stops as defined by follower scanning, no cooldown is set, and the scan stays resumable

#### Scenario: Other redirect
- **WHEN** Instagram answers a follower-page request with a redirect to a page that is not its verification, login, checkpoint or home page
- **THEN** the run stops, the redirect is recorded as the error, no cooldown is set, and the scan stays resumable
