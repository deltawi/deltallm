from __future__ import annotations

from src.services.redis_lua import RedisLuaScript

RATE_AND_FAIR_SHARE_SCRIPT = """
-- tier_admission_v2
local rate_n = tonumber(ARGV[1]) or 0
local fair_n = tonumber(ARGV[2]) or 0
local legacy_parallel_n = tonumber(ARGV[3]) or 0
local parallel_n = tonumber(ARGV[4]) or 0
local now_ms = tonumber(ARGV[5]) or 0
local parallel_expires_at_ms = tonumber(ARGV[6]) or 0
local parallel_ttl_seconds = tonumber(ARGV[7]) or 300
local cursor = 7

local rate_amounts = {}
local rate_limits = {}
local rate_ttls = {}
for i = 1, rate_n do
  rate_amounts[i] = tonumber(ARGV[cursor + i]) or 0
end
cursor = cursor + rate_n
for i = 1, rate_n do
  rate_limits[i] = tonumber(ARGV[cursor + i]) or 0
end
cursor = cursor + rate_n
for i = 1, rate_n do
  rate_ttls[i] = tonumber(ARGV[cursor + i]) or 60
end
cursor = cursor + rate_n

local legacy_parallel_limits = {}
for i = 1, legacy_parallel_n do
  legacy_parallel_limits[i] = tonumber(ARGV[cursor + i]) or 0
end
cursor = cursor + legacy_parallel_n

local parallel_limits = {}
local parallel_requested = {}
for i = 1, parallel_n do
  parallel_limits[i] = tonumber(ARGV[cursor + i]) or 0
end
cursor = cursor + parallel_n
for i = 1, parallel_n do
  parallel_requested[i] = tonumber(ARGV[cursor + i]) or 0
end
cursor = cursor + parallel_n

local parallel_tokens = {}
local parallel_token_count = 0
for i = 1, parallel_n do
  parallel_token_count = parallel_token_count + parallel_requested[i]
end
for i = 1, parallel_token_count do
  parallel_tokens[i] = ARGV[cursor + i]
end
cursor = cursor + parallel_token_count

for i = 1, rate_n do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0') or 0
  if current + rate_amounts[i] > rate_limits[i] then
    return {0, 'rate', i}
  end
end

local legacy_parallel_key_offset = rate_n
for i = 1, legacy_parallel_n do
  local key = KEYS[legacy_parallel_key_offset + i]
  local current = tonumber(redis.call('GET', key) or '0') or 0
  if current < 0 then
    current = 0
  end
  if current + 1 > legacy_parallel_limits[i] then
    return {0, 'parallel', 'legacy', i}
  end
end

local parallel_key_offset = rate_n + legacy_parallel_n
for i = 1, parallel_n do
  local key = KEYS[parallel_key_offset + i]
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms)
  local current = redis.call('ZCARD', key)
  if current + parallel_requested[i] > parallel_limits[i] then
    return {0, 'parallel', 'lease', i}
  end
end

local fair_results = {}
local fair_commits = {}
local staged_fair_counters = {}
local staged_active_states = {}
local staged_active_order = {}

local function fair_counter_value(key)
  local staged_value = staged_fair_counters[key]
  if staged_value == nil then
    staged_value = tonumber(redis.call('GET', key) or '0') or 0
    staged_fair_counters[key] = staged_value
  end
  return staged_value
end

local function stage_fair_counter_increment(key, amount)
  local next_value = fair_counter_value(key) + amount
  staged_fair_counters[key] = next_value
  return next_value
end

local function active_state_for(active_key, weight_key, active_count_key, total_weight_key, cleanup_lag_key, fair_now_ms, cleanup_limit)
  local state = staged_active_states[active_key]
  if state ~= nil then
    return state
  end

  local active_count = tonumber(redis.call('GET', active_count_key) or '0') or 0
  local total_weight = tonumber(redis.call('GET', total_weight_key) or '0') or 0
  local expired_orgs = redis.call('ZRANGEBYSCORE', active_key, '-inf', fair_now_ms, 'LIMIT', 0, cleanup_limit)
  local cleanup_lagged = 0
  if cleanup_limit > 0 and #expired_orgs >= cleanup_limit then
    cleanup_lagged = 1
  end

  local removed_orgs = {}
  local removed_count = 0
  local staged_scores = {}
  local staged_weights = {}
  for _, org_id in ipairs(expired_orgs) do
    local expired_weight = tonumber(redis.call('HGET', weight_key, org_id) or '0') or 0
    if expired_weight > 0 then
      total_weight = total_weight - expired_weight
    end
    active_count = active_count - 1
    removed_count = removed_count + 1
    removed_orgs[removed_count] = org_id
    staged_scores[org_id] = 0
    staged_weights[org_id] = 0
  end

  if active_count < 0 then
    active_count = 0
  end
  if total_weight < 0 then
    total_weight = 0
  end

  state = {
    active_key = active_key,
    weight_key = weight_key,
    active_count_key = active_count_key,
    total_weight_key = total_weight_key,
    cleanup_lag_key = cleanup_lag_key,
    active_count = active_count,
    total_weight = total_weight,
    cleanup_lagged = cleanup_lagged,
    cleanup_lag_at = fair_now_ms,
    active_ttl_seconds = 1,
    removed_orgs = removed_orgs,
    removed_count = removed_count,
    scores = staged_scores,
    weights = staged_weights,
    touched_orgs = {},
    touched_flags = {},
    touched_count = 0,
  }
  staged_active_states[active_key] = state
  staged_active_order[#staged_active_order + 1] = state
  return state
end

local function staged_active_score(state, organization_id)
  local score = state.scores[organization_id]
  if score == nil then
    score = tonumber(redis.call('ZSCORE', state.active_key, organization_id) or '0') or 0
  end
  return score
end

local function staged_active_weight(state, organization_id)
  local weight = state.weights[organization_id]
  if weight == nil then
    weight = tonumber(redis.call('HGET', state.weight_key, organization_id) or '0') or 0
  end
  return weight
end

local function stage_active_organization(
  active_key,
  weight_key,
  active_count_key,
  total_weight_key,
  cleanup_lag_key,
  fair_now_ms,
  cleanup_limit,
  organization_id,
  effective_weight,
  expires_at_ms,
  active_ttl_seconds
)
  local state = active_state_for(
    active_key,
    weight_key,
    active_count_key,
    total_weight_key,
    cleanup_lag_key,
    fair_now_ms,
    cleanup_limit
  )
  if active_ttl_seconds > state.active_ttl_seconds then
    state.active_ttl_seconds = active_ttl_seconds
  end

  local previous_score = staged_active_score(state, organization_id)
  local previous_weight = staged_active_weight(state, organization_id)
  if previous_score > fair_now_ms then
    state.total_weight = state.total_weight - previous_weight + effective_weight
  else
    state.active_count = state.active_count + 1
    state.total_weight = state.total_weight + effective_weight
  end

  if state.active_count < 1 then
    state.active_count = 1
  end
  if state.total_weight < effective_weight then
    state.total_weight = effective_weight
  end

  if state.touched_flags[organization_id] == nil then
    state.touched_count = state.touched_count + 1
    state.touched_orgs[state.touched_count] = organization_id
    state.touched_flags[organization_id] = 1
  end
  state.scores[organization_id] = expires_at_ms
  state.weights[organization_id] = effective_weight
  return state
end

local function commit_active_states()
  for _, state in ipairs(staged_active_order) do
    for i = 1, state.removed_count do
      local org_id = state.removed_orgs[i]
      redis.call('ZREM', state.active_key, org_id)
      redis.call('HDEL', state.weight_key, org_id)
    end
    for i = 1, state.touched_count do
      local org_id = state.touched_orgs[i]
      redis.call('ZADD', state.active_key, state.scores[org_id], org_id)
      redis.call('HSET', state.weight_key, org_id, tostring(state.weights[org_id]))
    end
    redis.call('SET', state.active_count_key, state.active_count)
    redis.call('SET', state.total_weight_key, state.total_weight)
    redis.call('EXPIRE', state.active_key, state.active_ttl_seconds)
    redis.call('EXPIRE', state.weight_key, state.active_ttl_seconds)
    redis.call('EXPIRE', state.active_count_key, state.active_ttl_seconds)
    redis.call('EXPIRE', state.total_weight_key, state.active_ttl_seconds)
    if state.cleanup_lagged == 1 then
      redis.call('SET', state.cleanup_lag_key, tostring(state.cleanup_lag_at))
      redis.call('EXPIRE', state.cleanup_lag_key, state.active_ttl_seconds)
    end
  end
end

local function evaluate_fair_share(index, key_offset, arg_offset)
  local active_key = KEYS[key_offset + 1]
  local weight_key = KEYS[key_offset + 2]
  local active_count_key = KEYS[key_offset + 3]
  local total_weight_key = KEYS[key_offset + 4]
  local rpm_pool_key = KEYS[key_offset + 5]
  local rpm_org_key = KEYS[key_offset + 6]
  local tpm_pool_key = KEYS[key_offset + 7]
  local tpm_org_key = KEYS[key_offset + 8]
  local boost_key = KEYS[key_offset + 9]
  local usage_rank_key = KEYS[key_offset + 10]
  local cleanup_lag_key = KEYS[key_offset + 11]
  local limit_hit_heatmap_key = KEYS[key_offset + 12]
  local limit_hit_rank_key = KEYS[key_offset + 13]
  local limit_hit_total_key = KEYS[key_offset + 14]
  local fair_now_ms = tonumber(ARGV[arg_offset + 1]) or 0
  local active_ttl_ms = tonumber(ARGV[arg_offset + 2]) or 10000
  local organization_id = ARGV[arg_offset + 3]
  local base_weight = tonumber(ARGV[arg_offset + 4]) or 1000
  local saturation_threshold = tonumber(ARGV[arg_offset + 5]) or 0.85
  local burst_multiplier = tonumber(ARGV[arg_offset + 6]) or 1
  local rpm_capacity = tonumber(ARGV[arg_offset + 7]) or 0
  local rpm_amount = tonumber(ARGV[arg_offset + 8]) or 0
  local tpm_capacity = tonumber(ARGV[arg_offset + 9]) or 0
  local tpm_amount = tonumber(ARGV[arg_offset + 10]) or 0
  local window_seconds = tonumber(ARGV[arg_offset + 11]) or 60
  local strategy = ARGV[arg_offset + 12] or 'weighted_fair'
  local cleanup_limit = tonumber(ARGV[arg_offset + 13]) or 64
  local limit_hit_field_prefix = ARGV[arg_offset + 14] or ''
  local tier_key = ARGV[arg_offset + 15] or 'none'
  local active_ttl_seconds = math.ceil(active_ttl_ms / 1000)
  if active_ttl_seconds < 1 then
    active_ttl_seconds = 1
  end
  local boost_multiplier = tonumber(redis.call('GET', boost_key) or '1') or 1
  if boost_multiplier < 1 then
    boost_multiplier = 1
  end
  local effective_weight = math.floor(base_weight * boost_multiplier)
  if effective_weight < 1 then
    effective_weight = 1
  end
  local expires_at_ms = fair_now_ms + active_ttl_ms
  local active_state = stage_active_organization(
    active_key,
    weight_key,
    active_count_key,
    total_weight_key,
    cleanup_lag_key,
    fair_now_ms,
    cleanup_limit,
    organization_id,
    effective_weight,
    expires_at_ms,
    active_ttl_seconds
  )
  local active_count = active_state.active_count
  local total_weight = active_state.total_weight
  local cleanup_lagged = active_state.cleanup_lagged
  local function record_limit_hit(scope)
    local field = limit_hit_field_prefix .. scope .. '|' .. tier_key
    redis.call('HINCRBY', limit_hit_heatmap_key, field, 1)
    redis.call('ZINCRBY', limit_hit_rank_key, 1, field)
    redis.call('INCR', limit_hit_total_key)
    redis.call('EXPIRE', limit_hit_heatmap_key, window_seconds)
    redis.call('EXPIRE', limit_hit_rank_key, window_seconds)
    redis.call('EXPIRE', limit_hit_total_key, window_seconds)
  end
  local function check_dimension(pool_key, org_key, capacity, amount, scope)
    if capacity <= 0 or amount <= 0 then
      return {1, scope, 'not_configured', 0, 0, capacity, 0, 0}
    end
    local pool_current = fair_counter_value(pool_key)
    local org_current = fair_counter_value(org_key)
    local next_pool = pool_current + amount
    local saturation = next_pool / capacity
    local share_multiplier = 1
    if strategy == 'reserved_burst' then
      share_multiplier = burst_multiplier
    end
    local share_limit = math.max(1, math.floor((capacity * effective_weight * share_multiplier) / total_weight))
    if share_limit > capacity then
      share_limit = capacity
    end
    if next_pool > capacity then
      return {0, scope, 'pool_capacity_exceeded', pool_current, org_current, capacity, share_limit, saturation}
    end
    if cleanup_lagged == 1 then
      return {1, scope, 'cleanup_lagged', pool_current, org_current, capacity, share_limit, saturation}
    end
    if saturation > saturation_threshold and org_current + amount > share_limit then
      return {0, scope, 'weighted_share_exceeded', pool_current, org_current, capacity, share_limit, saturation}
    end
    return {1, scope, 'allowed', pool_current, org_current, capacity, share_limit, saturation}
  end
  local rpm = check_dimension(rpm_pool_key, rpm_org_key, rpm_capacity, rpm_amount, 'tier_pool_fair_share_rpm')
  if rpm[1] == 0 then
    record_limit_hit(rpm[2])
    return {0, 'fair', index, rpm[1], rpm[2], rpm[3], active_count, total_weight / 1000, effective_weight / 1000, rpm[4], rpm[5], rpm[6], rpm[7], rpm[8], 'rpm'}
  end
  local rpm_org_total = rpm[5]
  if rpm_capacity > 0 and rpm_amount > 0 then
    stage_fair_counter_increment(rpm_pool_key, rpm_amount)
    rpm_org_total = stage_fair_counter_increment(rpm_org_key, rpm_amount)
  end

  local tpm = check_dimension(tpm_pool_key, tpm_org_key, tpm_capacity, tpm_amount, 'tier_pool_fair_share_tpm')
  if tpm[1] == 0 then
    record_limit_hit(tpm[2])
    return {0, 'fair', index, tpm[1], tpm[2], tpm[3], active_count, total_weight / 1000, effective_weight / 1000, tpm[4], tpm[5], tpm[6], tpm[7], tpm[8], 'tpm'}
  end
  local tpm_org_total = tpm[5]
  if tpm_capacity > 0 and tpm_amount > 0 then
    stage_fair_counter_increment(tpm_pool_key, tpm_amount)
    tpm_org_total = stage_fair_counter_increment(tpm_org_key, tpm_amount)
  end

  local final_reason = 'allowed'
  if rpm[3] == 'cleanup_lagged' or tpm[3] == 'cleanup_lagged' then
    final_reason = 'cleanup_lagged'
  end
  local selected = rpm
  local selected_dimension = 'rpm'
  if tpm[8] > rpm[8] then
    selected = tpm
    selected_dimension = 'tpm'
  end
  fair_results[index] = {1, selected[2], final_reason, active_count, total_weight / 1000, effective_weight / 1000, selected[4], selected[5], selected[6], selected[7], selected[8], selected_dimension}
  fair_commits[index] = {
    rpm_pool_key,
    rpm_org_key,
    tpm_pool_key,
    tpm_org_key,
    usage_rank_key,
    organization_id,
    rpm_capacity,
    rpm_amount,
    tpm_capacity,
    tpm_amount,
    window_seconds,
    rpm_org_total,
    tpm_org_total,
  }
  return nil
end

local fair_key_offset = rate_n + legacy_parallel_n + parallel_n
for fair_index = 1, fair_n do
  local failure = evaluate_fair_share(
    fair_index,
    fair_key_offset + ((fair_index - 1) * 14),
    cursor + ((fair_index - 1) * 15)
  )
  if failure ~= nil then
    return failure
  end
end

local results = {1, rate_n, fair_n}
for i = 1, rate_n do
  local amount = rate_amounts[i]
  local ttl = rate_ttls[i]
  local new_val = redis.call('INCRBY', KEYS[i], amount)
  redis.call('EXPIRE', KEYS[i], ttl)
  results[3 + i] = new_val
end

for i = 1, legacy_parallel_n do
  local key = KEYS[legacy_parallel_key_offset + i]
  local next_value = (tonumber(redis.call('GET', key) or '0') or 0) + 1
  redis.call('SET', key, next_value)
  redis.call('EXPIRE', key, parallel_ttl_seconds)
end

local token_index = 0
for i = 1, parallel_n do
  local key = KEYS[parallel_key_offset + i]
  local requested = parallel_requested[i]
  for _ = 1, requested do
    token_index = token_index + 1
    redis.call('ZADD', key, parallel_expires_at_ms, parallel_tokens[token_index])
  end
  redis.call('EXPIRE', key, math.ceil((parallel_expires_at_ms - now_ms) / 1000))
end

commit_active_states()

local result_index = 3 + rate_n
for fair_index = 1, fair_n do
  local commit = fair_commits[fair_index]
  local rpm_org_total = commit[12]
  local tpm_org_total = commit[13]
  if commit[7] > 0 and commit[8] > 0 then
    redis.call('INCRBY', commit[1], commit[8])
    rpm_org_total = redis.call('INCRBY', commit[2], commit[8])
    redis.call('EXPIRE', commit[1], commit[11])
    redis.call('EXPIRE', commit[2], commit[11])
  end
  if commit[9] > 0 and commit[10] > 0 then
    redis.call('INCRBY', commit[3], commit[10])
    tpm_org_total = redis.call('INCRBY', commit[4], commit[10])
    redis.call('EXPIRE', commit[3], commit[11])
    redis.call('EXPIRE', commit[4], commit[11])
  end
  local usage_score = rpm_org_total + tpm_org_total
  if usage_score > 0 then
    redis.call('ZADD', commit[5], usage_score, commit[6])
    redis.call('EXPIRE', commit[5], commit[11])
  end
  local fair_result = fair_results[fair_index]
  for i = 1, 12 do
    result_index = result_index + 1
    results[result_index] = fair_result[i]
  end
end
return results
"""

RATE_AND_FAIR_SHARE_LUA = RedisLuaScript(RATE_AND_FAIR_SHARE_SCRIPT)
