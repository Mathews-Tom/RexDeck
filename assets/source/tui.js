// Stylized recreations of the hosted tools' layouts, for promotional media.
// They are illustrations, not screen recordings.
const TUI = {
  hostedTools: ["Yazi", "lazygit", "gh-dash", "markdown-reader", "tokscale"],
  shell() {
    return `<div class="pad"><span class="t">~/projects/rexdeck</span> <span class="a">❯</span> git status -sb
<span class="m">## main...origin/main</span>
<span class="t">~/projects/rexdeck</span> <span class="a">❯</span> </div>`;
  },

  yazi(compact = false) {
    const parent = `<div class="col"><span class="s b">projects/</span>
  <span class="m">archive</span>
  <span class="sel"> rexdeck</span>
  <span class="m">notes</span>
  <span class="m">sandbox</span></div>`;
    return `<div class="cols" style="grid-template-columns: ${compact ? "1fr 1.2fr" : "1fr 1.3fr 1.7fr"}">
${compact ? "" : parent}
<div class="col"><span class="s"> .github</span>
<span class="s"> plugins</span>
<span class="sel"> src</span>
<span class="s"> tests</span>
 CHANGELOG.md
 LICENSE
 README.md
 pyproject.toml
 uv.lock</div>
<div class="col"><span class="s b">src/rexdeck/</span>
<span class="s"> clickup</span>
<span class="s"> host</span>
<span class="s"> plugins</span>
 attention.py
 cli.py
 integrations.py
 lifecycle.py
 report.py
 settings.py</div>
</div>
<div class="bar"><span class="mode">NOR</span><span>drwxr-xr-x</span><span>3/9</span><span style="margin-left:auto">~/projects/rexdeck/src</span></div>`;
  },

  lazygit() {
    return `<div class="cols" style="grid-template-columns: 0.95fr 1.25fr; gap: 8px; padding: 8px">
<div style="display:grid; grid-template-rows: auto 1fr 0.8fr 1.4fr; gap: 6px; overflow:hidden">
<div class="box"><h6>[1] Status</h6>rexdeck → <span class="t">main</span> <span class="m">✓</span></div>
<div class="box on"><h6>[2] Files</h6><span class="c">M</span>  README.md
<span class="sel"><span class="c">M</span>  src/rexdeck/cli.py</span>
<span class="t">A</span>  tests/test_report.py</div>
<div class="box"><h6>[3] Branches</h6><span class="t">* main</span>
  feat/deck-banner</div>
<div class="box"><h6>[4] Commits</h6><span class="a">d3e4f5a</span> docs: refresh project intro
<span class="a">c2d3e4f</span> feat: add task reader
<span class="a">b1c2d3e</span> feat: choose pane layout
<span class="a">a0b1c2d</span> fix: preserve focus</div>
</div>
<div class="box" style="overflow:hidden"><h6>Diff — src/rexdeck/cli.py</h6><span class="s">@@ -411,6 +411,23 @@</span>
<span class="c">-            if args.command == "enable":</span>
<span class="t">+            if args.command in {"enable", "disable"}:</span>
<span class="t">+                names = list(dict.fromkeys(args.plugin))</span>
<span class="t">+</span>
<span class="t">+                def change(name: str):</span>
<span class="t">+                    if args.command == "enable":</span>
<span class="t">+                        return _enable(path, target, name)</span>
<span class="t">+                    return lifecycle.disable(target.setup(name))</span>
                 ...
</div>
</div>
<div class="bar"><span>Commit: <span class="a">c</span></span><span>Push: <span class="a">P</span></span><span>Stage: <span class="a">space</span></span><span>Diff: <span class="a">enter</span></span><span style="margin-left:auto">Keybindings: <span class="a">?</span></span></div>`;
  },

  ghdash() {
    return `<div class="pad"><span class="b">  My Pull Requests</span>   <span class="m">Needs Review   Involved</span>
<span class="m">────────────────────────────────────────────────────</span>
<span class="m">  #   Title                              Checks  Rev</span>
<span class="sel">  42  Add pane layout presets             <span class="t">✓</span>      <span class="t">✓</span> </span>
  41  Readable plugin report              <span class="t">✓</span>      <span class="a">…</span>
  39  Guided workspace selection          <span class="t">✓</span>      <span class="t">✓</span>
  37  Viewer: PDF page navigation         <span class="c">✗</span>      <span class="m">–</span>
  35  Attention: exact jump               <span class="t">✓</span>      <span class="t">✓</span>
</div>
<div class="bar"><span>repo:rexdeck</span><span style="margin-left:auto">? help</span></div>`;
  },

  mdreader() {
    return `<div class="pad"><span class="a b"># Release flow</span>

<span class="m">Every candidate is built once and approved.</span>

    ┌──────────┐     ┌──────────┐     ┌──────────┐
    │  <span class="t">Merge</span>   │────▶│  <span class="s">Build</span>   │────▶│ <span class="a">Publish</span>  │
    └──────────┘     └──────────┘     └──────────┘
<span class="m">  [mermaid — text mode]</span></div>`;
  },

  tokscale() {
    const row = (name, w, cls, n) =>
      `${name.padEnd(16)}<span class="${cls}">${"█".repeat(w)}</span><span class="m">${"░".repeat(24 - w)}</span> ${n}`;
    return `<div class="pad"><span class="b">  Tokscale · Models</span>     <span class="m">Overview  Usage  Daily  Sessions</span>
<span class="m">────────────────────────────────────────────────────</span>
${row("claude-opus", 21, "a", "61%")}
${row("claude-sonnet", 9, "t", "24%")}
${row("gpt-5", 4, "s", " 11%")}
${row("other", 1, "l", "  4%")}

<span class="m">  r refresh   Shift+R auto-refresh   q quit</span></div>`;
  },

  clickup() {
    return `<div class="desk">
<div class="desk-scope">ClickUp · Demo workspace › Launch</div>
<div class="desk-fresh">Fresh current-list data</div>
<div class="desk-toolbar">Refresh   Lists   Actions   Help</div>
<div class="desk-columns">
<div class="desk-queue">
<div class="desk-filter">Filter this list  (/)</div>
<div class="desk-controls">□ Mine   □ Closed</div>
<div class="desk-task sel"><b>Ship launch banner</b><span class="a">in progress</span></div>
<div class="desk-task"><b>Draft release notes</b><span class="s">review</span></div>
<div class="desk-task"><b>Record walkthrough</b><span class="m">open</span></div>
</div>
<div class="desk-reader"><h5>Ship launch banner</h5><span class="a">in progress</span>
<div class="desk-meta">Home list 10001 · Points: 3</div>
<p><b>Description</b><br>Show familiar tools and purpose-built views in Rex.</p>
<p><b>Comments</b></p><div class="desk-comment"><span class="m">Demo user</span><br>Keep the artwork version-free.</div>
</div>
</div></div>
<div class="bar"><span>Enter read</span><span>/ filter</span><span>l lists</span><span>a actions</span><span style="margin-left:auto">q quit</span></div>`;
  },

  palette(query, sel) {
    const items = ["RexDeck Files", "RexDeck Git", "RexDeck GitHub", "RexDeck Markdown", "RexDeck Usage", "RexDeck Viewer", "RexDeck ClickUp"];
    const shown = items.filter((x) => x.toLowerCase().includes(query.toLowerCase().replace("rexdeck", "").trim()));
    const rows = (query ? shown : items)
      .map((x, i) => `<div class="prow ${i === sel ? "on" : ""}"><span>${x}</span><span class="m">RexDeck</span></div>`)
      .join("");
    return `<div class="pin"><span class="a">›</span> ${query}<span class="caret"></span></div>${rows}`;
  },
};
