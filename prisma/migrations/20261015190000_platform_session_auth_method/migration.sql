-- Record how a browser session signed in. The login flow uses this value to
-- decide if it shows the MFA enrollment prompt; SSO sessions do not get it.
-- Sessions that exist before the upgrade are recorded as password sessions,
-- which keeps their current behavior until the user signs in again.
ALTER TABLE "deltallm_platformsession"
ADD COLUMN "auth_method" TEXT NOT NULL DEFAULT 'password'
CONSTRAINT "deltallm_platformsession_auth_method_check"
CHECK ("auth_method" IN ('password', 'sso'));
