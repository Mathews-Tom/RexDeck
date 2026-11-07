package.path = arg[1] .. '/?.lua;' .. package.path
local adapter = require('host')
local child = {pid = 101, path = '/python'}
local token = 'generation-a'
local blocks = {{block_id = 'preview-1'}}
local closes, opens = {}, 0
local pending_exec = false
rex = {
  exec = function()
    if pending_exec then
      child, pending_exec = {pid = child.pid, path = '/python'}, false
    end
    if not token then return {ok = false, stdout = ''} end
    return {ok = true, stdout = token}
  end,
  json = {decode = function(value) return {ok = true, identity = value} end},
}
local app = {
  args = {python = '/python', rex = '/rex', server = 'unix:///owned.sock', bat = '/bat'},
  dir = arg[1],
  fail = function(_, message) error(message) end,
  rex = function(method, args)
    if method == 'session.list_blocks' then return {blocks = blocks} end
    if method == 'com.superlogical.terminal.process' then return {child = child} end
    if method == 'block.close' then closes[#closes + 1] = args.block_id; return {} end
    if method == 'session.new_split' then opens = opens + 1; return {block_ids = {'new-' .. opens}} end
    return {}
  end,
}
local ctx = {context = {session_id = 'session-a', source_block_id = 'origin-a'}, source = {pwd = '/root'}}
local function instance()
  local h = adapter.new(app)
  h.files = function() return {root = '/root', path = '/root/file'} end
  h.previews['session-a/origin-a'] = {block = 'preview-1', pid = 101, path = '/python', identity = 'generation-a'}
  return h
end

local function preserved(name, changed_child, changed_token, changed_blocks)
  child, token, blocks = changed_child, changed_token, changed_blocks
  local before = #closes
  local h = instance()
  h.preview(ctx, '/root/file')
  assert(#closes == before, name .. ' closed a pane it did not prove owned')
end
preserved('PID replaced', {pid = 202, path = '/python'}, 'generation-a', {{block_id = 'preview-1'}})
preserved('PID reused', {pid = 101, path = '/python'}, 'generation-b', {{block_id = 'preview-1'}})
preserved('Executable replaced', {pid = 101, path = '/editor'}, 'generation-a', {{block_id = 'preview-1'}})
preserved('Uncertain identity', {pid = 101, path = '/python'}, nil, {{block_id = 'preview-1'}})
preserved('Pane gone', {pid = 101, path = '/python'}, 'generation-a', {})
child, token, blocks = {pid = 101, path = '/python'}, 'generation-a', {{block_id = 'preview-1'}}
local h = instance()
h.preview(ctx, '/root/file')
assert(#closes == 1 and closes[1] == 'preview-1', 'verified matching generation was not replaced')
local other = {context = {session_id = 'session-a', source_block_id = 'origin-b'}, source = {pwd = '/root'}}
h.preview(other, '/root/file')
assert(#closes == 1, 'a second origin closed the first origin preview')

-- Rex reports its launcher until the preview process completes exec.
local launched = adapter.new(app)
launched.files = function() return {root = '/root', path = '/root/file'} end
child, token, blocks, pending_exec = {pid = 303, path = '/rex'}, 'preview-generation', {}, true
launched.preview(ctx, '/root/file')
local first = 'new-' .. opens
blocks = {{block_id = first}}
launched.preview(ctx, '/root/file')
assert(closes[#closes] == first, 'launch-time exec transition prevented owned replacement')
