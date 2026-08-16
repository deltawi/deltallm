# Settings

Settings control the global runtime behavior of the gateway.

![Settings](images/settings.png)

## Main sections

- **General**: runtime log level
- **Theme**: instance name, simple and expanded logos, favicon, primary and secondary action colours, and menu hover colour
- **Routing & Reliability**: default strategy, retries, timeouts, and cooldowns
- **Fallback Chains**: explicit fallback mappings
- **Recent Fallback Events**: operational fallback review
- **Caching**: cache enablement, backend, and TTL
- **Health Checks**: background probe behavior

## When to use this page

Use Settings for platform-wide defaults. Do not use it for per-group routing behavior; that belongs in [Route Groups](route-groups.md).

The Theme tab is available only to platform administrators (and the master-key break-glass session). Changes include an in-page preview and take effect immediately after save. **Discard changes** restores the last saved name and colours without modifying the database. Simple logos, expanded logos, and favicons are uploaded directly as PNG, JPEG, WebP, SVG, or ICO files up to 2 MB (ICO is favicon-only). Uploaded files are validated, stored as database BLOBs, and cached in memory by every replica. **Replace** and **Remove** take effect immediately. Button and branded-text foregrounds, surfaces, and hover colours are derived automatically to preserve visible, accessible controls. If a wordmark, mark, or favicon fails to load, the UI retries or falls back to the next safe built-in presentation. See [General Settings](../configuration/general.md#ui-branding) for persistence and replica behavior.

Authentication and onboarding controls such as SSO, invitations, and self-registration sandbox defaults are configured in `general_settings`, not from this page. See [General Settings](../configuration/general.md#self-registration-settings) and [Authentication & SSO](../features/authentication.md#self-service-sandbox-registration) for the self-registration sandbox flow.

## Good operating pattern

- Keep global defaults conservative
- Use route groups for workload-specific routing
- Use this page only for shared runtime behavior that should apply across the gateway
