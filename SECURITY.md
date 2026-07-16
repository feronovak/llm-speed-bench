# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or leaked credential.
Use GitHub's private vulnerability reporting feature for this repository. If it
is unavailable, contact the repository maintainers privately.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Do not include active API keys.

## Credential and data handling

- API keys belong in environment variables or an uncommitted
  `.env.production` file.
- The CLI does not execute `.env.production` as shell code.
- Catalog output removes custom request headers.
- The local catalogue capability ledger records model compatibility outcomes and
  safe request options, not probe response text or credentials; it is created
  with owner-only permissions where the platform supports them.
- Result artifacts redact every custom request-header value.
- Result files can contain prompts, provider errors, model metadata, and
  optionally full responses. Treat `results/` as potentially sensitive.
- Treat benchmark JSON as trusted input. It controls outbound HTTP(S)
  destinations, custom headers, prompts, and provider-specific request fields.
  Do not run configurations from untrusted sources without reviewing them.
- Custom provider URLs must use HTTP or HTTPS, include a host, and may not
  embed credentials.
- Revoke and rotate a credential immediately if it is committed or included in
  logs. Removing it from Git history is not sufficient by itself.
