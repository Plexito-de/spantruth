// The lock-file checker. It reads a lock file in this page and compares its versions with the
// nightly version table (versions.json, from the same site). The file never leaves the browser:
// the page sends one request, for versions.json, and nothing else.
//
// Every value from the file or the table goes into the page as text (textContent), never as
// markup, so a package name cannot inject HTML. The parsers mirror src/spantruth/doctor.py.

export const MAX_BYTES = 5_000_000;
export const SITE = "https://plexito-de.github.io/spantruth/";

/** The normalized distribution name, as in PEP 503: Foo_Bar.baz is foo-bar-baz. */
export function packageName(spec) {
  const name = spec.trim().split(/[\s<>=!~@[;(]/, 1)[0];
  return name.replace(/[-_.]+/g, "-").toLowerCase();
}

function tomlString(value) {
  const m = value.match(/^\s*"((?:[^"\\]|\\.)*)"|^\s*'([^']*)'/);
  return m ? (m[1] ?? m[2]) : null;
}

/** uv.lock, poetry.lock and pdm.lock: the name and version of every [[package]] table. */
export function parseTomlLock(text) {
  const out = new Map();
  let current = null;
  const close = () => {
    if (current && current.name) out.set(packageName(current.name), current.version ?? null);
  };
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith("[")) {
      if (line === "[[package]]") {
        close();
        current = { inTable: true };
      } else if (current) {
        current.inTable = false; // a sub-table such as [package.dependencies]
      }
      continue;
    }
    if (!current || !current.inTable) continue;
    const m = line.match(/^(name|version)\s*=\s*(.*)$/);
    if (m) current[m[1]] = tomlString(m[2]);
  }
  close();
  return out;
}

/** Pipfile.lock: the default and develop sections. */
export function parsePipfileLock(text) {
  const data = JSON.parse(text);
  const out = new Map();
  for (const section of ["default", "develop"]) {
    const rows = data && typeof data[section] === "object" && data[section] ? data[section] : {};
    for (const [name, row] of Object.entries(rows)) {
      const version = row && typeof row.version === "string" ? row.version.replace(/^==/, "") : "";
      out.set(packageName(name), version || null);
    }
  }
  return out;
}

/** requirements.txt: a pin per line, with backslash continuations and --hash options. */
export function parseRequirements(text) {
  const out = new Map();
  const joined = text.replace(/\\\r?\n/g, " ");
  for (let line of joined.split(/\r?\n/)) {
    line = line.replace(/\s--hash[=\s]\S+/g, "");
    line = line.split(" #", 1)[0].split(";", 1)[0].trim();
    if (!line || line.startsWith("#") || line.startsWith("-")) continue;
    const pinned = line.match(/^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*==\s*([^\s,]+)$/);
    const name = pinned ? packageName(pinned[1]) : packageName(line);
    if (!name || out.has(name)) continue;
    out.set(name, pinned ? pinned[2] : null);
  }
  return out;
}

/** The kind of file, from its name when there is one, else from its content. */
export function detect(text, filename = "") {
  const base = filename.split(/[\\/]/).pop().toLowerCase();
  if (base === "pipfile.lock") return "Pipfile.lock";
  if (["uv.lock", "poetry.lock", "pdm.lock"].includes(base)) return base;
  if (/requirements.*\.txt$/.test(base)) return "requirements.txt";
  const start = text.trimStart();
  if (start.startsWith("{")) return "Pipfile.lock";
  if (/^\[\[package\]\]\s*$/m.test(text)) {
    if (/^\s*\[metadata\][\s\S]*content-hash/m.test(text)) return "poetry.lock";
    if (/^\s*\[metadata\][\s\S]*lock_version/m.test(text)) return "pdm.lock";
    return "uv.lock";
  }
  return "requirements.txt";
}

export class LockError extends Error {}

/** The file's versions: {source, versions: Map of name to version or null}. */
export function parseLock(text, filename = "") {
  if (text.length > MAX_BYTES) throw new LockError("The file is larger than 5 MB.");
  const source = detect(text, filename);
  let versions;
  try {
    if (source === "Pipfile.lock") versions = parsePipfileLock(text);
    else if (source === "requirements.txt") versions = parseRequirements(text);
    else versions = parseTomlLock(text);
  } catch (err) {
    throw new LockError(`This does not read as a ${source}: ${err.message}`);
  }
  return { source, versions };
}

