# Security Policy

## Reporting

Report vulnerabilities privately through the repository's security advisory feature. Do not open a public issue containing exploit details or credentials.

## Deployment considerations

- The Docker socket grants privileged control over the host daemon. Run only trusted controller images and restrict host access.
- Use a Cloudflare token scoped to Zone DNS Edit and Zone Read for only the required zones.
- Store the API token outside source control and rotate it if it appears in logs, shell history, or an image layer.
- Workload containers need labels only. Never pass the Cloudflare token to workloads.
- The controller does not adopt unmarked DNS records and defaults to skipping target mismatches.