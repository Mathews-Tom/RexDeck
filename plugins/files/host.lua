-- Rex effects stay separate from filesystem traversal and the compact navigator.
local M = {}

function M.new(app)
  local H = {previews = {}}

  function H.query(operation, pwd, path)
    if not app.args.python then app.fail('configuration', 'Configure Files python, rex, bat and server arguments.') end
    local command = {app.args.python, app.dir .. '/filesystem.py', operation, pwd}
    if path then command[#command + 1] = path end
    local result, err = rex.exec(command, {timeout = 10})
    if not result then app.fail('unavailable', tostring(err)) end
    if #result.stdout > 12 * 1024 then app.fail('too_large', 'Filesystem helper exceeded its byte budget.') end
    local reply, decode_error = rex.json.decode(result.stdout)
    if not reply then app.fail('failed', 'Filesystem helper returned invalid JSON: ' .. tostring(decode_error)) end
    if not result.ok and reply.ok then app.fail('failed', 'Filesystem helper did not finish successfully.') end
    return reply
  end

  function H.files(operation, pwd, path)
    local reply = H.query(operation, pwd, path)
    if not reply.ok then app.fail('filesystem', reply.error) end
    return reply
  end

  function H.origin(ctx)
    local c = ctx.context
    if not c or not c.session_id or not c.source_block_id then
      app.fail('refused', 'Open Files from a project terminal pane.')
    end
    if not ctx.source.pwd then
      app.fail('refused', 'The originating pane has no reported directory: ' .. tostring(ctx.source.pwd_error))
    end
    return c.session_id, c.source_block_id
  end

  function H.focus(ctx)
    local session, block = H.origin(ctx)
    return app.rex('session.focus_block', {session_id = session, block_id = block})
  end

  function H.identity(pid)
    if type(pid) ~= 'number' or pid <= 0 then return nil end
    local result = rex.exec({app.args.python, app.dir .. '/preview.py', 'identity', tostring(pid)}, {timeout = 5})
    if not result or not result.ok or #result.stdout > 4096 then return nil end
    local reply = rex.json.decode(result.stdout)
    if not reply or not reply.ok then return nil end
    return reply.identity
  end

  function H.preview(ctx, path)
    local session, origin = H.origin(ctx)
    if not app.args.rex or not app.args.server or not app.args.bat then
      app.fail('configuration', 'Configure Files rex, server and bat arguments before opening a preview.')
    end
    local file = H.files('file', ctx.source.pwd, path)
    app.rex('session.attach', {session_id = session})
    local key = session .. '/' .. origin
    local old = H.previews[key]
    if old and old.pid and old.identity then
      local p = app.rex('session.list_blocks', {session_id = session})
      for _, b in ipairs(p.blocks or {}) do
        if b.block_id == old.block then
          local process = app.rex('com.superlogical.terminal.process', {session_id = session, block_id = old.block})
          -- PID alone can be reused; uncertain/replaced process generations are left alone.
          if process.child and process.child.pid == old.pid and process.child.path == old.path
              and H.identity(old.pid) == old.identity then
            app.rex('block.close', {session_id = session, block_id = old.block})
          end
        end
      end
    end
    local opened = app.rex('session.new_split', {
      session_id = session, anchor_id = origin, direction = 'horizontal', side = 'after', ratio = 0.5, focus = true,
      layout = {block = {flavor = 'com.superlogical.terminal.shell', label = 'rexdeck-preview',
        options = {shell = 'none', login = false, cwd = file.root,
          exit = {on_completion = true, quick_exit_threshold_ms = 0}, command = {
          app.args.python, app.dir .. '/preview.py', 'view', app.args.rex, app.args.server,
          session, origin, app.args.bat, file.root, file.path,
        }}}},
    })
    local block = opened.block_ids[1]
    local process = app.rex('com.superlogical.terminal.process', {session_id = session, block_id = block})
    local child = process.child
    local owned = {block = block}
    if child then
      local identity = H.identity(child.pid)
      -- The first snapshot can still name Rex's launcher, before exec into Python.
      local current = app.rex('com.superlogical.terminal.process', {session_id = session, block_id = block}).child
      if current and current.pid == child.pid then
        owned.pid, owned.path, owned.identity = child.pid, current.path, identity
      end
    end
    H.previews[key] = owned
    return {preview_block = block, path = file.path, origin_block = origin}
  end

  return H
end

return M
