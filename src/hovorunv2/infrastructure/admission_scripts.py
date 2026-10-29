"""Atomic bounded admission and terminal capacity release; queue enqueue stays a separate phase."""

ADMIT_WORK = """
local p = cjson.decode(ARGV[1])
local duplicate = redis.call('GET',KEYS[2])
if duplicate then
  local old = redis.call('GET',p.ticket_prefix..duplicate)
  if not old then return {'unavailable'} end
  return {'duplicate',old}
end
if redis.call('EXISTS',KEYS[1]) == 1 then return {'conflict'} end
if p.envelope.deadline <= p.now then return {'expired'} end
local count = tonumber(redis.call('HGET',KEYS[3],'count') or '0')
local bytes = tonumber(redis.call('HGET',KEYS[3],'bytes') or '0')
local subject = tonumber(redis.call('GET',KEYS[4]) or '0')
local ingress = tonumber(redis.call('GET',KEYS[5]) or '0')
if count >= p.global_limit or bytes+p.byte_size > p.bytes_limit or p.byte_size > p.payload_limit
  or subject >= p.subject_limit or ingress >= p.ingress_limit then return {'full'} end
local existing = redis.call('GET',KEYS[6])
if existing and existing ~= p.blob then return {'conflict'} end
redis.call('SET',KEYS[6],p.blob,'EX',p.payload_ttl)
redis.call('HINCRBY',KEYS[3],'count',1)
redis.call('HINCRBY',KEYS[3],'bytes',p.byte_size)
redis.call('INCR',KEYS[4])
redis.call('INCR',KEYS[5])
local ticket = {
  envelope=p.envelope_json,
  state='accepted',
  stage='media',
  byte_size=p.byte_size,
  attempt=0,
  token='',
  published=0,
  not_before=0,
  subject_key=KEYS[4],
  ingress_key=KEYS[5],
  payload_key=KEYS[6],
  dedupe_key=KEYS[2]
}
local raw = cjson.encode(ticket)
redis.call('SET',KEYS[1],raw)
redis.call('SET',KEYS[2],p.envelope.job_id)
redis.call('ZADD',KEYS[7],p.envelope.deadline,p.envelope.job_id)
redis.call('ZADD',KEYS[8],p.envelope.deadline,p.envelope.job_id)
return {'accepted',raw}
"""

CLAIM_STAGE = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {} end
local ticket = cjson.decode(raw)
local envelope = cjson.decode(ticket.envelope)

if ticket.state ~= 'accepted' and ticket.state ~= 'enqueued' then return {} end
if ARGV[5] and ticket.envelope ~= ARGV[5] then return {} end

local now = tonumber(ARGV[2])
if now >= envelope.deadline then
  local count = tonumber(redis.call('HGET',KEYS[2],'count') or '0')
  local bytes = tonumber(redis.call('HGET',KEYS[2],'bytes') or '0')
  local subject = tonumber(redis.call('GET',ticket.subject_key) or '0')
  local ingress = tonumber(redis.call('GET',ticket.ingress_key) or '0')
  if count < 1 or bytes < ticket.byte_size or subject < 1 or ingress < 1 then return {} end
  redis.call('HINCRBY',KEYS[2],'count',-1)
  redis.call('HINCRBY',KEYS[2],'bytes',-ticket.byte_size)
  if ticket.subject_key and ticket.subject_key ~= '' then
    if redis.call('DECR',ticket.subject_key) <= 0 then redis.call('DEL',ticket.subject_key) end
  end
  if ticket.ingress_key and ticket.ingress_key ~= '' then
    if redis.call('DECR',ticket.ingress_key) <= 0 then redis.call('DEL',ticket.ingress_key) end
  end
  if ticket.payload_key and ticket.payload_key ~= '' then
    redis.call('DEL',ticket.payload_key)
  end
  redis.call('ZREM',KEYS[3],envelope.job_id)
  redis.call('ZREM',KEYS[4],envelope.job_id)
  ticket.state = 'terminal'
  ticket.outcome = 'expired'
  redis.call('SET',KEYS[1],cjson.encode(ticket),'EX',ARGV[4])
  if ticket.dedupe_key and ticket.dedupe_key ~= '' then
    redis.call('EXPIRE',ticket.dedupe_key,ARGV[4])
  end
  return {cjson.encode(ticket),'expired'}
end

if ticket.not_before and now < tonumber(ticket.not_before) then return {} end
if ticket.state ~= 'accepted' and ticket.state ~= 'enqueued' then return {} end
if ticket.stage ~= ARGV[1] then return {} end

ticket.state = 'running'
ticket.token = ARGV[3]
ticket.attempt = (ticket.attempt or 0) + 1
redis.call('SET',KEYS[1],cjson.encode(ticket))
redis.call('ZREM',KEYS[3],envelope.job_id)
return {cjson.encode(ticket),'claimed'}
"""

STAGE_HANDOFF = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {} end
local ticket = cjson.decode(raw)
if ticket.state ~= 'running' then return {} end
if ticket.token ~= ARGV[1] then return {'conflict'} end

if ticket.stage ~= 'media' or ARGV[2] ~= 'browser' then return {'conflict'} end
local envelope = cjson.decode(ticket.envelope)
ticket.state = 'accepted'
ticket.stage = ARGV[2]
ticket.token = ''
ticket.published = 0
ticket.publication_registered = 0
ticket.not_before = 0
redis.call('SET',KEYS[1],cjson.encode(ticket))
redis.call('ZADD',KEYS[2],envelope.deadline,envelope.job_id)
return {cjson.encode(ticket)}
"""

CANCEL_WORK = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {0} end
local ticket = cjson.decode(raw)
local envelope = cjson.decode(ticket.envelope)

