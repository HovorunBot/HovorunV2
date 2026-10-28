"""Atomic single-Valkey execution budget operations; no payload contents enter these scripts."""

RESERVE_BUDGET = """
local p = cjson.decode(ARGV[1])
local old = redis.call('GET', KEYS[1])
if old then
  local r = cjson.decode(old)
  if r.fingerprint ~= p.fingerprint then return {'conflict'} end
  if r.state == 'reserved' and p.now >= p.deadline then return {'expired'} end
  return {r.state}
end
if p.deadline <= p.now then return {'expired'} end
local retry = p.now
local quotas = {}
for i,q in ipairs(p.quotas) do
  if q.amount > q.burst or q.amount > q.limit then return {'impossible'} end
  local used = math.max(tonumber(redis.call('HGET', q.key, 'used') or '0'), q.baseline)
  local held = tonumber(redis.call('HGET', q.key, 'held') or '0')
  local tokens = tonumber(redis.call('HGET', q.rate, 'tokens') or tostring(q.burst))
  local at = tonumber(redis.call('HGET', q.rate, 'at') or tostring(p.now))
  tokens = math.min(q.burst, tokens + math.max(0,p.now-at)*q.limit/q.seconds)
  if used + held + q.amount > q.limit then retry = math.max(retry,q.ends) end
  if tokens < q.amount then retry = math.max(retry,p.now+(q.amount-tokens)*q.seconds/q.limit) end
  quotas[i] = {used=used, held=held, tokens=tokens}
end
for _,c in ipairs(p.concurrency) do
  if redis.call('SCARD',c.key) >= c.limit then retry = math.max(retry,p.now+1) end
end
if retry > p.now then return {'waiting',tostring(retry)} end
for i,q in ipairs(p.quotas) do
  redis.call('HSET',q.key,'used',string.format('%.0f',quotas[i].used),'held',string.format('%.0f',quotas[i].held+q.amount))
  redis.call('HSET',q.rate,'tokens',quotas[i].tokens-q.amount,'at',p.now)
  redis.call('EXPIRE',q.key,math.ceil(q.ends-p.now+p.retention))
  redis.call('EXPIRE',q.rate,math.ceil(q.seconds+p.retention))
end
for _,c in ipairs(p.concurrency) do redis.call('SADD',c.key,p.id) end
p.state = 'reserved'
redis.call('SET',KEYS[1],cjson.encode(p))
redis.call('ZADD',KEYS[2],p.deadline,p.id)
return {'reserved'}
"""

FINISH_BUDGET = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {'unavailable'} end
local p = cjson.decode(raw)
local actual = cjson.decode(ARGV[1])
local now = tonumber(ARGV[2])
for unit,estimated in pairs(p.estimates) do
  local amount = actual[unit]
  if amount == nil or amount < 0 or amount > estimated or amount ~= math.floor(amount) then return {'conflict'} end
end
for unit,_ in pairs(actual) do if p.estimates[unit] == nil then return {'conflict'} end end
if p.state ~= 'reserved' then
  for unit,amount in pairs(actual) do if p.actual[unit] ~= amount then return {'conflict'} end end
  return {p.state}
end
for _,q in ipairs(p.quotas) do
  if redis.call('EXISTS',q.key) == 0 or redis.call('EXISTS',q.rate) == 0 then return {'unavailable'} end
end
local consumed = false
for _,amount in pairs(actual) do if amount > 0 then consumed = true end end
for _,q in ipairs(p.quotas) do
  local held = tonumber(redis.call('HGET',q.key,'held'))
  local used = tonumber(redis.call('HGET',q.key,'used'))
  redis.call('HSET',q.key,'held',string.format('%.0f',held-q.amount),'used',string.format('%.0f',used+actual[q.unit]))
  local tokens = tonumber(redis.call('HGET',q.rate,'tokens'))
  local at = tonumber(redis.call('HGET',q.rate,'at'))
  tokens = math.min(q.burst,tokens+math.max(0,now-at)*q.limit/q.seconds+q.amount-actual[q.unit])
  redis.call('HSET',q.rate,'tokens',tokens,'at',now)
end
for _,c in ipairs(p.concurrency) do redis.call('SREM',c.key,p.id) end
p.actual = actual
p.state = consumed and 'committed' or 'released'
redis.call('SET',KEYS[1],cjson.encode(p),'EX',p.retention)
redis.call('ZREM',KEYS[2],p.id)
return {p.state}
"""
