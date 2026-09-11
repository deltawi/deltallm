_ATTEMPT_ADMISSION_SCRIPT = """
-- router_attempt_admission_v2
local redis_time = redis.call('TIME')
local now_ms = (tonumber(redis_time[1]) * 1000) + math.floor(tonumber(redis_time[2]) / 1000)
local capacity_count = tonumber(ARGV[1]) or 0
local lease_ttl_ms = tonumber(ARGV[2]) or 1000
local legacy_compat_ttl_ms = tonumber(ARGV[3]) or lease_ttl_ms
local owner_token = ARGV[4]

local expired = redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)
local current = tonumber(redis.call('GET', KEYS[1]) or '0') or 0
current = math.max(0, current - expired)
local owner_count = redis.call('ZCARD', KEYS[2])
current = math.max(current, owner_count)

local active_ttl_ms = redis.call('PTTL', KEYS[1])
if current <= 0 then
  redis.call('DEL', KEYS[1])
elseif expired > 0 then
  redis.call('SET', KEYS[1], current)
  if active_ttl_ms > 0 then
    redis.call('PEXPIRE', KEYS[1], active_ttl_ms)
  end
end

if redis.call('EXISTS', KEYS[3]) == 1 then
  return {0, 'cooldown', 0}
end

local healthy = redis.call('HGET', KEYS[4], 'healthy')
local concurrency = tonumber(ARGV[6 + capacity_count]) or 0
if tonumber(ARGV[7 + capacity_count * 2]) == 1 then
  local existing_expiry = redis.call('ZSCORE', KEYS[2], owner_token)
  if existing_expiry then
    return {1, 'acquired', current, tonumber(existing_expiry), 0}
  end
end
if concurrency > 0 and current >= concurrency then
  return {0, 'capacity', 0}
end
for index = 1, capacity_count do
  local usage = tonumber(redis.call('GET', KEYS[5 + index]) or '0')
  local limit = tonumber(ARGV[4 + index])
  local consume = tonumber(ARGV[6 + capacity_count + index]) or 0
  if usage >= limit or usage + consume > limit then
    return {0, 'capacity', 0}
  end
end

local recovery = 0
if healthy == 'false' then
  if redis.call('HGET', KEYS[4], 'recovery_required') ~= 'true' then
    return {0, 'unhealthy', 0}
  end
  local claimed = redis.call('SET', KEYS[5], owner_token, 'NX', 'PX', lease_ttl_ms)
  if not claimed then
    return {0, 'recovery_in_progress', 0}
  end
  recovery = 1
end

local expires_at_ms = now_ms + lease_ttl_ms
for index = 1, capacity_count do
  local consume = tonumber(ARGV[6 + capacity_count + index]) or 0
  if consume > 0 then
    redis.call('INCRBY', KEYS[5 + index], consume)
    redis.call('EXPIRE', KEYS[5 + index], 120)
  end
end
redis.call('ZADD', KEYS[2], expires_at_ms, owner_token)
local active = current + 1
redis.call('SET', KEYS[1], active)

local latest = redis.call('ZREVRANGE', KEYS[2], 0, 0, 'WITHSCORES')
local key_expires_at_ms = expires_at_ms
if #latest >= 2 then
  key_expires_at_ms = math.max(key_expires_at_ms, tonumber(latest[2]))
end
if current > owner_count then
  key_expires_at_ms = math.max(key_expires_at_ms, now_ms + legacy_compat_ttl_ms)
end
key_expires_at_ms = key_expires_at_ms + tonumber(ARGV[5 + capacity_count])
redis.call('PEXPIREAT', KEYS[1], key_expires_at_ms)
redis.call('PEXPIREAT', KEYS[2], key_expires_at_ms)
return {1, 'acquired', active, expires_at_ms, recovery}
"""

_ATTEMPT_RELEASE_SCRIPT = """
-- router_attempt_release_v2
local removed = redis.call('ZREM', KEYS[2], ARGV[1])
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local owner_count = redis.call('ZCARD', KEYS[2])
if removed == 1 then
  current = math.max(0, current - 1)
end
current = math.max(current, owner_count)

if current <= 0 then
  redis.call('DEL', KEYS[1])
else
  local active_ttl_ms = redis.call('PTTL', KEYS[1])
  redis.call('SET', KEYS[1], current)
  if active_ttl_ms > 0 then
    redis.call('PEXPIRE', KEYS[1], active_ttl_ms)
  end
end
if owner_count == 0 then
  redis.call('DEL', KEYS[2])
end
if redis.call('GET', KEYS[3]) == ARGV[1] then
  redis.call('DEL', KEYS[3])
end
return current
"""

_ATTEMPT_ACTIVE_BATCH_SCRIPT = """
-- router_attempt_active_batch_v1
local redis_time = redis.call('TIME')
local now_ms = (tonumber(redis_time[1]) * 1000) + math.floor(tonumber(redis_time[2]) / 1000)
local deployment_count = math.floor(#KEYS / 2)
local results = {}

for index = 1, deployment_count do
  local active_key = KEYS[((index - 1) * 2) + 1]
  local owners_key = KEYS[((index - 1) * 2) + 2]
  local expired = redis.call('ZREMRANGEBYSCORE', owners_key, '-inf', now_ms)
  local current = tonumber(redis.call('GET', active_key) or '0') or 0
  current = math.max(0, current - expired)
  local owner_count = redis.call('ZCARD', owners_key)
  current = math.max(current, owner_count)

  if current <= 0 then
    redis.call('DEL', active_key)
  elseif expired > 0 then
    local active_ttl_ms = redis.call('PTTL', active_key)
    redis.call('SET', active_key, current)
    if active_ttl_ms > 0 then
      redis.call('PEXPIRE', active_key, active_ttl_ms)
    end
  end
  if owner_count == 0 then
    redis.call('DEL', owners_key)
  end
  results[index] = current
end

return results
"""
