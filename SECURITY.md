# Security Policy

## Supported versions

Security fixes target the latest published release and the current `main` branch. Older releases are not maintained separately. Check the [latest release](https://github.com/viamus/voice-to-me/releases/latest) before reporting a problem.

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/viamus/voice-to-me/security/advisories/new) to contact the repository maintainers. Do not disclose an unpatched vulnerability in a public issue or pull request.

Include the affected version or commit, relevant settings, steps to reproduce, expected impact and any proposed fix. Use synthetic audio or text for a proof of concept, and remove credentials, private messages, writing styles and identifying paths from attachments. Do not test against another person's machine or account.

Maintainers can discuss the report and coordinate a fix with you in the private advisory. Public disclosure should follow that coordination. This project does not offer a guaranteed response time or a bug bounty.

For normal bugs, setup problems and feature requests, use the [issue forms](https://github.com/viamus/voice-to-me/issues/new/choose). For conduct concerns, see the [Code of Conduct](CODE_OF_CONDUCT.md).

## Data handling

Voice to Me keeps recorded audio and session history in memory. Codex refinement passes the transcription and writing style to Codex CLI when enabled. Diagnostics exclude dictated text and writing-style instructions. See [the architecture and data flow](docs/architecture.md) for details relevant to a report.
