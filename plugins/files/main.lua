local app = require('lazyrex').plugin('rexdeck_files')
local host = require('host').new(app)
local state = {directories = {}, roots = {}}
local TITLE = 'Rexdeck Files'

local function label(text)
  return (tostring(text):gsub('[%z\1-\31\127]', function(c)
    return string.format('\\x%02x', string.byte(c))
  end))
end

-- Mirror only the pinned SDK's documented wire fields, not renderer-only metadata.
-- Measure a complete conservatively escaped envelope, reserving transport overhead.
local function fits(nodes)
  local wire = {}
  local function action(n)
    if not n.on_select then return n.action end
    return {name = 'select', label = n.action_label or 'Select', enabled = n.enabled ~= false,
      user_only = n.user_only or 'navigation'}
  end
  for i, n in ipairs(nodes) do
    local out = {id = tostring(n.id), type = n.type or 'widget', label = n.label, text = n.text, action = action(n)}
    if n.type == 'list' then
      if #(n.items or {}) == 0 then
        out = {id = tostring(n.id), type = 'widget', label = n.label, text = n.empty or 'Nothing here yet.'}
      else
        out.items = {}
        for j, item in ipairs(n.items) do
          out.items[j] = {id = tostring(item.id), label = tostring(item.label or item.id),
            detail = item.detail or '', action = action(item)}
        end
      end
    end
    wire[i] = out
  end
  local reply, err = rex.json.encode({tree = {title = TITLE, nodes = wire}, refresh = 'all'})
  if not reply then app.fail('invalid_input', 'Cannot encode the Files view: ' .. tostring(err)) end
  local envelope = rex.json.encode({id = string.rep('i', 128), result = reply,
    transport_reserve = string.rep(' ', 4096)})
  return envelope and #envelope <= 48 * 1024
end

app.panel(TITLE, function(ctx)
  local session, origin = host.origin(ctx)
  local key = session .. '/' .. origin .. '/' .. ctx.source.pwd
  local listing = host.query('list', ctx.source.pwd, state.directories[key])
  local function navigate(path)
    local checked = host.files('list', ctx.source.pwd, path)
    state.directories[key] = checked.path
    state.roots[key] = checked.root
    app.refresh()
    return {path = checked.path}
  end
  local return_node = app.ui.widget('return', 'Return to project terminal',
    function(action_ctx) return host.focus(action_ctx) end, {user_only = 'navigation', label = 'Return'})
  local refresh_node = app.ui.widget('refresh', 'Refresh directory', function() app.refresh(); return {} end,
    {user_only = 'navigation', label = 'Refresh'})
  local function recovery(message)
    local nodes = {
      app.ui.error('filesystem', label(message)),
      app.ui.widget('reset', 'Return to project root', function() return navigate(nil) end,
        {user_only = 'navigation', label = 'Root'}),
      refresh_node,
      return_node,
    }
    local current, root = state.directories[key], state.roots[key]
    if current and root and current ~= root then
      table.insert(nodes, 2, app.ui.widget('parent', 'Parent directory', function()
        local parent = current:match('^(.*)/[^/]+$')
        return navigate(parent == '' and '/' or parent)
      end, {user_only = 'navigation', label = 'Parent'}))
    end
    if not fits(nodes) then app.fail('too_large', 'Recovery view exceeded its byte budget; return to the project terminal.') end
    return nodes
  end
  if not listing.ok then return recovery(listing.error) end
  state.roots[key] = listing.root
  local items = {}
  if listing.path ~= listing.root then
    items[#items + 1] = {id = '..', label = '../', detail = '', user_only = 'navigation',
      on_select = function()
        local parent = listing.path:match('^(.*)/[^/]+$')
        return navigate(parent == '' and '/' or parent)
      end}
  end
  for _, entry in ipairs(listing.entries or {}) do
    local e = entry
    items[#items + 1] = {id = e.id, label = label(e.name) .. (e.kind == 'directory' and '/' or ''),
      detail = '', user_only = 'navigation', on_select = function(action_ctx)
        if e.kind == 'directory' then return navigate(e.path) end
        return host.preview(action_ctx, e.path)
      end}
  end
  local relative = listing.path:sub(#listing.root + 1)
  if listing.root == '/' and relative ~= '' then relative = '/' .. relative end
  local project = listing.root:match('([^/]+)$') or '/'
  local nodes = {
    app.ui.text('location', label(project) .. (relative ~= '' and (' · .' .. relative) or ' · .')),
    app.ui.text('keys', 'Tab/Shift+Tab select · Enter open · Esc close'),
    app.ui.list('files', items, {empty = 'Empty directory'}),
    refresh_node,
    return_node,
  }
  if fits(nodes) then return nodes end
  local fallback = recovery('Directory exceeds the complete view byte budget. No entries were omitted; listing refused.')
  if not fits(fallback) then app.fail('too_large', 'Recovery view exceeded its byte budget; close Files and return to the project terminal.') end
  return fallback
end, {id = 'files', description = 'Browse this project; preview UTF-8 text up to 2 MiB.', landing = false})
