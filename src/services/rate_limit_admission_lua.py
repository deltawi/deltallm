from src.services.redis_lua import RedisLuaScript
from src.services.output_limit_lua import OUTPUT_ADMISSION_LUA

RATE_LIMIT_SCRIPT = r"""
local n = #KEYS
for i = 1, n do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  local amount = tonumber(ARGV[i]) or 0
  local limit = tonumber(ARGV[n + i]) or 0
  if current + amount > limit then
    return {0, i}
  end
end
local results = {1, 0}
for i = 1, n do
  local amount = tonumber(ARGV[i]) or 0
  local ttl = tonumber(ARGV[(2 * n) + i]) or 60
  local new_val = redis.call('INCRBY', KEYS[i], amount)
  redis.call('EXPIRE', KEYS[i], ttl)
  results[i + 2] = new_val
end
return results
"""

RATE_LIMIT_OUTPUT_LUA = RedisLuaScript(
    OUTPUT_ADMISSION_LUA
    + RATE_LIMIT_SCRIPT.replace("local n = #KEYS", "local n = #KEYS - output_n")
    .replace(
        "local results = {1, 0}",
        "if not output_result_fits(n, 0) then return output_error() end\nlocal results = {1, 0}",
    )
    .replace("return results", "return output_commit(results)")
)