if ticket.state == 'terminal' then
  return {0}
elseif ticket.state == 'running' then
  ticket.state = 'cancelling'
  redis.call('SET',KEYS[1],cjson.encode(ticket))
  return {1}
elseif ticket.state == 'accepted' or ticket.state == 'enqueued' then
  local count = tonumber(redis.call('HGET',KEYS[2],'count') or '0')
  local bytes = tonumber(redis.call('HGET',KEYS[2],'bytes') or '0')
  local subject = tonumber(redis.call('GET',ticket.subject_key) or '0')
  local ingress = tonumber(redis.call('GET',ticket.ingress_key) or '0')
  if count < 1 or bytes < ticket.byte_size or subject < 1 or ingress < 1 then return {} end
  redis.call('HINCRBY',KEYS[2],'count',-1)
  redis.call('HINCRBY',KEYS[2],'bytes',-ticket.byte_size)
  if ticket.subject_key and ticket.subject_key ~= '' then
    if redis.call('DECR',ticket.subject_key) <= 0 then redis.call('DEL',ticket.subject_key) end
  end
  if ticket.ingress_key and ticket.ingress_key ~= '' then
    if redis.call('DECR',ticket.ingress_key) <= 0 then redis.call('DEL',ticket.ingress_key) end
  end
  if ticket.payload_key and ticket.payload_key ~= '' then
    redis.call('DEL',ticket.payload_key)
  end
  redis.call('ZREM',KEYS[3],envelope.job_id)
  redis.call('ZREM',KEYS[4],envelope.job_id)
  ticket.state = 'terminal'
  ticket.outcome = 'cancelled'
  redis.call('SET',KEYS[1],cjson.encode(ticket),'EX',ARGV[1])
  if ticket.dedupe_key and ticket.dedupe_key ~= '' then
    redis.call('EXPIRE',ticket.dedupe_key,ARGV[1])
  end
  return {1}
end
return {0}
"""

TRANSITION_WORK = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {} end
local ticket = cjson.decode(raw)
local envelope = cjson.decode(ticket.envelope)
local operation = ARGV[1]
if operation == 'enqueued' then
  if ticket.state == 'accepted' and ticket.stage == ARGV[5] and ticket.attempt == tonumber(ARGV[6]) then
    ticket.state = 'enqueued'
    redis.call('SET',KEYS[1],cjson.encode(ticket))
    redis.call('ZREM',KEYS[3],envelope.job_id)
  end
  return {cjson.encode(ticket)}
elseif operation == 'terminal' then
  if ticket.state == 'terminal' then
    if ARGV[4] ~= '' and ARGV[4] ~= ticket.token then return {'wrong_owner'} end
    if ticket.outcome ~= ARGV[2] then return {'conflict'} end
    return {raw}
  end
  local token = ARGV[4] or ''
  if ticket.state ~= 'running' and ticket.state ~= 'cancelling' and token ~= '' then
    return {'wrong_owner'}
  end
  if (ticket.state == 'running' or ticket.state == 'cancelling') and ticket.token and ticket.token ~= '' then
    if token ~= ticket.token then return {'wrong_owner'} end
  end
  if ticket.subject_key and ticket.subject_key ~= '' and ticket.byte_size > 0 then
    local count = tonumber(redis.call('HGET',KEYS[2],'count') or '0')
    local bytes = tonumber(redis.call('HGET',KEYS[2],'bytes') or '0')
    local subject = tonumber(redis.call('GET',ticket.subject_key) or '0')
    local ingress = tonumber(redis.call('GET',ticket.ingress_key) or '0')
    if count < 1 or bytes < ticket.byte_size or subject < 1 or ingress < 1 then return {'unavailable'} end
    redis.call('HINCRBY',KEYS[2],'count',-1)
    redis.call('HINCRBY',KEYS[2],'bytes',-ticket.byte_size)
    if redis.call('DECR',ticket.subject_key) == 0 then redis.call('DEL',ticket.subject_key) end
    if redis.call('DECR',ticket.ingress_key) == 0 then redis.call('DEL',ticket.ingress_key) end
  end
  if ticket.payload_key and ticket.payload_key ~= '' then
    redis.call('DEL',ticket.payload_key)
  end
  redis.call('ZREM',KEYS[3],envelope.job_id)
  redis.call('ZREM',KEYS[4],envelope.job_id)
  ticket.state = 'terminal'
  ticket.outcome = ARGV[2]
  redis.call('SET',KEYS[1],cjson.encode(ticket),'EX',ARGV[3])
  if ticket.dedupe_key and ticket.dedupe_key ~= '' then
    redis.call('EXPIRE',ticket.dedupe_key,ARGV[3])
  end
  return {cjson.encode(ticket)}
end
return {}
"""

REGISTER_PUBLICATION = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {-2} end
local ticket = cjson.decode(raw)
if ticket.envelope ~= ARGV[1] then return {-1} end
if ticket.published == 1 or ticket.state ~= 'accepted' then return {0} end
local code = 2
if ticket.publication_registered ~= 1 then
  ticket.not_before = ARGV[2]
  ticket.publication_registered = 1
  code = 1
end
raw = cjson.encode(ticket)
redis.call('SET',KEYS[1],raw)
return {code,raw}
"""

SET_PUBLISHED = """
local raw = redis.call('GET',KEYS[1])
if not raw then return 0 end
local ticket = cjson.decode(raw)
if ticket.stage ~= ARGV[1] or ticket.attempt ~= tonumber(ARGV[2]) then return 0 end
if ticket.state == 'terminal' then return 0 end
ticket.published = 1
redis.call('SET',KEYS[1],cjson.encode(ticket))
return 1
"""