const VERSION = /^v?(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?$/;
const PHASE = { a: 0, b: 1, rc: 2 };

/** A sort key for a PEP 440 version, as history.version_key in Python. Null for anything else. */
export function versionKey(version) {
  const m = String(version).trim().toLowerCase().match(VERSION);
  if (!m) return null;
  const release = m[1].split(".").map(Number);
  while (release.length > 1 && release[release.length - 1] === 0) release.pop();
  const devOnly = m[5] !== undefined && m[2] === undefined && m[4] === undefined;
  return [
    release,
    devOnly ? -1 : m[2] !== undefined ? PHASE[m[2]] : 3,
    Number(m[3] ?? 0),
    m[4] !== undefined ? Number(m[4]) : -1,
    m[5] !== undefined ? Number(m[5]) : Number.MAX_SAFE_INTEGER,
  ];
}

/** -1, 0 or 1. A version that does not parse sorts first. */
export function compareVersions(a, b) {
  const ka = versionKey(a);
  const kb = versionKey(b);
  if (!ka || !kb) return ka ? 1 : kb ? -1 : 0;
  const [ra, ...ta] = ka;
  const [rb, ...tb] = kb;
  for (let i = 0; i < Math.max(ra.length, rb.length); i++) {
    const d = (ra[i] ?? 0) - (rb[i] ?? 0);
    if (d) return Math.sign(d);
  }
  for (let i = 0; i < ta.length; i++) if (ta[i] !== tb[i]) return Math.sign(ta[i] - tb[i]);
  return 0;
}

/** The table's subjects this project uses, with the rule of doctor.match. */
export function match(table, versions) {
  const has = (p) => versions.has(packageName(p));
  const found = [];
  for (const [name, s] of Object.entries(table.subjects || {})) {
    const named = (s.detect || []).some(has);
    const needed = !(s.detect_with || []).length || s.detect_with.some(has);
    if (named && needed) found.push(name);
  }
  // The Anthropic SDK traces itself when the app has an OpenTelemetry SDK (anthropic 1.14.0 on).
  const covered = found.some((n) => (table.subjects[n].providers || []).includes("anthropic"));
  if (has("anthropic") && has("opentelemetry-sdk") && !covered && table.subjects["anthropic-sdk"]) {
    found.push("anthropic-sdk");
  }
  return found;
}

/** The anchor of one finding on the report, as report.finding_id in Python. */
export function findingId(subject, scenario, check) {
  return `${subject}--${scenario.replaceAll("/", "--")}--${check}`.replace(/[^A-Za-z0-9._-]/g, "-");
}

/**
 * What the table says for one package at the project's version. `state` is one of: unpinned,
 * missing (the project does not have it), measured, older, newer, between (not one of the
 * releases the table ran).
 */
export function lookup(table, subject, packageNameValue, version) {
  const p = table.subjects?.[subject]?.packages?.[packageNameValue];
  const result = { subject, package: packageNameValue, version, pinned: p?.pinned ?? "" };
  if (!p) return { ...result, state: "missing" };
  if (version === undefined) return { ...result, state: "missing" };
  if (version === null) return { ...result, state: "unpinned" };
  const ran = (p.versions || []).filter(Boolean);
  const hit = ran.find((v) => compareVersions(v.version, version) === 0 && versionKey(version));
  // A fix counts for this version when it shipped after it.
  const fixes = (p.first_fixed || []).filter((f) => compareVersions(f.version, version) > 0);
  if (hit) {
    const failing = Object.entries(hit.failing || {}).flatMap(([scenario, checks]) =>
      Object.entries(checks).map(([check, bug]) => ({
        scenario,
        check,
        bug,
        fixedIn: fixes.find((f) => f.scenario === scenario && f.check === check)?.version ?? "",
      })),
    );
    return {
      ...result,
      state: hit.error ? "error" : "measured",
      entry: hit,
      failing,
      newest: ran.length ? ran[ran.length - 1].version : "",
      scenarios: (p.scenarios || []).length,
    };
  }
  const oldest = ran[0]?.version;
  const newest = ran[ran.length - 1]?.version;
  let state = "between";
  if (!ran.length) state = "between";
  else if (compareVersions(version, oldest) < 0) state = "older";
  else if (compareVersions(version, newest) > 0) state = "newer";
  return { ...result, state, oldest: oldest ?? "", newest: newest ?? "", fixes };
}

/** The failing checks grouped by bug class: scenarios, and the release that fixes them. */
export function byClass(failing) {
  const groups = new Map();
  for (const f of failing) {
    const g = groups.get(f.bug) ?? { bug: f.bug, scenarios: new Set(), fixed: [], unfixed: 0 };
    g.scenarios.add(f.scenario);
    if (f.fixedIn) g.fixed.push(f.fixedIn);
    else g.unfixed += 1;
    groups.set(f.bug, g);
  }
  return [...groups.values()]
    .sort((a, b) => Number(!a.bug) - Number(!b.bug) || a.bug.localeCompare(b.bug))
    .map((g) => ({
      bug: g.bug,
      scenarios: g.scenarios.size,
      fixedIn: g.fixed.sort(compareVersions).at(-1) ?? "",
      unfixed: g.unfixed,
    }));
}

/** The failing checks grouped by scenario, in scenario order. */
export function byScenario(failing) {
  const groups = new Map();
  for (const f of failing) {
    if (!groups.has(f.scenario)) groups.set(f.scenario, []);
    groups.get(f.scenario).push(f);
  }
  return [...groups.keys()].sort().map((scenario) => ({ scenario, checks: groups.get(scenario) }));
}

/** Everything the page shows for one parsed file. */
export function check(table, parsed) {
  const subjects = match(table, parsed.versions);
  const rows = subjects.map((name) => ({
    name,
    title: table.subjects[name].title,
    packages: Object.keys(table.subjects[name].packages || {})
      .map((pkg) => lookup(table, name, pkg, parsed.versions.get(pkg)))
      .filter((r) => r.state !== "missing"),
  }));
  const sdks = ["openai", "anthropic"]
    .filter((p) => parsed.versions.has(p))
    .map((p) => ({ name: p, version: parsed.versions.get(p) }));
  return { source: parsed.source, count: parsed.versions.size, subjects: rows, sdks };
}

// ---------------------------------------------------------------------------------------------
// The page. Nothing below runs under node.

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

function link(text, href) {
  return el("a", { href }, text);
}

function findingPage(row) {
  if (row.version && row.pinned && compareVersions(row.version, row.pinned) === 0) return "index.html";
  if (row.version && row.newest && compareVersions(row.version, row.newest) === 0) return "latest.html";
  return "";
}

function fixText(group, releases) {
  if (group.fixedIn && !group.unfixed) return `fixed in ${group.fixedIn}`;
  if (group.fixedIn) return `partly fixed in ${group.fixedIn}`;
  return `not fixed in the last ${releases} releases`;
}

function packageBlock(row, table) {
  const head = el("p", {}, el("code", {}, `${row.package} ${row.version ?? ""}`.trim()), " ");
  const block = el("div", { class: "pkg" }, head);
  if (row.state === "unpinned") {
    head.append(el("span", { class: "v inconclusive" }, "no version"));
    block.append(el("p", {}, "The file names this package with no exact version, so the table cannot answer. Pin it, or run doctor."));
    return block;
  }
  if (row.state === "error") {
    head.append(el("span", { class: "v inconclusive" }, "could not run"));
    block.append(el("p", {}, `The nightly run could not install or run this version: ${row.entry.error}`));
    return block;
  }
  if (row.state !== "measured") {
    head.append(el("span", { class: "v inconclusive" }, "not in the table"));
    const where = {
      older: `It is older than the releases the table covers (${row.oldest} to ${row.newest}).`,
      newer: `It is newer than the last release the table ran (${row.newest}).`,
      between: "The table did not run this version.",
    }[row.state];
    block.append(el("p", {}, `${where} Run doctor to check it on your machine.`));
    return block;
  }
  if (!row.failing.length) {
    head.append(el("span", { class: "v pass" }, "pass"));
    block.append(el("p", {}, `Every check passed in ${row.scenarios} scenarios, run on ${row.entry.run}.`));
    return block;
  }
  const scenarios = byScenario(row.failing);
  head.append(el("span", { class: "v wrong" }, `${scenarios.length} of ${row.scenarios} scenarios fail`));
  const names = table.classes || {};
  const classes = byClass(row.failing).map((g) =>
    el(
      "li",
      {},
      g.bug ? link(`${g.bug} ${names[g.bug] || ""}`.trim(), `bugs.html#${encodeURIComponent(g.bug)}`) : "Other checks",
      `: ${g.scenarios} scenario${g.scenarios === 1 ? "" : "s"}, `,
      el("span", { class: g.fixedIn ? "fix" : "muted" }, fixText(g, table.releases)),
    ),
  );
  block.append(el("ul", { class: "classes" }, ...classes));
  const page = findingPage(row);
  const body = scenarios.map((s) => {
    const checks = el("td", {});
    s.checks.forEach((f, i) => {
      if (i) checks.append(", ");
      checks.append(page ? link(f.check, `${page}#${findingId(row.subject, s.scenario, f.check)}`) : f.check);
      if (f.fixedIn) checks.append(el("span", { class: "muted" }, ` (fixed in ${f.fixedIn})`));
    });
    return el("tr", {}, el("td", {}, el("code", {}, s.scenario)), checks);
  });
  const header = el("tr", {}, ...["Scenario", "Failing checks"].map((h) => el("th", { scope: "col" }, h)));
  block.append(
    el(
      "details",
      {},
      el("summary", {}, `Every failing check, by scenario (${row.failing.length})`),
      el("div", { class: "wrap" }, el("table", {}, el("thead", {}, header), el("tbody", {}, ...body))),
    ),
  );
  block.append(el("p", { class: "muted" }, `Run on ${row.entry.run}. Released ${row.entry.released || "on an unknown date"}.`));
  return block;
}

function render(out, table, result) {
  out.replaceChildren();
  out.append(el("p", {}, `Read ${result.source}: ${result.count} packages. Nothing was sent anywhere.`));
  if (!result.subjects.length) {
    const known = Object.values(table.subjects)
      .flatMap((s) => s.detect || [])
      .sort();
    out.append(
      el("p", {}, "No instrumentation library that spantruth tests is in this file. It recognizes: ", known.join(", "), "."),
    );
  }
  for (const s of result.subjects) {
    const section = el("section", { class: "result" }, el("h2", {}, s.title));
    if (!s.packages.length) {
      section.append(el("p", {}, "The file has none of the packages the table runs for this library. Run doctor."));
    }
    for (const row of s.packages) section.append(packageBlock(row, table));
    out.append(section);
  }
  if (result.sdks.length) {
    const items = result.sdks.map((s) => el("li", {}, el("code", {}, `${s.name} ${s.version ?? "(no version)"}`)));
    out.append(
      el(
        "section",
        { class: "result" },
        el("h2", {}, "Provider SDKs"),
        el("ul", {}, ...items),
        el(
          "p",
          { class: "muted" },
          "The table runs each library with the SDK versions that spantruth pins, so these versions are not part of the answer above. Since anthropic 1.14.0 the Anthropic SDK also traces itself: ",
          link("why that can count a call twice", "double-spans.html"),
          ".",
        ),
      ),
    );
  }
  const meta = el(
    "p",
    { class: "muted" },
    `Table of ${String(table.created).slice(0, 10)}, spantruth ${table.spantruth}, the last ${table.releases} releases of each library. `,
    link("The table as JSON", "versions.json"),
    ".",
  );
  out.append(meta);
}

async function readFile(file) {
  if (file.size > MAX_BYTES) throw new LockError("The file is larger than 5 MB.");
  return { text: await file.text(), name: file.name };
}

export function start() {
  const form = document.getElementById("form");
  const input = document.getElementById("lock");
  const picker = document.getElementById("file");
  const out = document.getElementById("result");
  const status = document.getElementById("status");
  let filename = "";
  const tablePromise = fetch("versions.json", { credentials: "omit" }).then((r) => {
    if (!r.ok) throw new Error(`versions.json answered ${r.status}`);
    return r.json();
  });

  async function run(text) {
    status.textContent = "";
    try {
      const table = await tablePromise;
      const parsed = parseLock(text, filename);
      render(out, table, check(table, parsed));
      out.focus();
    } catch (err) {
      out.replaceChildren();
      status.textContent =
        err instanceof LockError ? err.message : `The version table could not load: ${err.message}`;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!input.value.trim()) {
      status.textContent = "Paste a lock file or choose one first.";
      input.focus();
      return;
    }
    run(input.value);
  });
  input.addEventListener("input", () => {
    filename = "";
  });
  async function take(file) {
    try {
      const { text, name } = await readFile(file);
      input.value = text;
      filename = name;
      run(text);
    } catch (err) {
      status.textContent = err.message;
    }
  }
  picker.addEventListener("change", () => picker.files[0] && take(picker.files[0]));
  for (const ev of ["dragenter", "dragover"]) {
    input.addEventListener(ev, (e) => {
      e.preventDefault();
      input.classList.add("drop");
    });
  }
  for (const ev of ["dragleave", "drop"]) input.addEventListener(ev, () => input.classList.remove("drop"));
  input.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (file) take(file);
  });
}

if (typeof document !== "undefined") start();
