"""Shared clock and retention rules for token-owned concurrency leases."""

PARALLEL_LEASE_LUA = """
local function parallel_now_ms()
  local server_time = redis.call('TIME')
  return tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
end

local function expire_parallel_owners(key)
  -- The key contains independent owners, possibly with different lease TTLs.
  -- Retain all of them until the last owner's expiry; membership scores still
  -- determine which owners count towards admission.
  local latest = redis.call('ZREVRANGE', key, 0, 0, 'WITHSCORES')
  if #latest >= 2 then
    redis.call('PEXPIREAT', key, math.ceil(tonumber(latest[2])))
  end
end
"""
