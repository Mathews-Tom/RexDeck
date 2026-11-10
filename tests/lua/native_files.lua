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
  exec = function(argv)
    assert(argv[5] == 'context' and argv[6] == 'directory')
    effects = effects + 1
    if mode == 'exec-error' then return nil, 'python unavailable' end
    if mode == 'refused-cwd' then return {ok = false, stderr = 'remote cwd refused'} end
    return {ok = true, stdout = '{"cwd":"/exact origin","target":"/exact origin"}'}
  end,
  json = {decode = function()
    if mode == 'invalid-json' then return nil, 'invalid JSON' end
    if mode == 'null-json' then return nil end
    if mode == 'relative-cwd' then return {cwd = 'relative'} end
    if mode == 'missing-cwd' then return {} end
    if mode == 'missing-target' then return {cwd = '/exact origin'} end
    return {cwd = '/exact origin', target = '/exact origin'}
  end},
  session = {new_split = function(request)
    effects = effects + 1
    assert(request.direction == 'vertical' and request.side == 'after' and request.ratio == 0.7,
      'split ignored configured layout')
    local source = panes[request.anchor_id]
    if mode == 'split-error' then return nil, 'split denied' end
    if not source or source.session ~= request.session_id then return nil, 'invalid source' end
    opened = opened + 1
    local id = 'yazi-' .. opened
    panes[id] = {session = source.session, window = source.window,
      cwd = request.layout.block.options.cwd, command = request.layout.block.options.command}
    if request.focus then focused = id end
    return {block_ids = {id}}
  end},
}
local options = {python = '/python', rex = '/rex', yazi = '/yazi',
  server = 'unix:///private.sock', launcher_code = 'pass', plugin = 'files',
  title = 'Files', tool = 'yazi', tool_label = 'Yazi', description = 'Files',
  keywords = 'files', context = 'directory', args = {},
  layout = {direction = 'vertical', side = 'after', ratio = 0.7}}
for _, ratio in ipairs({'0.7', 0 / 0, 0.1, 0.9}) do
  options.layout.ratio = ratio
  assert(not pcall(install, options), 'invalid layout ratio accepted: ' .. tostring(ratio))
end
options.layout.ratio = 0.7
install(options)
assert(action.args == nil, 'non-document action declares arguments')
assert(action.name == 'rexdeck_files' and type(action.run) == 'function')

for _, invalid in ipairs({{}, {session_id = 'session-a'}, {block_id = 'origin-a'},
    {session_id = '', block_id = 'origin-a'}, {session_id = 1, block_id = 'origin-a'}}) do
  reset()
  assert(not pcall(action.run, invalid, {}), 'missing origin identity accepted')
  assert(effects == 0 and opened == 0, 'invalid identity caused effects')
end

for _, failure in ipairs({'attach-error', 'pwd-error', 'no-pwd', 'exec-error', 'refused-cwd',
    'invalid-json', 'null-json', 'relative-cwd', 'missing-cwd', 'missing-target', 'split-error'}) do
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

-- Descriptor argv reaches the launcher after the nine fixed fields, in order.
local fixed = panes[first].command
assert(#fixed == 14 and fixed[5] == 'run' and fixed[14] == '/exact origin',
  'launch command lost its fixed launcher fields')
options.args = {'tui', '--client', 'omp'}
install(options)
reset()
local forwarded = panes[action.run(context, {}).block_ids[1]].command
assert(#forwarded == 17 and forwarded[15] == 'tui' and forwarded[16] == '--client'
  and forwarded[17] == 'omp', 'descriptor args were not forwarded after the fixed fields')

options.plugin, options.title, options.context = 'markdown', 'Markdown', 'document'
options.tool, options.tool_label = 'markdown-reader', 'markdown-reader'
options['markdown-reader'] = '/markdown-reader'
options.args = {'--theme', 'dark'}
install(options)
assert(action.args.file == 'string?', 'document action lacks optional file argument')
local selected = '/selected/雪 notes.md'
rex.exec = function(argv)
  assert(argv[5] == 'context' and argv[6] == 'document')
  assert(argv[7] == 'file:///exact%20origin' and argv[8] == selected,
    'selected file did not reach context validation')
  return {ok = true, stdout = 'validated'}
end
rex.json.decode = function()
  return {cwd = '/selected', target = selected}
end
rex.session.new_split = function(request)
  opened = opened + 1
  local pane = request.layout.block.options
  assert(pane.cwd == '/selected', 'pane did not use decoded workdir')
  assert(pane.command[14] == selected, 'launcher did not use decoded target')
  assert(pane.command[15] == '--theme' and pane.command[16] == 'dark')
  return {block_ids = {'markdown-1'}}
end
reset()
action.run(context, {file = selected})
assert(opened == 1)
reset()
rex.json.decode = function() return {cwd = '/selected'} end
local ok, err = pcall(action.run, context, {file = selected})
assert(not ok and tostring(err):find('invalid context validation result', 1, true))
assert(opened == 0, 'missing target opened a pane')
for _, invalid in ipairs({'', false, 1, {}}) do
  reset()
  assert(not pcall(action.run, context, {file = invalid}))
  assert(effects == 0 and opened == 0, 'invalid selection caused effects')
