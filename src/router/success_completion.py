"""Complete one provider attempt with the existing fenced health transitions."""

from src.router.attempt_scripts import _ATTEMPT_RELEASE_SCRIPT
from src.router.health_state import HEALTH_SUCCESS_SCRIPT

SUCCESS_COMPLETION_SCRIPT = (
    "local function recover(KEYS, ARGV)\n"
    + HEALTH_SUCCESS_SCRIPT
    + "\nend\nlocal function release(KEYS, ARGV)\n"
    + _ATTEMPT_RELEASE_SCRIPT
    + "\nend\n"
    + """
-- router_attempt_success_completion_v2
local server_time = redis.call('TIME')
local now_ms = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
local expires = tonumber(redis.call('ZSCORE', KEYS[6], ARGV[1]))
local transition = {0, 0, 0, 'recoverable'}
if not expires then
  return transition
end
-- An expired owner must not recover health or clear a new owner's cooldown.
if expires > now_ms then
  transition = recover(
    {KEYS[1], KEYS[2], KEYS[3], KEYS[4]},
    {server_time[1], ARGV[2], ARGV[3]}
  )
end
redis.call('ZADD', KEYS[7], tonumber(ARGV[4]), ARGV[6])
redis.call('ZREMRANGEBYSCORE', KEYS[7], 0, tonumber(ARGV[5]))
redis.call('PEXPIRE', KEYS[7], tonumber(ARGV[7]))
local usage_count = tonumber(ARGV[8]) or 0
for index = 1, usage_count do
  local increment = tonumber(ARGV[8 + index]) or 0
  if increment > 0 then
    redis.call('INCRBY', KEYS[7 + index], increment)
    redis.call('EXPIRE', KEYS[7 + index], 120)
  end
end
release({KEYS[5], KEYS[6], KEYS[4]}, {ARGV[1]})
return transition
"""
)
