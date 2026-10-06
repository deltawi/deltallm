"""Read recorded output at admission and account completed provider attempts."""

from src.services.redis_lua import RedisLuaScript

OUTPUT_BUCKET_LUA = r"""
local function output_bucket(key, current_window)
  local kind = redis.call('TYPE', key).ok
  if kind == 'none' then return {0, 0} end
  if kind ~= 'hash' then return nil end
  local fields = redis.call('HMGET', key, 'window_id', 'used', 'unknown')
  local window, used, unknown = tonumber(fields[1]), tonumber(fields[2]), tonumber(fields[3])
  if not window or window < 0 or window ~= math.floor(window) or window > current_window or
     not used or used < 0 or used > 2147483647 or used ~= math.floor(used) or
     (unknown ~= 0 and unknown ~= 1) then return nil end
  if window < current_window then return {0, 0} end
  return {used, unknown}
end
"""

OUTPUT_ADMISSION_LUA = (
    OUTPUT_BUCKET_LUA
    + r"""
-- output_admission_v2; output arguments and bucket keys follow normal admission.
local output_n = tonumber(ARGV[#ARGV])
if not output_n or output_n < 1 or output_n > 4 or output_n ~= math.floor(output_n) or
   #KEYS > 128 or #ARGV > 1024 or #KEYS < output_n then
  return {0, 'output_unavailable'}
end
local output_key_offset = #KEYS - output_n
local output_arg_offset = #ARGV - output_n - 1
local output_now = tonumber(redis.call('TIME')[1])
local output_window = math.floor(output_now / 60)
local output_reset = (output_window + 1) * 60
local output_values = {}
local output_unknown = {}

local function output_error() return {0, 'output_unavailable'} end
local function output_result_fits(rate_n, fair_n)
  return 512 + rate_n * 32 + fair_n * 400 + output_n * 32 <= 4096
end

for i = 1, output_n do
  local state = output_bucket(KEYS[output_key_offset + i], output_window)
  local limit = tonumber(ARGV[output_arg_offset + i])
  if not state or not limit or limit < 1 or limit > 2147483647 or
     limit ~= math.floor(limit) then return output_error() end
  output_values[i] = state[1]
  output_unknown[i] = state[2]
end
-- Validate every bucket before selecting a denial. Unknown state takes precedence.
for i = 1, output_n do
  if output_unknown[i] == 1 then
    return {0, 'output_unknown', i, output_values[i], tonumber(ARGV[output_arg_offset + i]),
            output_window, output_reset, output_now}
  end
end
for i = 1, output_n do
  local limit = tonumber(ARGV[output_arg_offset + i])
  if output_values[i] >= limit then
    return {0, 'output', i, output_values[i], limit, output_window, output_reset, output_now}
  end
end

local function output_commit(result)
  result[#result + 1] = 'output_v2'
  result[#result + 1] = output_window
  result[#result + 1] = output_reset
  for i = 1, output_n do result[#result + 1] = output_values[i] end
  return result
end
"""
)

OUTPUT_ACCOUNTING_SCRIPT = (
    OUTPUT_BUCKET_LUA
    + r"""
-- output_accounting_v2; one receipt per completed attempt, across all caller scopes.
local n = #KEYS - 1
local actual = tonumber(ARGV[2])
if n < 1 or n > 4 or #ARGV ~= 2 or #ARGV[1] ~= 64 or
   not actual or actual < -1 or actual > 2147483647 or actual ~= math.floor(actual) then
  return {0}
end
local receipt = KEYS[#KEYS]
local kind = redis.call('TYPE', receipt).ok
if kind ~= 'none' and kind ~= 'string' then return {0} end
local saved = redis.call('GET', receipt)
local now = tonumber(redis.call('TIME')[1])
local window = math.floor(now / 60)
if saved then
  if #saved > 4096 then return {0} end
  local ok, record = pcall(cjson.decode, saved)
  if not ok or type(record) ~= 'table' or record.fingerprint ~= ARGV[1] or
     record.actual ~= actual or type(record.result) ~= 'table' then return {0} end
  local result = record.result
  if #result ~= 4 + 2*n or result[1] ~= 1 or type(result[2]) ~= 'number' or
     result[2] < 0 or result[2] > window or result[2] ~= math.floor(result[2]) or
     result[3] ~= (result[2] + 1)*60 or (result[4] ~= 0 and result[4] ~= 1) then
    return {0}
  end
  for i = 1, n do
    local used, unknown = result[4+i], result[4+n+i]
    if type(used) ~= 'number' or used < 0 or used > 2147483647 or used ~= math.floor(used) or
       (unknown ~= 0 and unknown ~= 1) then return {0} end
  end
  return result
end
local reset = (window + 1)*60
local result = {1, window, reset, 0}
for i = 1, n do
  local state = output_bucket(KEYS[i], window)
  if not state then return {0} end
  local used = state[1]
  if actual >= 0 then
    if used + actual > 2147483647 then result[4] = 1 end
    used = math.min(2147483647, used + actual)
  end
  result[4+i] = used
  result[4+n+i] = actual == -1 and 1 or state[2]
end
local encoded = cjson.encode({fingerprint=ARGV[1], actual=actual, result=result})
if #encoded > 4096 then return {0} end
-- Validate all state and serialization before writes. Redis/OOM failure remains ambiguous.
for i = 1, n do
  redis.call('HSET', KEYS[i], 'window_id', window, 'used', result[4+i],
             'unknown', result[4+n+i])
  redis.call('EXPIREAT', KEYS[i], reset + 30)
end
redis.call('SET', receipt, encoded, 'EX', 120)
return result
"""
)

OUTPUT_ACCOUNTING_LUA = RedisLuaScript(OUTPUT_ACCOUNTING_SCRIPT)
