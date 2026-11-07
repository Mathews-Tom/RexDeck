-- Native Rex configuration module; also embedded verbatim by RexDeck's installer.
return function(opts)
  if type(opts) ~= 'table' then error('RexDeck Files requires installation options', 0) end
  local paths = {}
  for _, key in ipairs({'python', 'rex', 'yazi', 'launcher'}) do
    local value = opts[key]
    if type(value) ~= 'string' or value:sub(1, 1) ~= '/' or value:find('%z') then
      error('RexDeck Files requires an absolute ' .. key .. ' path', 0)
    end
    paths[key] = value
  end
  local server = opts.server
  if type(server) ~= 'string' or server == '' or server:find('%z') then
    error('RexDeck Files requires an explicit Rex server', 0)
  end

  rex.action{
    name = 'rexdeck_files',
    title = 'RexDeck Files',
    category = 'RexDeck',
    description = 'Open Yazi in the invoking terminal directory',
    keywords = 'files yazi explorer',
    run = function(ctx, args)
      -- Freeze identity before any call can change focus or mutate the context.
      local session = type(ctx) == 'table' and ctx.session_id
      local origin = type(ctx) == 'table' and ctx.block_id
      if type(session) ~= 'string' or session == '' or type(origin) ~= 'string' or origin == '' then
        error('RexDeck Files requires the invoking session and terminal pane', 0)
      end
      local attached, attach_error = rex.call('session.attach', {session_id = session})
      if not attached then
        error('RexDeck Files cannot attach to the invoking session: ' .. tostring(attach_error), 0)
      end
      local reported, pwd_error = rex.call('com.superlogical.terminal.pwd', {
        session_id = session, block_id = origin, args = {},
      })
      if not reported then
        error('RexDeck Files cannot read the invoking terminal cwd: ' .. tostring(pwd_error), 0)
      end
      if type(reported.pwd) ~= 'string' or reported.pwd == '' then
        error('RexDeck Files: the invoking terminal has not reported a cwd', 0)
      end
      local checked, exec_error = rex.exec({paths.python, paths.launcher, 'cwd', reported.pwd}, {timeout = 10})
      if not checked then
        error('RexDeck Files cannot validate the invoking cwd: ' .. tostring(exec_error), 0)
      end
      if not checked.ok then
        error('RexDeck Files refused the invoking cwd: ' .. tostring(checked.stderr), 0)
      end
      local decoded, json_error = rex.json.decode(checked.stdout)
      if json_error or type(decoded) ~= 'table' or type(decoded.cwd) ~= 'string'
          or decoded.cwd:sub(1, 1) ~= '/' or decoded.cwd:find('%z') then
        error('RexDeck Files received an invalid cwd validation result: ' .. tostring(json_error), 0)
      end
      local cwd = decoded.cwd
      local split, split_error = rex.session.new_split{
        session_id = session,
        anchor_id = origin,
        direction = 'horizontal',
        side = 'after',
        ratio = 0.5,
        focus = true,
        layout = {block = {
          flavor = 'com.superlogical.terminal.shell',
          label = 'RexDeck Files',
          options = {
            cwd = cwd,
            shell = 'none',
            login = false,
            exit = {on_completion = true},
            command = {paths.python, paths.launcher, 'run', paths.yazi, paths.rex,
              server, session, origin, cwd},
          },
        }},
      }
      if not split then
        error('RexDeck Files cannot open the Yazi terminal: ' .. tostring(split_error), 0)
      end
      return split
    end,
  }
end
