"use strict";

(() => {
  const raw = "https://raw.githubusercontent.com/ShadyF4RR/Stockfish/refs/heads/research/reporting/";
  const freshSeconds = 900;
  const cacheKey = "stockfish.lab.dashboard.v1";
  const activeStates = new Set(["active", "running", "testing", "in_progress", "building", "benchmarking"]);
  const colors = {
    good: ["fresh snapshot", "online", "testing", "validated", "promoted", "passed", "operational"],
    info: ["active", "running", "screened", "documented", "completed", "idle", "in progress"],
    warn: ["stale snapshot", "cached snapshot", "stale", "inconclusive", "partial", "pending", "paused"],
    bad: ["offline", "rejected", "failed", "unavailable", "disconnected"]
  };
  const $ = id => document.getElementById(id);
  const list = value => Array.isArray(value) ? value.filter(v => v && typeof v === "object") : [];
  const words = value => typeof value === "string" && value.trim() ? value.trim() : "";
  const num = value => typeof value === "number" && Number.isFinite(value);
  const format = (v, maximumFractionDigits = 1) => num(v)
    ? v.toLocaleString(undefined, {maximumFractionDigits}) : "—";
  const label = value => words(value).replace(/[_-]+/g, " ") || "Unknown";
  const parsed = t => typeof t === "string" ? Date.parse(t) : NaN;
  const age = t => (Date.now() - parsed(t)) / 1000;
  const trustedTime = t => Number.isFinite(age(t)) && age(t) >= -120;
  const duration = sec => {
    if (!num(sec) || sec < 0) return "—";
    if (sec < 60) return Math.floor(sec) + "s";
    const mins = Math.floor(sec / 60);
    if (mins < 60) return mins + "m";
    if (mins < 1440) return Math.floor(mins / 60) + "h " + mins % 60 + "m";
    return Math.floor(mins / 1440) + "d " + Math.floor(mins % 1440 / 60) + "h";
  };
  const time = t => trustedTime(t) ? new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short", timeZone: "UTC"
  }).format(new Date(t)) + " UTC" : "Unavailable";
  function node(tag, value, cls) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== null && value !== undefined) el.textContent = value;
    return el;
  }
  function box(tag, cls, ...children) {
    const el = node(tag, null, cls);
    el.append(...children.filter(Boolean));
    return el;
  }
  function badge(value) {
    const title = label(value);
    const tone = Object.keys(colors).find(c => colors[c].includes(title.toLowerCase())) || "neutral";
    return node("span", title, "badge " + tone);
  }
  function safeHref(href) {
    if (!words(href)) return null;
    try {
      const url = new URL(href);
      if (url.protocol !== "https:" || url.username || url.password) return null;
      return url.href;
    } catch { return null; }
  }
  function link(title, href) {
    const url = safeHref(href);
    if (!url) return node("span", title);
    const anchor = node("a", title);
    anchor.href = url;
    anchor.rel = "noreferrer";
    return anchor;
  }
  const empty = text => node("p", text, "empty");
  function fields(items, cls = "details") {
    return box("dl", cls, ...items.map(([k,v]) =>
      box("div", "", node("dt", k), node("dd", v ?? "—"))));
  }
  function elo(record) {
    if (!record || !num(record.estimate)) return "Unavailable";
    let out = (record.estimate > 0 ? "+" : "") + format(record.estimate) + " Elo";
    if (num(record.lower) && num(record.upper)) {
      const level = num(record.confidence) ? format(record.confidence <= 1 ? 100 * record.confidence : record.confidence) + "% " : "";
      out += " (" + level + "CI " + format(record.lower) + " to " + format(record.upper) + ")";
    }
    return out;
  }
  function wdl(record) {
    return record && [record.wins, record.draws, record.losses].every(num)
      ? [record.wins, record.draws, record.losses].map(v => format(v, 0)).join(" / ") : "—";
  }
  function performance(exp) {
    return list(exp.performance).filter(v => num(v.value)).map(v => [
      words(v.label) || "Performance", format(v.value, 2) + (words(v.unit) ? " " + v.unit : "")
    ]);
  }
  function links(exp) {
    const targets = list(exp.links).filter(v => safeHref(v.url));
    if (safeHref(exp.report_url)) targets.unshift({label:"Experiment report",url:exp.report_url});
    if (!targets.length) return null;
    return box("div", "footline", ...targets.map(v => link(words(v.label) || "Evidence", v.url)));
  }
  function statusText(value) {
    if (words(value)) return words(value);
    if (!value || typeof value !== "object") return "Unavailable";
    return words(value.status) || words(value.result) || words(value.conclusion) || "See report";
  }
  let snapshot = null, telemetry = null, source = "none", telemetrySource = "none";
  let lastFetchFailed = false, busy = false, attempted = false;
  let lastRendered = "";
  function serverShape(value) {
    return value && value.schema_version === 1 && trustedTime(value.generated_at)
      && Array.isArray(value.experiments) && Array.isArray(value.workers);
  }
  function workerShape(value) {
    return value && value.schema_version === 1 && trustedTime(value.updated_at) && Array.isArray(value.workers);
  }
  function readCache() {
    try {
      const data = JSON.parse(localStorage.getItem(cacheKey));
      if (serverShape(data?.snapshot)) { snapshot = data.snapshot; source = "cache"; }
      if (workerShape(data?.telemetry)) { telemetry = data.telemetry; telemetrySource = "cache"; }
    } catch { /* Offline/private windows can disable storage. */ }
  }
  function saveCache() {
    try { localStorage.setItem(cacheKey, JSON.stringify({snapshot, telemetry})); }
    catch { /* Storage is an optional fallback. */ }
  }
  async function json(url, validate) {
    const request = new URL(url, location.href);
    request.searchParams.set("t", String(Date.now()));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(request, {cache:"no-store", credentials:"omit", referrerPolicy:"no-referrer", signal:controller.signal});
      if (!response.ok) throw new Error("HTTP " + response.status);
      const output = await response.json();
      if (!validate(output)) throw new Error("Invalid reporting document");
      return output;
    } finally { clearTimeout(timer); }
  }
  function accept(data, kind, origin) {
    if (kind === "snapshot") {
      if (!snapshot || parsed(data.generated_at) >= parsed(snapshot.generated_at)) {
        snapshot = data; source = origin;
      }
    } else if (!telemetry || parsed(data.updated_at) >= parsed(telemetry.updated_at)) {
      telemetry = data; telemetrySource = origin;
    }
  }
  function workerState(worker) {
    const observed = worker.seen;
    if (!trustedTime(observed)) return words(worker.status) === "unregistered" ? "Unregistered" : "Unknown";
    const limit = num(worker.offline_after_seconds) && worker.offline_after_seconds > 0 ? worker.offline_after_seconds : freshSeconds;
    if (age(observed) > limit) return "Offline";
    return label(worker.status);
  }
  function workers() {
    const joined = new Map();
    for (const w of list(snapshot?.workers)) {
      if (words(w.id)) joined.set(w.id, {...w, seen:w.last_seen || w.updated_at, origin:source});
    }
    for (const w of list(telemetry?.workers)) {
      if (!words(w.id) || !trustedTime(w.updated_at)) continue;
      const old = joined.get(w.id);
      if (old && trustedTime(old.seen) && parsed(w.updated_at) < parsed(old.seen)) continue;
      joined.set(w.id, {...w, seen:w.updated_at, origin:telemetrySource,
        ram_used_bytes:num(w.ram_used_mib) ? w.ram_used_mib * 1048576 : null,
        ram_total_bytes:num(w.ram_total_mib) ? w.ram_total_mib * 1048576 : null});
    }
    return Array.from(joined.values());
  }
  function mergeExperiments(workerRows) {
    const all = new Map(list(snapshot?.experiments).filter(e => words(e.id)).map(e => [e.id, {...e}]));
    const associated = new Map();
    for (const w of workerRows) {
      if (words(w.experiment_id) && ["testing","idle"].includes(w.status) && trustedTime(w.seen)) {
        if (!associated.has(w.experiment_id)) associated.set(w.experiment_id, []);
        associated.get(w.experiment_id).push(w);
      }
    }
    for (const [id, members] of associated) {
      const w = members[0];
      const e = all.get(id);
      if (e && !activeStates.has(e.status) && trustedTime(e.updated_at) && parsed(e.updated_at) >= parsed(w.seen)) continue;
      if (e && !activeStates.has(e.status) && !["queued","proposed"].includes(e.status)) continue;
      if (!e && w.status === "idle") continue;
      const combined = e ? {...e} : {id, started_at:w.started_at};
      if (members.length === 1 && (!trustedTime(combined.updated_at) || parsed(w.seen) >= parsed(combined.updated_at))) {
        const newer = !e || !trustedTime(e.updated_at) || parsed(w.seen) > parsed(e.updated_at);
        if (w.status === "testing") combined.status = workerState(w) === "Offline" ? "Last reported testing" : "testing";
        if (newer && num(w.completed_games) && w.completed_games !== e?.games_completed) {
          combined.wdl = null;
          combined.elo = null;
          combined.groups = [];
        }
        combined.updated_at = w.seen;
        if (trustedTime(combined.started_at)) combined.duration_seconds = (parsed(w.seen) - parsed(combined.started_at)) / 1000;
        if (num(w.completed_games)) combined.games_completed = w.completed_games;
        if (num(w.planned_games)) combined.games_planned = w.planned_games;
        if (num(w.remaining_games)) combined.games_remaining = w.remaining_games;
        if (num(w.nps)) combined.worker_nps = w.nps;
        if (Array.isArray(w.groups) && w.groups.length > 0) {
          const totals = list(w.groups).reduce((n,g) => n + (num(g.games) ? g.games : 0), 0);
          // Only one candidate arm can be attributed without an experiment-level grouping rule.
          combined.wdl = w.groups.length === 1 && totals === w.completed_games ? w.groups[0] : null;
          combined.groups = w.groups;
        }
        combined.progress_source = workerState(w) === "Offline" ? "Last worker heartbeat · offline"
          : w.status === "idle" ? "Worker idle · awaiting experiment report"
          : w.origin === "remote" ? "Worker heartbeat" : "Cached worker heartbeat";
      }
      all.set(id, combined);
    }
    return Array.from(all.values());
  }
  function active(exp) { return activeStates.has(exp.status) || exp.status === "Last reported testing"; }
  function groupsView(items) {
    const groups = list(items);
    if (!groups.length) return null;
    const details = node("details",null,"footline group-detail");
    details.append(node("summary","Match groups (" + groups.length + ")"));
    for (const g of groups) details.append(node("p",
      [words(g.arm),words(g.opponent),words(g.time_control),words(g.split)].filter(Boolean).join(" · ")
      + " · " + format(g.games) + " games · W/D/L " + wdl(g)
      + (num(g.abnormal_games) ? " · " + format(g.abnormal_games) + " abnormal" : "")));
    return details;
  }
  function experimentCard(e) {
    const el = node("article", null, "card active-card");
    el.append(box("div","line-head",node("h3",e.id),badge(e.status)));
    if (words(e.hypothesis)) el.append(node("p",e.hypothesis,"text"));
    const percent = num(e.games_planned) && e.games_planned > 0 && num(e.games_completed)
      ? Math.max(0, Math.min(100, 100 * e.games_completed / e.games_planned)) : null;
    if (percent !== null) {
      const progress = node("progress"); progress.max = 100; progress.value = percent;
      progress.setAttribute("aria-label","Games completed for " + e.id);
      el.append(box("div","progress",progress,node("span",format(percent,0) + "%","small")));
    }
    el.append(fields([["Games played", num(e.games_planned) ? format(e.games_completed) + " / " + format(e.games_planned) : format(e.games_completed)],
      ["Games remaining",format(e.games_remaining)],["Wins / draws / losses",wdl(e.wdl)],["Estimated Elo",elo(e.elo)]],"results"));
    const reportedDuration = num(e.duration_seconds) ? e.duration_seconds
      : trustedTime(e.updated_at) && trustedTime(e.started_at) ? (parsed(e.updated_at)-parsed(e.started_at))/1000 : null;
    el.append(fields([["Started",time(e.started_at)],["Reported duration",duration(reportedDuration)],["Last update",time(e.updated_at)],
      ...performance(e), ...(num(e.worker_nps) ? [["Worker NPS",format(e.worker_nps)]] : [])]));
    const groups = groupsView(e.groups); if (groups) el.append(groups);
    const foot = links(e);
    if (foot) el.append(foot);
    if (words(e.progress_source)) el.append(node("p",e.progress_source,"footline"));
    return el;
  }
  function workerCard(w) {
    const state = workerState(w);
    const el = node("article",null,"card");
    el.append(box("div","line-head",node("h3",w.id === "pc" ? "Dedicated Windows worker · pc" : w.id),badge(state)));
    const descriptors = [words(w.experiment_id), words(w.model || w.cpu_model), words(w.os)].filter(Boolean);
    if (descriptors.length) el.append(node("p",descriptors.join(" · "),"text"));
    const memory = num(w.ram_used_bytes) ? format(w.ram_used_bytes/1073741824,2) + " GiB" : "—";
    el.append(fields([["CPU usage",num(w.cpu_percent) ? format(w.cpu_percent) + "%" : "—"],
      ["RAM used / total",memory + " / " + (num(w.ram_total_bytes) ? format(w.ram_total_bytes/1073741824,2)+" GiB" : "—")],
      ["Temperature",num(w.temperature_c) ? format(w.temperature_c)+" °C" : "—"],
      ["Nodes per second",format(w.nps)],["Last heartbeat",time(w.seen)]]));
    if (num(w.completed_games) || num(w.planned_games)) el.append(fields([
      ["Games completed",format(w.completed_games)],["Remaining",format(w.remaining_games)],["Planned",format(w.planned_games)]]));
    const groups = groupsView(w.groups); if (groups) el.append(groups);
    if (!trustedTime(w.seen)) el.append(node("p","No verified heartbeat.","footline"));
    else if (state === "Offline" || w.origin !== "remote") el.append(node("p","Measurements from the last recorded heartbeat.","footline"));
    if (words(w.metric_scope)) el.append(node("p",w.metric_scope,"footline"));
    else if (w.metric_scope && typeof w.metric_scope === "object") {
      const scopes = Object.entries(w.metric_scope).filter(([,v])=>words(v))
        .map(([k,v])=>label(k) + " · " + v);
      if (scopes.length) el.append(node("p",scopes.join(" / "),"footline"));
    }
    return el;
  }
  function table(expList) {
    const table = node("table");
    const head = node("tr");
    for (const t of ["Experiment","Outcome","Games","W / D / L","Estimated Elo"]) {
      const th = node("th",t); th.scope = "col"; head.append(th);
    }
    table.append(box("thead","",head));
    const body = node("tbody");
    for (const e of expList) {
      const details = node("details"); details.dataset.id = e.id;
      const inside = node("div",null,"report-detail");
      if (words(e.hypothesis)) inside.append(node("p",e.hypothesis));
      if (words(e.conclusion)) inside.append(node("p",e.conclusion));
      inside.append(fields([["Started",time(e.started_at)],["Last update",time(e.updated_at)],["Duration",duration(e.duration_seconds)],
        ["Baseline commit",words(e.baseline_commit) || "Unavailable"],["Candidate commit",words(e.candidate_commit) || "Unavailable"],
        ["Build",statusText(e.build)],["Correctness",statusText(e.correctness)],...performance(e)]));
      if (Array.isArray(e.source_changes) && e.source_changes.some(words))
        inside.append(node("p","Changed files: " + e.source_changes.filter(words).join(", "),"meta"));
      const groups = groupsView(e.groups); if (groups) inside.append(groups);
      const refs = links(e); if (refs) inside.append(refs);
      details.append(node("summary",e.id),inside);
      const tr = box("tr","",box("td","",details),box("td","",badge(e.status)));
      for (const [title,value] of [["Games",format(e.games_completed)],["W / D / L",wdl(e.wdl)],["Estimated Elo",elo(e.elo)]]) {
        const td = node("td",value,"tabnums"); td.dataset.label=title; tr.append(td);
      }
      body.append(tr);
    }
    table.append(body);
    return table;
  }
  function setRows(id, rows, convert, message) {
    const entries = list(rows);
    $(id).replaceChildren(...(entries.length ? entries.map(convert) : [empty(message)]));
  }
  function history(exps) {
    const select = $("history-filter"), current = select.value;
    const statuses = [...new Set(exps.map(e => words(e.status)).filter(Boolean))].sort();
    const allowed = ["all", ...statuses];
    if (allowed.join("|") !== Array.from(select.options,o => o.value).join("|")) {
      select.replaceChildren(...allowed.map(v => { const o = node("option",v === "all" ? "All records" : label(v)); o.value=v; return o; }));
      if (allowed.includes(current)) select.value=current;
    }
    const open = new Set(Array.from($("history-table").querySelectorAll("details[open]"), d => d.dataset.id));
    const picked = exps.filter(e => select.value === "all" || e.status === select.value);
    $("history-table").replaceChildren(picked.length ? table(picked) : empty("No experiment records published for this selection."));
    $("history-table").querySelectorAll("details").forEach(d => { d.open = open.has(d.dataset.id); });
  }
  function render(force = false) {
    const wr = workers(), exps = mergeExperiments(wr);
    const fresh = trustedTime(snapshot?.generated_at) && age(snapshot.generated_at) <= freshSeconds;
    const status = !snapshot ? (attempted ? "Unavailable" : "Loading") : source !== "remote" || lastFetchFailed ? "Cached snapshot" : fresh ? "Fresh snapshot" : "Stale snapshot";
    const key = [snapshot?.generated_at, telemetry?.updated_at, source, telemetrySource, status, lastFetchFailed, busy,
      wr.map(w => workerState(w)).join(","), $("history-filter").value].join("|");
    if (!force && key===lastRendered) return;
    lastRendered=key;
    const statusBadge=badge(status);
    $("sync-status").textContent=statusBadge.textContent;
    $("sync-status").className=statusBadge.className;
    $("source-line").textContent = snapshot ? "Snapshot published " + time(snapshot.generated_at) : "No verified research snapshot available.";
    $("refresh").disabled=busy;
    $("refresh").setAttribute("aria-busy",String(busy));
    const warning = !snapshot && attempted ? "The reporting branch and deployment snapshot are unavailable. Verify your connection or try Refresh."
      : lastFetchFailed ? "Reporting branch unavailable. Displaying the last successful publication and any available worker heartbeat."
      : snapshot && !fresh ? "This research snapshot is older than 15 minutes. Each worker has its own heartbeat timestamp." : "";
    $("connection").textContent=warning;
    $("connection").hidden=!warning;
    const live = exps.filter(active);
    const published = !!snapshot, knownWorkers = published || !!telemetry;
    const liveCount = live.filter(e => activeStates.has(e.status)).length;
    const knownOnline = wr.filter(w => ["online","testing","idle"].includes(workerState(w).toLowerCase())).length;
    const activeAgents = list(snapshot?.agents).filter(a => ["active","running","working","testing","assigned","in_progress"].includes(a.status)).length;
    $("metrics").replaceChildren(...[
      ["Active experiments", published ? format(liveCount) : "—"],["Workers online",knownWorkers ? format(knownOnline) : "—"],
      ["Active assignments",Array.isArray(snapshot?.agents) ? format(activeAgents) : "—"],
      ["Promotions",Array.isArray(snapshot?.promotions) ? format(snapshot.promotions.length) : "—"]
    ].map(([k,v])=>box("div","",node("dt",k),node("dd",v))));
    $("active-list").replaceChildren(...(live.length ? live.map(experimentCard) : [empty(published ? "No experiments reported active." : "Active experiments unavailable.")]));
    const workerCards=wr.map(workerCard);
    if (knownWorkers && !wr.some(w=>w.id === "pc")) {
      workerCards.push(box("article","card",
        box("div","line-head",node("h3","Dedicated Windows worker · pc"),badge("Unregistered")),
        node("p","Configured for Intel Core Ultra 5 325 · 8 cores · 16 GB RAM · Windows 11.","text"),
        node("p","No heartbeat from this worker has been published.","footline")));
    }
    $("worker-list").replaceChildren(...(workerCards.length ? workerCards : [empty("Worker registry unavailable.")]));
    setRows("agent-list",snapshot?.agents,a => box("div","row",
      box("div","line-head",node("strong",words(a.role)||"Agent"),badge(a.status)),
      a.task ? node("p",a.task) : null,node("p",[words(a.experiment_id), "Updated " + time(a.updated_at)].filter(Boolean).join(" · "),"meta")
    ),"No agent assignments published.");
    history(exps);
    setRows("promotions",snapshot?.promotions,p => box("div","row",link(words(p.id)||"Promotion",p.url),
      node("p", "Promoted " + time(p.at) + (num(p.lower_elo) ? " · Elo lower bound " + format(p.lower_elo) : ""),"meta")
    ),"No statistically validated promotions published.");
    setRows("fishtest",snapshot?.fishtest,f => box("div","row",
      box("div","line-head",link([words(f.experiment_id),words(f.stage)].filter(Boolean).join(" · ")||"Fishtest record",f.url),badge(f.status)),
      node("p",["Games " + format(f.games), num(f.elo) ? format(f.elo) + " Elo" : "", words(f.run_id), time(f.at)].filter(Boolean).join(" · "),"meta")
    ),"No Fishtest submissions published.");
    const activity = snapshot?.activity;
    for (const [id, entries] of [["commits",activity?.commits],["pulls",activity?.pull_requests]]) {
      setRows(id,entries,c => box("div","row",link(words(c.title)||"GitHub record",c.url),node("p",time(c.at),"meta")),
        activity?.status === "supplied" ? "No entries in the published activity window." : "GitHub activity unavailable in this snapshot.");
    }
    const s = snapshot?.scheduler;
    $("scheduler").replaceChildren(s ? box("div","row",box("div","line-head",badge(s.status),s.url ? link("Workflow",s.url) : null),
      words(s.detail || s.description) ? node("p",words(s.detail || s.description)) : null,node("p","Updated " + time(s.updated_at),"meta"))
      : empty("Scheduler status unavailable."));
    setRows("notifications",snapshot?.notifications,n => box("div","row",link(words(n.message)||label(n.kind),n.url),node("p",time(n.at),"meta")),
      "No notification events published.");
  }
  async function refresh() {
    if (busy) return;
    busy=true; render();
    const branch = json(raw + "snapshot.json",serverShape).then(d => accept(d,"snapshot","remote"));
    const workersReq = json(raw + "research-lab/dashboard/telemetry.json",workerShape)
      .then(d => accept(d,"telemetry","remote"));
    const bootstrap = !snapshot && location.protocol !== "file:" ? json("./snapshot.json",serverShape)
      .then(d => { if (!snapshot || parsed(d.generated_at) > parsed(snapshot.generated_at)) accept(d,"snapshot","deployment"); render(); }) : Promise.resolve();
    const [snapshotRead,workerRead] = await Promise.allSettled([branch,workersReq,bootstrap]);
    lastFetchFailed=snapshotRead.status === "rejected";
    if (workerRead.status === "rejected" && telemetry) telemetrySource="cache";
    if (lastFetchFailed && source === "remote") source="cache";
    busy=false; attempted=true; saveCache(); render(true);
  }
  $("refresh").addEventListener("click",refresh);
  $("history-filter").addEventListener("change",()=>render(true));
  window.addEventListener("online",refresh);
  document.addEventListener("visibilitychange",()=>{ if (!document.hidden) refresh(); });
  readCache(); render(); refresh();
  setInterval(()=>{ if (!document.hidden) refresh(); },60000);
  setInterval(()=>{ if (!document.hidden) render(); },15000);
})();
