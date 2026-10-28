"""Atomic publication receipts and exclusive execution claims for Celery feature messages."""

REGISTER_FEATURE = """
local old = redis.call('HGET',KEYS[1],'envelope')
if old then
    if old ~= ARGV[1] then return -1 end
    if redis.call('HGET',KEYS[1],'published') == '1'
        or redis.call('HGET',KEYS[1],'state') ~= 'pending' then return 0 end
    return 2
end
redis.call('HSET',KEYS[1],'envelope',ARGV[1],'state','pending','published','0','not_before',ARGV[2])
return 1
"""

CLAIM_FEATURE = """
if redis.call('HGET',KEYS[1],'envelope') ~= ARGV[1] then return 0 end
if redis.call('HGET',KEYS[1],'state') ~= 'pending' then return 0 end
if tonumber(ARGV[2]) >= tonumber(ARGV[3]) then
    redis.call('HSET',KEYS[1],'state','terminal','outcome','expired')
    redis.call('EXPIRE',KEYS[1],ARGV[5])
    return 0
end
if tonumber(ARGV[2]) < tonumber(redis.call('HGET',KEYS[1],'not_before')) then return 0 end
redis.call('HSET',KEYS[1],'state','running','token',ARGV[4])
return 1
"""

FINISH_FEATURE = """
if redis.call('HGET',KEYS[1],'token') ~= ARGV[1] then return 0 end
local state = redis.call('HGET',KEYS[1],'state')
if state == 'terminal' then return redis.call('HGET',KEYS[1],'outcome') == ARGV[2] and 1 or 0 end
if state ~= 'running' and state ~= 'cancelling' then return 0 end
redis.call('HSET',KEYS[1],'state','terminal','outcome',ARGV[2])
redis.call('EXPIRE',KEYS[1],ARGV[3])
return 1
"""

CANCEL_FEATURE = """
local state = redis.call('HGET',KEYS[1],'state')
if not state or state == 'terminal' then return 0 end
if state == 'pending' then
    redis.call('HSET',KEYS[1],'state','terminal','outcome','cancelled')
    redis.call('EXPIRE',KEYS[1],ARGV[1])
else
    redis.call('HSET',KEYS[1],'state','cancelling')
end
return 1
"""