end

-- GitHub must preserve the captured repository/account choice across validation.
options.plugin, options.title, options.context = 'github', 'GitHub', 'github-worktree'
options.tool, options.tool_label, options['gh-dash'] = 'gh-dash', 'gh-dash', '/gh-dash'
options.args = {}
install(options)
assert(action.args.repo == 'string?' and action.args.remote == 'string?'
  and action.args.account == 'string?', 'GitHub action lacks explicit identity arguments')
local captured = 'captured identity'
rex.json.encode = function(value)
  assert(value.repo == 'github.com/owner/project' and value.account == 'reader')
  assert(value.remote == nil)
  return captured
end
rex.exec = function(argv)
  assert(argv[6] == 'github-worktree' and argv[8] == captured,
    'GitHub identity lost before context validation')
  return {ok = true, stdout = 'validated'}
end
rex.json.decode = function()
  return {cwd = '/exact origin', target = '/exact origin', selection = captured}
end
rex.session.new_split = function(request)
  opened = opened + 1
  assert(request.anchor_id == 'origin-a', 'GitHub lost captured origin')
  local command = request.layout.block.options.command
  assert(command[14] == '/exact origin' and command[15] == captured,
    'GitHub selection lost between preflight and launch')
  return {block_ids = {'github-1'}}
end
reset()
action.run(context, {repo = 'github.com/owner/project', account = 'reader'})
assert(opened == 1)
reset()
local ok, err = pcall(action.run, context, {})
assert(not ok and tostring(err):find('account=LOGIN', 1, true))
assert(opened == 0 and effects == 0, 'missing account caused effects')
reset()
rex.json.decode = function() return {cwd = '/exact origin', target = '/exact origin'} end
assert(not pcall(action.run, context, {repo = 'github.com/owner/project', account = 'reader'}))
assert(opened == 0, 'missing validated identity opened a pane')

options.plugin, options.title, options.context = 'viewer', 'Viewer', 'media'
options.tool, options.tool_label = 'yazi', 'Yazi'
options.args = {}
install(options)
for _, absent in ipairs({{}, {file = false}, {file = ''}}) do
  reset()
  local accepted, error = pcall(action.run, context, absent)
  assert(not accepted and tostring(error):find('RexDeck Viewer', 1, true))
  assert(effects == 0 and opened == 0, 'missing rich-file selection changed focus or panes')
end
reset()
rex.exec = function(argv)
  assert(argv[6] == 'media' and argv[8] == '/selected/image.png')
  return {ok = false, stderr = 'Invalid .png file signature'}
end
local accepted, error = pcall(action.run, context, {file = '/selected/image.png'})
assert(not accepted and tostring(error):find('Invalid .png file signature', 1, true))
assert(opened == 0 and focused == 'origin-b', 'corrupt rich file opened a pane')

-- selected-file carries its accepted suffixes in the context argument only.
options.plugin, options.title, options.context = 'json', 'JSON', 'selected-file'
options.tool, options.tool_label, options.jless = 'jless', 'jless', '/jless'
options.args = {'--json'}
for _, invalid in ipairs({false, '', 'json;rm', '.json\0'}) do
  options.selected_file = invalid
  assert(not pcall(install, options), 'invalid selected-file spec accepted')
end
options.selected_file = '.json'
install(options)
assert(action.args.file == 'string?', 'selected-file action lacks file argument')
for _, absent in ipairs({{}, {file = false}, {file = ''}}) do
  reset()
  local accepted, error = pcall(action.run, context, absent)
  assert(not accepted and tostring(error):find('RexDeck JSON', 1, true))
  if absent.file == nil then
    assert(tostring(error):find('selected .json file', 1, true), tostring(error))
  end
  assert(effects == 0 and opened == 0, 'missing selected file changed focus or panes')
end
rex.exec = function(argv)
  assert(argv[6] == 'selected-file:.json' and argv[8] == 'data/big.json',
    'selected-file spec or selection lost before validation')
  return {ok = true, stdout = 'validated'}
end
rex.json.decode = function() return {cwd = '/data', target = '/data/big.json'} end
rex.session.new_split = function(request)
  opened = opened + 1
  local command = request.layout.block.options.command
  assert(command[6] == 'selected-file:.json' and command[14] == '/data/big.json'
    and command[15] == '--json' and #command == 15,
    'selected-file launch lost its spec, target or args')
  return {block_ids = {'json-1'}}
end
reset()
action.run(context, {file = 'data/big.json'})
assert(opened == 1)
options.selected_file = '.log,.txt,/'
install(options)
reset()
local accepted, error = pcall(action.run, context, {})
assert(not accepted and tostring(error):find('selected .log, .txt file or a directory', 1, true),
  tostring(error))
