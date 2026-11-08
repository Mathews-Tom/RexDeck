-- Native Rex host for one RexDeck integration; embedded verbatim in its generated module.
-- Every integration shares this action: frozen origin identity, validated context,
-- shell-less argv, configured split layout and exact-origin return by the launcher.
local contexts = {directory = true, ['git-worktree'] = true}
local function nonempty(value)
  return type(value) == 'string' and value ~= '' and not value:find('%z')
end

return function(opts)
  if type(opts) ~= 'table' then error('RexDeck requires installation options', 0) end
  if not nonempty(opts.plugin) or not opts.plugin:match('^[a-z][a-z0-9]*$') then
    error('RexDeck requires a valid integration id', 0)
  end
  if not nonempty(opts.title) then error('RexDeck requires an integration title', 0) end
  local title = 'RexDeck ' .. opts.title
  for _, key in ipairs({'tool', 'tool_label', 'description', 'keywords'}) do
    if not nonempty(opts[key]) then error(title .. ' requires ' .. key, 0) end
  end
  if not contexts[opts.context] then error(title .. ' has an unknown context', 0) end
  local paths = {}
  for _, key in ipairs({'python', 'rex', opts.tool}) do
    local value = opts[key]
    if not nonempty(value) or value:sub(1, 1) ~= '/' then
      error(title .. ' requires an absolute ' .. key .. ' path', 0)
    end
    paths[key] = value
  end
  local args = opts.args
  if type(args) ~= 'table' then error(title .. ' requires an argument list', 0) end
  for index = 1, #args do
    if not nonempty(args[index]) then error(title .. ' has an invalid argument', 0) end
  end
  local layout = opts.layout
  if type(layout) ~= 'table'
      or (layout.direction ~= 'horizontal' and layout.direction ~= 'vertical')
      or (layout.side ~= 'before' and layout.side ~= 'after')
      or type(layout.ratio) ~= 'number' or not (layout.ratio >= 0.2 and layout.ratio <= 0.8) then
    error(title .. ' has an invalid pane layout', 0)
  end
  local launcher = opts.launcher_code
  if not nonempty(launcher) then error(title .. ' requires its applied Python launcher', 0) end
  if not nonempty(opts.server) then error(title .. ' requires an explicit Rex server', 0) end

  rex.action{
    name = 'rexdeck_' .. opts.plugin,
    title = title,
    category = 'RexDeck',
    description = opts.description,
    keywords = opts.keywords,
    run = function(ctx, _)
      -- Freeze identity before any call can change focus or mutate the context.
      local session = type(ctx) == 'table' and ctx.session_id
      local origin = type(ctx) == 'table' and ctx.block_id
      if type(session) ~= 'string' or session == '' or type(origin) ~= 'string' or origin == '' then
        error(title .. ' requires the invoking session and terminal pane', 0)
      end
      local attached, attach_error = rex.call('session.attach', {session_id = session})
      if not attached then
        error(title .. ' cannot attach to the invoking session: ' .. tostring(attach_error), 0)
      end
      local reported, pwd_error = rex.call('com.superlogical.terminal.pwd', {
        session_id = session, block_id = origin, args = {},
      })
      if not reported then
        error(title .. ' cannot read the invoking terminal cwd: ' .. tostring(pwd_error), 0)
      end
      if type(reported.pwd) ~= 'string' or reported.pwd == '' then
        error(title .. ': the invoking terminal has not reported a cwd', 0)
      end
      local checked, exec_error = rex.exec({paths.python, '-I', '-c', launcher,
        'context', opts.context, reported.pwd}, {timeout = 15})
      if not checked then
        error(title .. ' cannot validate the invoking cwd: ' .. tostring(exec_error), 0)
      end
      if not checked.ok then
        error(title .. ' refused the invoking cwd: ' .. tostring(checked.stderr), 0)
      end
      local decoded, json_error = rex.json.decode(checked.stdout)
      if json_error or type(decoded) ~= 'table' or type(decoded.cwd) ~= 'string'
          or decoded.cwd:sub(1, 1) ~= '/' or decoded.cwd:find('%z') then
        error(title .. ' received an invalid cwd validation result: ' .. tostring(json_error), 0)
      end
      local cwd = decoded.cwd
      local command = {paths.python, '-I', '-c', launcher, 'run', opts.context, opts.title,
        opts.tool_label, paths[opts.tool], paths.rex, opts.server, session, origin, cwd}
      for index = 1, #args do command[#command + 1] = args[index] end
      local split, split_error = rex.session.new_split{
        session_id = session,
        anchor_id = origin,
        direction = layout.direction,
        side = layout.side,
        ratio = layout.ratio,
        focus = true,
        layout = {block = {
          flavor = 'com.superlogical.terminal.shell',
          label = title,
          options = {
            cwd = cwd,
            shell = 'none',
            login = false,
            exit = {on_completion = true},
            command = command,
          },
        }},
      }
      if not split then
        error(title .. ' cannot open the tool terminal: ' .. tostring(split_error), 0)
      end
      return split
    end,
  }
end
