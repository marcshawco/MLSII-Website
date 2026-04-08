# Private Local Data

Put sensitive personal files in this folder.

Git ignores everything in `private/` except this README, so you can keep
local-only data here without committing it.

Example `private/links.json`:

```json
{
  "m1_referral_url": ""
}
```

Production deployments should use environment variables when possible.
