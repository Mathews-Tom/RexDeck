-- State-boundary tests: independent panes, frozen origins and failure atomicity.
local install = assert(loadfile(arg[1] .. '/main.lua'))()
assert(type(install) == 'function')
local action, mode, context
local panes, focused, opened, effects
local function reset()
  panes = {
    ['origin-a'] = {session = 'session-a', window = 'window-a'},
    ['origin-b'] = {session = 'session-b', window = 'window-b'},
  }
  focused, opened, effects = 'origin-b', 0, 0
  mode = 'normal'
  context = {session_id = 'session-a', block_id = 'origin-a'}
end
reset()
rex = {
  action = function(value) action = value end,
  call = function(method, request)
    effects = effects + 1
    if method == 'session.attach' then
      if mode == 'attach-error' then return nil, 'attachment denied' end
      -- A callback may yield while another pane becomes current.
      context.session_id, context.block_id = 'session-b', 'origin-b'
      return {}
    end
    assert(method == 'com.superlogical.terminal.pwd')
    local source = panes[request.block_id]
    if not source or source.session ~= request.session_id then return nil, 'source not found' end
    if mode == 'pwd-error' then return nil, 'terminal unavailable' end
    if mode == 'no-pwd' then return {} end
    return {pwd = 'file:///exact%20origin'}
  end,
  exec = function()
    effects = effects + 1
    if mode == 'exec-error' then return nil, 'python unavailable' end
    if mode == 'refused-cwd' then return {ok = false, stderr = 'remote cwd refused'} end
    return {ok = true, stdout = '{"cwd":"/exact origin"}'}
  end,
  json = {decode = function()
    if mode == 'invalid-json' then return nil, 'invalid JSON' end
    if mode == 'null-json' then return nil end
    if mode == 'relative-cwd' then return {cwd = 'relative'} end
    if mode == 'missing-cwd' then return {} end
    return {cwd = '/exact origin'}
  end},
  session = {new_split = function(request)
    effects = effects + 1
    local source = panes[request.anchor_id]
    if mode == 'split-error' then return nil, 'split denied' end
    if not source or source.session ~= request.session_id then return nil, 'invalid source' end
    opened = opened + 1
    local id = 'yazi-' .. opened
    panes[id] = {session = source.session, window = source.window,
      cwd = request.layout.block.options.cwd}
    if request.focus then focused = id end
    return {block_ids = {id}}
  end},
}
install{python = '/python', rex = '/rex', yazi = '/yazi', server = 'unix:///private.sock', launcher = '/launch.py'}
assert(action.name == 'rexdeck_files' and type(action.run) == 'function')

for _, invalid in ipairs({{}, {session_id = 'session-a'}, {block_id = 'origin-a'},
    {session_id = '', block_id = 'origin-a'}, {session_id = 1, block_id = 'origin-a'}}) do
  reset()
  assert(not pcall(action.run, invalid, {}), 'missing origin identity accepted')
  assert(effects == 0 and opened == 0, 'invalid identity caused effects')
end

for _, failure in ipairs({'attach-error', 'pwd-error', 'no-pwd', 'exec-error', 'refused-cwd',
    'invalid-json', 'null-json', 'relative-cwd', 'missing-cwd', 'split-error'}) do
  reset()
  mode = failure
  local ok, err = pcall(action.run, context, {})
  assert(not ok and tostring(err):find('RexDeck Files', 1, true), failure .. ' was not visible')
  assert(opened == 0 and focused == 'origin-b', failure .. ' changed panes/focus')
end

reset()
local first = action.run(context, {}).block_ids[1]
assert(panes[first].session == 'session-a' and panes[first].window == 'window-a',
  'mutable focus/context redirected split away from frozen origin')
assert(panes[first].cwd == '/exact origin', 'pane did not start in decoded cwd')
context = {session_id = 'session-b', block_id = 'origin-b'}
local second = action.run(context, {}).block_ids[1]
assert(first ~= second and panes[first] and panes[second], 'second invocation replaced a Yazi pane')
assert(panes[second].window == 'window-b', 'second origin opened in first origin window')
context = {session_id = 'session-a', block_id = 'origin-a'}
local third = action.run(context, {}).block_ids[1]
assert(third ~= first and panes[first] and panes[second], 'repeat invocation closed independent panes')
