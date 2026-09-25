# Set rate limits

Rate limits control how many requests or tokens can be used during a period. Start with the
application key, then add shared team or organization limits only when you need them.

## Choose where to set the limit

| Goal | Set the limit on |
| --- | --- |
| Control one application | Application key |
| Share a limit across several keys | Team |
| Cap all traffic for one customer or tenant | Organization |
| Protect one provider deployment | Model deployment |

The first three limits control callers. A model deployment limit protects provider capacity and is
configured separately.

## Set an application limit

1. Open **API Keys** and create or edit a key.
2. Set only the limits you need:
   - **RPM** for requests per minute
   - **TPM** for tokens per minute
   - **RPH** for requests per hour
   - **RPD** for requests per day
   - **TPD** for tokens per day
3. Save the key.
4. Send a small test burst and inspect the rate-limit headers in the response.

An unset field does not limit that time period. A request that exceeds a caller limit receives a
`429 Too Many Requests` response.

## Add a shared limit

Set the same fields on a team or organization when several keys should share one cap. Lower scopes
remain subject to the limits above them, so verify both an allowed request and a request that should
be rejected.

## Learn more

- [Rate-limit behavior and response headers](../features/rate-limiting.md)
- [Application keys](../admin-ui/api-keys.md)
- [Teams](../admin-ui/teams.md)
- [Organizations](../admin-ui/organizations.md)
