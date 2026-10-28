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
local ticket = {envelope=p.envelope_json,state='accepted',byte_size=p.byte_size,
  subject_key=KEYS[4],ingress_key=KEYS[5],payload_key=KEYS[6],dedupe_key=KEYS[2]}
local raw = cjson.encode(ticket)
redis.call('SET',KEYS[1],raw)
redis.call('SET',KEYS[2],p.envelope.job_id)
redis.call('ZADD',KEYS[7],p.envelope.deadline,p.envelope.job_id)
redis.call('ZADD',KEYS[8],p.envelope.deadline,p.envelope.job_id)
return {'accepted',raw}
"""

TRANSITION_WORK = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {} end
local ticket = cjson.decode(raw)
local envelope = cjson.decode(ticket.envelope)
local operation = ARGV[1]
if operation == 'enqueued' then
  if ticket.state == 'accepted' then
    ticket.state = 'enqueued'
    redis.call('SET',KEYS[1],cjson.encode(ticket))
    redis.call('ZREM',KEYS[3],envelope.job_id)
  end
else
  if ticket.state == 'terminal' then
    if ticket.outcome ~= ARGV[2] then return {'conflict'} end
    return {raw}
  end
  local count = tonumber(redis.call('HGET',KEYS[2],'count') or '0')
  local bytes = tonumber(redis.call('HGET',KEYS[2],'bytes') or '0')
  local subject = tonumber(redis.call('GET',ticket.subject_key) or '0')
  local ingress = tonumber(redis.call('GET',ticket.ingress_key) or '0')
  if count < 1 or bytes < ticket.byte_size or subject < 1 or ingress < 1 then return {'unavailable'} end
  redis.call('HINCRBY',KEYS[2],'count',-1)
  redis.call('HINCRBY',KEYS[2],'bytes',-ticket.byte_size)
  if redis.call('DECR',ticket.subject_key) == 0 then redis.call('DEL',ticket.subject_key) end
  if redis.call('DECR',ticket.ingress_key) == 0 then redis.call('DEL',ticket.ingress_key) end
  redis.call('DEL',ticket.payload_key)
  redis.call('ZREM',KEYS[3],envelope.job_id)
  redis.call('ZREM',KEYS[4],envelope.job_id)
  ticket.state = 'terminal'
  ticket.outcome = ARGV[2]
  redis.call('SET',KEYS[1],cjson.encode(ticket),'EX',ARGV[3])
  redis.call('EXPIRE',ticket.dedupe_key,ARGV[3])
end
return {cjson.encode(ticket)}
"""
