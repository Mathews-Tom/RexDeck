-- Native Rex host for one RexDeck integration; embedded verbatim in its generated module.
-- Every integration shares this action: frozen origin identity, validated context,
-- shell-less argv, configured split layout and exact-origin return by the launcher.
local contexts = {directory = true, ['git-worktree'] = true, document = true,
  ['github-worktree'] = true, media = true, ['selected-file'] = true,
  ['dekit-config'] = true}
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
  -- An `#!/usr/bin/env NAME` tool keeps the interpreter qualified with it: the app-started
  -- server's PATH is minimal, so the pane gets that interpreter's directory first.
  local path_prepend = nil
  if opts.interpreter ~= nil then
    local value = opts.interpreter
    local directory = nonempty(value) and value:match('^(/.*)/[^/]+$')
    if not directory then error(title .. ' requires an absolute interpreter path', 0) end
    path_prepend = {directory}
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

  -- selected-file passes its accepted suffixes (and `/` for directories) to the
  -- launcher inside the context argument, so existing context argv stays unchanged.
  local launch_context = opts.context
  local wanted = nil
  if opts.context == 'selected-file' then
    local spec = opts.selected_file
    if not nonempty(spec) or not spec:match('^[%.%w/,]+$') then
      error(title .. ' requires its accepted file selection', 0)
    end
    launch_context = 'selected-file:' .. spec
    local names, directories = {}, false
    for entry in spec:gmatch('[^,]+') do
      if entry == '/' then directories = true else names[#names + 1] = entry end
    end
    wanted = #names > 0 and (table.concat(names, ', ') .. ' file') or ''
    if directories then wanted = wanted .. (#names > 0 and ' or a directory' or 'directory') end
  end
  if opts.context == 'dekit-config' then wanted = 'trusted dekit.yaml' end
  local document = opts.context == 'document' or opts.context == 'media' or wanted ~= nil
  local github = opts.context == 'github-worktree'

  rex.action{
    name = 'rexdeck_' .. opts.plugin,
    title = title,
    category = 'RexDeck',
    description = opts.description,
    keywords = opts.keywords,
    args = document and {file = 'string?'}
      or (github and {repo = 'string?', remote = 'string?', account = 'string?'} or nil),
    run = function(ctx, action_args)
      local selected = nil
      if not document and type(action_args) == 'table' and action_args.file ~= nil then
        error(title .. ' does not accept a selected file', 0)
      end
      if document and type(action_args) == 'table' then selected = action_args.file end
      if opts.context == 'media' and selected == nil then
        error(title .. ' requires an explicitly selected PDF, JPG, PNG or SVG file', 0)
      end
      if wanted and selected == nil then
        error(title .. ' requires an explicitly selected ' .. wanted .. ' (file=PATH)', 0)
      end
      if selected ~= nil and not nonempty(selected) then
        error(title .. ' requires a nonempty selected file path', 0)
      end
      if github then
        local choice = {}
        for _, key in ipairs({'repo', 'remote', 'account'}) do
          local value = type(action_args) == 'table' and action_args[key] or nil
          if value ~= nil then
            if not nonempty(value) then error(title .. ' requires a nonempty ' .. key, 0) end
            choice[key] = value
          end
        end
        if not nonempty(choice.account) then
          error(title .. ' requires account=LOGIN matching existing gh authentication', 0)
        end
        selected = rex.json.encode(choice)
      end
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
      local validation = {paths.python, '-I', '-c', launcher, 'context', launch_context,
        reported.pwd}
      if selected then validation[#validation + 1] = selected end
      local checked, exec_error = rex.exec(validation, {timeout = 15})
      if not checked then
        error(title .. ' cannot validate the invoking context: ' .. tostring(exec_error), 0)
      end
      if not checked.ok then
        error(title .. ' refused the invoking context: ' .. tostring(checked.stderr), 0)
      end
      local decoded, json_error = rex.json.decode(checked.stdout)
      if json_error or type(decoded) ~= 'table'
          or type(decoded.cwd) ~= 'string' or decoded.cwd:sub(1, 1) ~= '/' or decoded.cwd:find('%z')
          or type(decoded.target) ~= 'string' or decoded.target:sub(1, 1) ~= '/'
          or decoded.target:find('%z') then
        error(title .. ' received an invalid context validation result: ' .. tostring(json_error), 0)
      end
      local cwd = decoded.cwd
      local command = {paths.python, '-I', '-c', launcher, 'run', launch_context, opts.title,
        opts.tool_label, paths[opts.tool], paths.rex, opts.server, session, origin,
        decoded.target}
      for index = 1, #args do command[#command + 1] = args[index] end
      if github then
        if not nonempty(decoded.selection) then
          error(title .. ' did not receive a validated repository/account selection', 0)
        end
        command[#command + 1] = decoded.selection
      end
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
            path_prepend = path_prepend,
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
