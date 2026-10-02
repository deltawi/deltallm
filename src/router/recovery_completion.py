"""Compose the existing health and release transitions in one Redis operation."""

from src.router.attempt_scripts import _ATTEMPT_RELEASE_SCRIPT
from src.router.health_state import HEALTH_SUCCESS_SCRIPT

RECOVERY_COMPLETION_SCRIPT = (
    "local function recover(KEYS, ARGV)\n"
    + HEALTH_SUCCESS_SCRIPT
    + "\nend\nlocal function release(KEYS, ARGV)\n"
    + _ATTEMPT_RELEASE_SCRIPT
    + "\nend\n"
    + """
-- router_recovery_completion_v1
local server_time = redis.call('TIME')
local now_ms = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
local expires = tonumber(redis.call('ZSCORE', KEYS[6], ARGV[1]))
local transition = {0, 0, 0, 'recoverable'}
-- Both the attempt and recovery token must still be owned. The existing
-- health transition also fences manual cooldowns and replacement owners.
if expires and expires > now_ms then
  transition = recover(
    {KEYS[1], KEYS[2], KEYS[3], KEYS[4]},
    {server_time[1], ARGV[1], ARGV[2]}
  )
end
release({KEYS[5], KEYS[6], KEYS[4]}, {ARGV[1]})
return transition
"""
)
