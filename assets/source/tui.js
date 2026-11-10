// Generic, tool-agnostic pane archetypes for RexDeck promotional media.
// They depict kinds of panes (a navigator, a table, a dashboard, a purpose-built
// view) with skeleton content, so new integrations never require new artwork.
// They are illustrations, not screen recordings, and name no specific tool.
const TUI = {
  // Stable categories rather than a tool list; every future integration fits one.
  categories: ["Files & code", "Data & logs", "Systems & services", "Work & APIs"],

  bar(width, cls = "") {
    return `<i class="sk ${cls}" style="width:${width}%"></i>`;
  },

  shell(lines = []) {
    const prompt = `<span class="t">~/work</span> <span class="a">❯</span>`;
    const body = lines.map(([cmd, out]) => `${prompt} ${cmd}${out ? `\n<span class="m">${out}</span>` : ""}`).join("\n");
    return `<div class="pad">${body}${body ? "\n" : ""}${prompt} </div>`;
  },

  // A hosted terminal tool: columns of entries with a preview.
  navigator(compact = false) {
    const col = (rows, sel) =>
      `<div class="col sk-col">${rows.map((w, i) => `<div class="sk-row ${i === sel ? "on" : ""}">${this.bar(w, i === sel ? "hi" : "")}</div>`).join("")}</div>`;
    const preview = `<div class="col sk-col"><div class="sk-row">${this.bar(46, "a")}</div>${[92, 84, 88, 60, 0, 90, 76, 82, 40]
      .map((w) => `<div class="sk-row">${w ? this.bar(w, "dim") : ""}</div>`)
      .join("")}</div>`;
    return `<div class="cols" style="grid-template-columns: ${compact ? "1fr 1.5fr" : "0.9fr 1.1fr 1.6fr"}">
${compact ? "" : col([62, 48, 70, 54, 40], 2)}${col([58, 72, 44, 66, 50, 38, 61, 47], 1)}${preview}</div>
<div class="bar"><span class="mode">NOR</span><span>↑↓ move</span><span>enter open</span><span>/ search</span><span style="margin-left:auto">q quit</span></div>`;
  },

  // Rows and columns: logs, tables, query results.
  table() {
    const widths = [18, 34, 22, 14];
    const head = `<div class="sk-tr head">${widths.map((w) => `<span style="flex:${w}">${this.bar(70, "a")}</span>`).join("")}</div>`;
    const rows = [0, 1, 2, 3, 4, 5, 6, 7]
      .map((r) => `<div class="sk-tr ${r === 2 ? "on" : ""}">${widths
        .map((w, c) => `<span style="flex:${w}">${this.bar(40 + ((r * 17 + c * 23) % 55), r === 2 ? "hi" : c === 3 ? "t" : "dim")}</span>`)
        .join("")}</div>`)
      .join("");
    return `<div class="sk-table">${head}${rows}</div>
<div class="bar"><span>/ filter</span><span>s sort</span><span>; query</span><span style="margin-left:auto">q quit</span></div>`;
  },

  // Live numbers: system, services, usage.
  dashboard() {
    const gauge = (label, pct, cls) =>
      `<div class="sk-gauge"><span class="m">${label}</span><div class="track"><div class="fill ${cls}" style="width:${pct}%"></div></div></div>`;
    const spark = (cls, seed) => {
      const pts = Array.from({ length: 24 }, (_, i) => `${(i * 100) / 23},${40 - (18 + 14 * Math.sin(i / 2.3 + seed) + 6 * Math.sin(i * 1.7 + seed))}`).join(" ");
      return `<svg class="spark ${cls}" viewBox="0 0 100 40" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;
    };
    return `<div class="sk-dash">
<div class="sk-panel">${gauge("load", 64, "a")}${gauge("memory", 42, "t")}${gauge("network", 27, "s")}</div>
<div class="sk-panel">${spark("a", 0.4)}</div>
<div class="sk-panel">${spark("t", 2.1)}</div>
<div class="sk-panel sk-list">${[70, 54, 62, 45].map((w, i) => `<div class="sk-row ${i === 0 ? "on" : ""}">${this.bar(w, i === 0 ? "hi" : "dim")}</div>`).join("")}</div>
</div>
<div class="bar"><span>tab focus</span><span>r refresh</span><span style="margin-left:auto">q quit</span></div>`;
  },

  // A purpose-built RexDeck view over CLI output or an API: queue + reader.
  view() {
    const item = (w, pill, on) =>
      `<div class="sk-item ${on ? "on" : ""}">${this.bar(w, on ? "hi" : "dim")}<span class="pill ${pill}"></span></div>`;
    return `<div class="sk-view">
<div class="sk-scope">${this.bar(38, "a")}<span class="fresh">fresh</span></div>
<div class="sk-columns">
<div class="sk-queue"><div class="sk-filter">/ filter</div>${item(74, "a", true)}${item(58, "s")}${item(66, "t")}${item(49, "m")}${item(61, "s")}</div>
<div class="sk-reader"><div class="sk-row">${this.bar(64, "fg")}</div><div class="sk-row"><span class="pill a"></span> ${this.bar(28, "dim")}</div>
${[94, 88, 91, 52].map((w) => `<div class="sk-row">${this.bar(w, "dim")}</div>`).join("")}
<div class="sk-note">${this.bar(70, "dim")}${this.bar(45, "dim")}</div></div>
</div></div>
<div class="bar"><span>enter read</span><span>r refresh</span><span>a review change</span><span style="margin-left:auto">q quit</span></div>`;
  },

  palette(query, sel) {
    const items = [
      ["Terminal tool", 46],
      ["Data view", 38],
      ["API view", 52],
      ["Terminal tool", 30],
      ["CLI output view", 42],
    ];
    const rows = items
      .map(([kind, w], i) => `<div class="prow ${i === sel ? "on" : ""}"><span>RexDeck <i class="sk inline ${i === sel ? "hi" : "dim"}" style="width:${w * 3}px"></i></span><span class="m">${kind}</span></div>`)
      .join("");
    return `<div class="pin"><span class="a">›</span> ${query}<span class="caret"></span></div>${rows}`;
  },
};
