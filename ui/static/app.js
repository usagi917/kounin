// 画面。文字は全部 textContent で入れる。セルの文字列を HTML として解釈しない。
"use strict";

const LABELS = { today: "今月", inbox: "確認", ask: "kounin に聞く", change: "変更", pay: "支給", history: "履歴" };
const STAGES = [["計算", "today"], ["確認", "inbox"], ["3案", "pay"], ["決定", "pay"], ["CSV", "pay"]];
const PULSE_MS = 2500;
const state = { session: null, pulse: null, drawn: null, quiet: false, shownStage: 0, seenRun: null, prevGroups: null,
  fresh: {}, drafts: {}, open: {}, meters: {} };

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : String(child));
  }
  return node;
}

const plain = (value) => Math.round(value).toLocaleString("ja-JP");
const yen = (value) => `${plain(value)}円`;
const signed = (value) => `${value > 0 ? "+" : value < 0 ? "−" : "±"}${Math.abs(value).toLocaleString("ja-JP")}円`;
const monthLabel = (month) => { const [y, m] = month.split("-"); return `${y}年${Number(m)}月分`; };
const dayLabel = (text) => { const [y, m, d] = text.split("-"); return `${y}年${Number(m)}月${Number(d)}日`; };
const jst = (text) => new Date(text).toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", dateStyle: "short", timeStyle: "short" });
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const moving = () => !matchMedia("(prefers-reduced-motion: reduce)").matches;
const afterPaint = (work) => requestAnimationFrame(() => requestAnimationFrame(work));

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method === "POST" && state.session && state.session.csrf) headers["X-CSRF-Token"] = state.session.csrf;
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401) { await boot(); throw new Error("ログインしてください"); }
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) {
    if (!response.ok) throw new Error("うまくいきませんでした");
    return response;
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "うまくいきませんでした");
  return data;
}
const clearHash = () => history.replaceState(null, "", location.pathname);
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

function problem(message) {
  return h("div", { class: "errors rise", role: "alert" }, h("b", null, "できませんでした"), h("span", null, message));
}

// 押している間は二重に送らない。失敗は画面の言葉で出す。
function action(button, slot, work) {
  return async () => {
    button.disabled = true;
    slot.replaceChildren();
    try { await work(); }
    catch (error) { slot.replaceChildren(problem(error.message)); }
    finally { button.disabled = false; }
  };
}

// 動きの部品。動きを減らす設定と、裏で画面を更新する時は動かさない。
function num(value, format = plain) {
  const node = h("span", { class: "num" }, format(value));
  if (state.quiet || !moving()) return node;
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - start) / 900);
    node.textContent = format(value * (1 - Math.pow(1 - t, 3)));
    if (t < 1) requestAnimationFrame(tick);
    else node.textContent = format(value);
  };
  node.textContent = format(0);
  requestAnimationFrame(tick);
  return node;
}
function meter(key, ratio) {
  const fill = h("i", { style: `width:${(state.meters[key] ?? 0) * 100}%` });
  afterPaint(() => { fill.style.width = `${ratio * 100}%`; });
  state.meters[key] = ratio;
  return h("div", { class: "meter", role: "progressbar", "aria-valuenow": Math.round(ratio * 100), "aria-valuemin": 0, "aria-valuemax": 100 }, fill);
}
function mark() {
  const ns = "http://www.w3.org/2000/svg";
  const icon = document.createElementNS(ns, "svg");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", "M5 12.5l4.5 4.5L19 7.5");
  icon.append(path);
  return icon;
}
const avatar = (name) => h("span", { class: "avatar", "aria-hidden": "true" }, name.slice(0, 1));
const pips = (count) => h("span", { class: "pips", "aria-hidden": "true" }, Array.from({ length: Math.min(count, 24) }, () => h("i")));
function toast(text) {
  const node = h("div", { class: "toast", role: "status" }, mark(), h("span", null, text));
  document.body.append(node);
  setTimeout(() => node.remove(), 2800);
}
function folded(key, title, ...children) {
  return h("details", { open: state.open[key], ontoggle: (event) => { state.open[key] = event.target.open; } }, h("summary", null, title), h("div", { class: "fold" }, children));
}
function kept(key, attrs) {
  const box = h("textarea", attrs);
  box.value = state.drafts[key] || "";
  box.addEventListener("input", () => { state.drafts[key] = box.value; });
  return box;
}

// 枠
function flowBar() {
  const stage = state.pulse ? state.pulse.stage : 0;
  const ratio = (value) => `${(Math.min(value, STAGES.length - 1) / (STAGES.length - 1)) * 100}%`;
  const fill = h("i", { style: `width:${ratio(state.shownStage)}` });
  afterPaint(() => { fill.style.width = ratio(stage); });
  state.shownStage = stage;
  return h("nav", { class: "flow", "aria-label": "今月の工程" }, h("div", { class: "flow-track" }, fill),
    STAGES.map(([label, screen], index) => h("a", { href: `#${screen}`, class: `step${index < stage ? " done" : index === stage ? " now" : ""}`, "aria-current": index === stage ? "step" : null },
      h("span", { class: "dotmark" }, index < stage ? mark() : index + 1), h("span", null, label))));
}

function frame(screen, content) {
  const { user, screens, environment } = state.session;
  const nav = screens.map((name) => h("a", { href: `#${name}`, "aria-current": name === screen ? "page" : null }, LABELS[name]));
  const logout = h("button", { class: "ghost", onclick: async () => { await post("/api/logout"); clearHash(); await boot(); } }, "ログアウト");
  document.getElementById("app").replaceChildren(h("div", { class: `page${state.quiet ? " quiet" : ""}` },
    h("header", { class: "top" }, h("a", { class: "brand", href: "#" }, "kounin"), h("nav", { class: "nav", "aria-label": "画面" }, nav),
      h("div", { class: "who" }, avatar(user.name), h("b", null, user.name), logout)),
    flowBar(),
    h("main", { class: `main${state.quiet ? "" : " rise"}` }, content),
    h("footer", { class: "foot" }, h("span", null, "法令判断は参考情報です"), h("span", null, environment))));
}

function heading(eyebrow, title) {
  return h("div", null, h("div", { class: "eyebrow" }, eyebrow), h("h1", null, title));
}
const stat = (label, value, unit) => h("div", { class: "stat" }, h("dt", null, label), h("dd", null, value, unit ? h("small", null, ` ${unit}`) : null));

// 全職員の点。1人1点。個人は分からず、職場と状態だけを見せる。
function dotField(groups, intro) {
  let index = 0;
  const before = state.prevGroups || {};
  return h("div", { class: `field${intro ? " intro" : ""}` }, groups.map((group) => {
    const dots = [];
    for (let i = 0; i < group.total; i += 1) {
      const waiting = i >= group.total - group.waiting;
      const solved = !waiting && before[group.name] !== undefined && i >= group.total - before[group.name];
      dots.push(h("i", { class: `dot${waiting ? " wait" : ""}${solved ? " solved" : ""}`, style: `--i:${index}` }));
      index += 1;
    }
    return h("section", { class: "zone" }, h("div", { class: "zone-head" }, h("span", null, group.name), h("small", null, group.waiting ? `確認 ${group.waiting}人 / ${group.total}人` : `${group.total}人`)), h("div", { class: "dots" }, dots));
  }));
}
function ghostField(count) {
  return h("div", { class: "field blank" }, h("div", { class: "dots" }, Array.from({ length: count }, (_, index) => h("i", { class: "dot", style: `--i:${index}` }))));
}
const legend = () => h("div", { class: "legend" }, h("span", null, h("i", { class: "dot" }), "計算できた"), h("span", null, h("i", { class: "dot wait still" }), "確認が必要"));

// 今月
async function todayScreen() {
  const data = await api("/api/today");
  const run = data.run;
  if (!run || data.stale) {
    const slot = h("div");
    const title = h("h1", null, data.stale ? "新しいデータが届きました。" : "今月分を計算します。");
    const field = ghostField(run ? run.total : data.headcount);
    const button = h("button", { class: "primary" }, "今月分を計算する");
    button.addEventListener("click", action(button, slot, async () => {
      title.textContent = "計算しています";
      field.classList.add("scan");
      await Promise.all([post("/api/run"), wait(1200)]);
      await render();
    }));
    frame("today", [h("div", null, h("div", { class: "eyebrow" }, monthLabel(data.month)), title), button, slot, field]);
    return;
  }
  const intro = !state.quiet && state.seenRun !== run.id;
  state.seenRun = run.id;
  const blocks = [heading(monthLabel(data.month), run.stopped ? `${run.stopped}人の確認が必要です。` : "全員の計算が終わりました。"),
    run.stopped ? h("a", { class: "primary", href: "#inbox" }, "確認へ") : h("a", { class: "primary", href: "#pay" }, "支給へ")];
  blocks.push(h("dl", { class: "stats" }, stat("計算できた人", num(run.proceeding), `/ ${run.total}人`), stat("確認が必要な人", num(run.stopped), "人"), stat("次の支給", dayLabel(run.payment_date))));
  blocks.push(h("section", { class: "block" }, dotField(data.groups, intro), legend()));
  state.prevGroups = Object.fromEntries(data.groups.map((group) => [group.name, group.waiting]));
  if (data.waiting.length) {
    blocks.push(h("section", { class: "block" }, h("h2", null, "確認すること"),
      h("div", null, data.waiting.map((row) => h("div", { class: "person" }, h("div", { class: "who-row" }, avatar(row.who), h("div", { class: "id" }, h("span", null, row.reason), h("small", null, row.who))), h("div", { class: "row" }, pips(row.count), h("span", null, `${row.count}人`)))))));
  }
  const left = run.estimate.excluded ? `（${run.estimate.excluded}人を除く）` : "";
  blocks.push(h("section", { class: "block" }, h("h2", null, "見込み"),
    h("dl", { class: "stats" }, stat(`${run.period_name}の見込み${left}`, num(run.estimate.total_pay, yen)), stat("昇給の見込み", num(run.raises.raised), "人"), stat("基本給の月額の増分", num(run.raises.monthly_increase, yen)))));
  frame("today", blocks);
}

// 確認
function rankRow(person, ranks, slot, send) {
  const select = h("select", { "aria-label": `${person.name}の考課` }, h("option", { value: "" }, "選ぶ"), ranks.map((rank) => h("option", { value: rank }, rank)));
  const button = h("button", { class: "ghost", disabled: true }, "答える");
  const row = h("div", { class: "person" }, idBlock(person), h("div", { class: "row" }, select, button));
  select.addEventListener("change", () => { button.disabled = !select.value; });
  button.addEventListener("click", action(button, slot, () => send(select.value, row)));
  return row;
}
function judgeRow(person, slot, send) {
  const buttons = person.options.map((group) => h("button", { class: "ghost" }, `${group}で数える`));
  const row = h("div", { class: "person" }, idBlock(person), h("div", { class: "row" }, buttons));
  buttons.forEach((button, index) => button.addEventListener("click", action(button, slot, () => send(person.options[index], row))));
  return row;
}
const idBlock = (person) => h("div", { class: "id" }, h("span", null, person.name), h("small", null, `${person.group || "職場が不明"}・${person.job || "職種が不明"}`), h("small", null, person.history || person.reason));

function fileCard(bundle, slot) {
  const result = h("div");
  const show = (data) => {
    if (!data.ok) {
      result.replaceChildren(h("div", { class: "errors rise", role: "alert" }, h("b", null, "取り込みませんでした"), data.errors.map((text) => h("span", null, text))));
      return;
    }
    const confirm = h("button", { class: "primary" }, "この内容で確定する");
    confirm.addEventListener("click", action(confirm, slot, async () => { await post("/api/files/confirm", { kind: data.kind, token: data.token }); toast("取り込みました"); await render(); }));
    const diff = data.diff;
    result.replaceChildren(h("div", { class: "block rise", "data-hold": true }, h("dl", { class: "stats" }, stat("変わる人", num(diff.changed), "人"), stat("増える人", num(diff.added), "人"), stat("消える人", num(diff.removed), "人")), confirm));
  };
  const check = async (file) => {
    slot.replaceChildren();
    try { show(await api(`/api/files/check?kind=${encodeURIComponent(bundle.file)}`, { method: "POST", body: await file.arrayBuffer() })); }
    catch (error) { slot.replaceChildren(problem(error.message)); }
  };
  const input = h("input", { type: "file", accept: ".csv,text/csv", "aria-label": `${bundle.file}のファイル`, onchange: (event) => { if (event.target.files.length) check(event.target.files[0]); } });
  const zone = h("label", { class: "drop" }, input, h("span", null, "ファイルをここへ置く"), h("small", null, "CSV"));
  const over = (on) => (event) => { event.preventDefault(); zone.classList.toggle("over", on); };
  zone.addEventListener("dragover", over(true));
  zone.addEventListener("dragleave", over(false));
  zone.addEventListener("drop", (event) => { over(false)(event); if (event.dataTransfer.files.length) check(event.dataTransfer.files[0]); });
  return h("div", { class: "block" }, zone, result);
}

// 一時URL。生の URL は作った時だけ画面に出す。サーバーはハッシュしか持たない
const ASKED = { rank: "考課", judge: "どの職場で数えるか" };
const lines = (text) => text.split("\n").map((line) => line.trim()).filter(Boolean);
async function makeLink(to, questions) {
  const data = await post("/api/links", { to, questions });
  state.fresh[to] = `${location.origin}/q#${data.token}`;
  delete state.drafts[`c:${to}`];
  delete state.meters[to];
  await render();
}
function urlBox(url) {
  const input = h("input", { type: "text", readonly: true, value: url, "aria-label": "一時URL", onclick: (event) => event.target.select() });
  const copy = h("button", { class: "ghost" }, "コピー");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(url); toast("コピーしました"); }
    catch { input.select(); }
  });
  return h("div", { class: "block urlbox" }, h("div", { class: "url" }, input, copy), h("small", null, "この画面を離れると再表示できません"));
}
function linkState(contact, slot) {
  const { link } = contact;
  const fresh = state.fresh[contact.id] ? urlBox(state.fresh[contact.id]) : null;
  if (!link) return fresh;
  const live = link.status === "回答待ち";
  const text = live ? `${link.done} / ${link.total} 問` : link.status === "回答済み" ? "答えがすべて届きました" : "期限が切れました";
  const revoke = h("button", { class: "ghost" }, "取り消す");
  revoke.addEventListener("click", action(revoke, slot, async () => { await post("/api/links/revoke", { id: link.id }); delete state.fresh[contact.id]; await render(); }));
  return h("div", { class: "block" }, fresh, h("div", { class: "track" }, meter(contact.id, link.total ? link.done / link.total : 0), h("span", { class: `tag${live ? "" : " quietly"}` }, live ? "回答待ち" : link.status), h("span", { class: "small" }, text), live ? revoke : null));
}
function contactCard(contact) {
  const slot = h("div");
  const count = contact.bundles.reduce((sum, bundle) => sum + bundle.count, 0);
  const live = contact.link && contact.link.status === "回答待ち";
  const box = kept(`c:${contact.id}`, { "aria-label": `${contact.name}さんに面談で聞きたいこと`, placeholder: "1行に1問" });
  const make = h("button", { class: "ghost" }, live ? "URLを作り直す" : "URLを作る");
  make.addEventListener("click", action(make, slot, () => makeLink(contact.id, lines(box.value))));
  return h("section", { class: "card" }, h("div", { class: "card-head" }, avatar(contact.name), h("div", { class: "id" }, h("h2", null, contact.name), h("small", null, contact.role)),
    count ? h("div", { class: "row end" }, pips(count), h("span", null, `${count}人`)) : null),
  linkState(contact, slot),
  count ? folded(`p:${contact.id}`, contact.bundles.map((bundle) => `${ASKED[bundle.kind]}（${bundle.count}人）`).join("・"), contact.bundles.map((bundle) => bundle.people.map((person) => h("div", { class: "person" }, idBlock(person))))) : null,
  folded(`q:${contact.id}`, "面談の質問", box), slot, make);
}
function interviewCard(others) {
  const slot = h("div");
  const who = h("select", { "aria-label": "聞く相手" }, others.map((row) => h("option", { value: row.id }, `${row.name}（${row.role}）`)));
  const box = kept("interview", { "aria-label": "面談で聞きたいこと", placeholder: "1行に1問" });
  const make = h("button", { class: "ghost" }, "URLを作る");
  make.addEventListener("click", action(make, slot, async () => { await makeLink(who.value, lines(box.value)); delete state.drafts.interview; }));
  const examples = ["今期、負担が増えた人はいますか", "来期の昇給で気になる人はいますか", "異動の予定はありますか"]
    .map((text) => h("button", { class: "chip", onclick: () => { box.value = box.value ? `${box.value}\n${text}` : text; state.drafts.interview = box.value; box.focus(); } }, text));
  return h("section", { class: "card" }, h("h2", null, "面談"), h("label", null, "相手", who), h("label", null, "質問", box), h("div", { class: "chips" }, examples), slot, make);
}
async function inboxScreen() {
  const data = await api("/api/inbox");
  const asked = data.contacts.filter((row) => row.bundles.length || (row.link && row.link.status === "回答待ち") || state.fresh[row.id]);
  const others = data.contacts.filter((row) => !asked.includes(row));
  const count = [...data.bundles, ...data.contacts.flatMap((row) => row.bundles)].reduce((sum, bundle) => sum + bundle.count, 0);
  const blocks = [heading(monthLabel(data.month), count ? `確認が必要な人が${count}人います。` : "確認はすべて終わりました。")];
  for (const bundle of data.bundles) {
    const slot = h("div");
    const rows = bundle.kind === "rank"
      ? bundle.people.map((person) => rankRow(person, data.ranks, slot, async (rank) => { await post("/api/inbox/rank", { item_id: person.item_id, rank }); await render(); }))
      : bundle.people.map((person) => h("div", { class: "person" }, idBlock(person)));
    blocks.push(h("section", { class: "card" }, h("div", { class: "card-head" }, avatar("人事"), h("div", { class: "id" }, h("h2", null, bundle.title)), h("div", { class: "row end" }, pips(bundle.count), h("span", null, `${bundle.count}人`))),
      slot, folded(`f:${bundle.file || bundle.kind}`, "対象の人", rows), bundle.kind === "file" ? fileCard(bundle, slot) : null));
  }
  blocks.push(...asked.map(contactCard));
  if (others.length) blocks.push(interviewCard(others));
  if (data.answers.length) {
    blocks.push(h("section", { class: "block" }, h("h2", null, "届いた答え"), data.answers.map((row) => h("div", { class: "answer" },
      h("div", { class: "who-row" }, avatar(row.who), h("div", { class: "id" }, h("span", null, row.who), h("small", null, jst(row.at)))),
      h("p", { class: "q" }, row.question), h("p", { class: "text" }, row.text)))));
  }
  frame("inbox", blocks);
}

// kounin に聞く
function recordTable(record) {
  return h("div", { class: "block" }, h("h3", null, `${record.name}さんの計算の記録`),
    h("div", { class: "scroll" }, h("table", null, h("tbody", null, record.trace.map((step) => h("tr", null, h("th", { scope: "row" }, step.name), h("td", null, step.value)))))));
}
function askScreen() {
  const box = h("textarea", { "aria-label": "聞きたいこと", placeholder: "賞与はどう計算するの" });
  const answers = h("div", { class: "block" });
  const slot = h("div");
  const button = h("button", { class: "primary" }, "聞く");
  const send = action(button, slot, async () => {
    const text = box.value;
    const data = await post("/api/ask", { text });
    answers.prepend(h("div", { class: "answer rise" }, h("p", { class: "q" }, text), h("p", null, data.text),
      data.steps ? h("ol", { class: "steps" }, data.steps.map((step, index) => h("li", { style: `--i:${index}` }, step))) : null,
      data.record ? recordTable(data.record) : null,
      data.sources && data.sources.length ? h("p", { class: "small" }, `出典：${data.sources.join("、")}`) : null));
    box.value = "";
  });
  button.addEventListener("click", send);
  const examples = ["賞与はどう計算するの", "昇給はどう決まるの", "異動した人はどう数えるの"]
    .map((text) => h("button", { class: "chip", onclick: () => { box.value = text; box.focus(); } }, text));
  frame("ask", [heading("kounin に聞く", "何でも聞いてください。"), box, h("div", { class: "chips" }, examples), button, slot, answers]);
}

// 変更
function split(effect) {
  const total = effect.up + effect.down + effect.same || 1;
  const part = (count, cls) => (count ? h("i", { class: cls, style: `--w:${(count / total) * 100}%` }) : null);
  return h("div", { class: "block" }, h("div", { class: "split", role: "img", "aria-label": `増える人${effect.up}人、減る人${effect.down}人、変わらない人${effect.same}人` }, part(effect.up, "up"), part(effect.down, "down"), part(effect.same, "same")),
    h("div", { class: "legend" }, h("span", null, h("i", { class: "key up" }), `増える ${effect.up}人`), h("span", null, h("i", { class: "key down" }), `減る ${effect.down}人`), h("span", null, h("i", { class: "key same" }), `変わらない ${effect.same}人`)));
}
function effectView(record, slot) {
  const effect = record.effect;
  const people = (title, items) => (items.length ? folded(`e:${title}`, title, items.map((item) =>
    h("div", { class: "person" }, h("div", { class: "id" }, h("span", null, item.name), h("small", null, item.group || "")), h("span", null, signed(item.delta))))) : null);
  const approve = h("button", { class: "primary" }, "承認する");
  approve.addEventListener("click", action(approve, slot, async () => { await post("/api/changes/approve", { id: record.id }); toast("新しい版にしました"); await render(); }));
  const reject = h("button", { class: "ghost" }, "取り下げる");
  reject.addEventListener("click", action(reject, slot, async () => { await post("/api/changes/reject", { id: record.id }); await render(); }));
  return h("section", { class: "card rise" },
    h("div", { class: "scroll" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "変えるもの"), h("th", null, "今"), h("th", null, "案"))),
      h("tbody", null, record.ops.map((op) => h("tr", null, h("td", null, op.label), h("td", null, op.before), h("td", { class: "strong" }, op.after)))))),
    h("dl", { class: "stats" }, stat("総額の差", signed(effect.total_delta)), stat("施行日", dayLabel(record.effective_from))),
    split(effect), people("増える額の大きい人", effect.largest_up), people("減る額の大きい人", effect.largest_down),
    slot, h("div", { class: "row" }, approve, reject));
}
async function changeScreen() {
  const data = await api("/api/changes");
  const open = data.changes.find((record) => record.status === "案");
  const text = h("input", { type: "text", "aria-label": "変えたいこと", placeholder: "配分率を0.55にする" });
  const day = h("input", { type: "date", value: data.effective_from, "aria-label": "施行日" });
  const slot = h("div");
  const button = h("button", { class: "primary" }, "差を見る");
  button.addEventListener("click", action(button, slot, async () => {
    const result = await post("/api/changes/propose", { text: text.value, effective_from: day.value });
    if (result.stop) { slot.replaceChildren(h("div", { class: "errors rise", role: "alert" }, h("b", null, result.stop), h("span", null, result.why))); return; }
    await render();
  }));
  const examples = ["配分率を0.55にする", "等級3のBのポイントを200にする"]
    .map((value) => h("button", { class: "chip", onclick: () => { text.value = value; text.focus(); } }, value));
  const blocks = [];
  if (open) {
    const actionSlot = h("div");
    blocks.push(heading("変更", `「${open.text}」`), effectView(open, actionSlot));
  } else {
    blocks.push(heading("変更", "何が変わりましたか。"), h("label", null, "変えたいこと", text), h("div", { class: "chips" }, examples), h("label", null, "施行日", day), button, slot);
  }
  blocks.push(h("section", { class: "block" }, h("h2", null, "版"), h("div", { class: "scroll" }, h("table", null,
    h("thead", null, h("tr", null, h("th", null, "版"), h("th", null, "施行日"), h("th", null, "内容"))),
    h("tbody", null, data.versions.map((item) => h("tr", null, h("td", null, item.version), h("td", null, dayLabel(item.effective_from)), h("td", null, item.summary))))))));
  frame("change", blocks);
}

// 支給。案は同じ物差しの棒で比べる
function planRow(plan, top, order, extra) {
  const bar = (label, value, cls) => h("div", { class: "bar-row" }, h("span", { class: "small" }, label),
    h("div", { class: "bar" }, h("i", { class: cls, style: `--w:${(value / top) * 100}%;--d:${order * 140}ms` })), h("span", { class: "bar-value" }, num(value, yen)));
  return h("section", { class: `card plan${extra.chosen ? " chosen" : ""}` }, h("div", { class: "plan-head" }, h("h2", null, `案${plan.id} ${plan.name}`),
    extra.chosen ? h("span", { class: "tag stamp" }, "決定") : order ? h("span", { class: "small" }, signed(plan.delta)) : null),
  bar("原資", plan.fund, "fund"), bar("支給計", plan.total_pay, "pay"), extra.button || null);
}
const topOf = (plans) => Math.max(...plans.flatMap((plan) => [plan.fund, plan.total_pay]));

async function payScreen() {
  const data = await api("/api/pay");
  const slot = h("div");
  const run = data.run;
  const blocks = [];
  const decided = data.decision && !data.decision.expired ? data.decision : null;
  if (!run || data.stale || run.stopped) {
    const why = !run ? "今月分をまだ計算していません。" : data.stale ? "新しいデータが届きました。" : `確認が必要な人が${run.stopped}人います。`;
    blocks.push(heading("支給", why), h("a", { class: "primary", href: !run || data.stale ? "#today" : "#inbox" }, !run || data.stale ? "計算へ" : "確認へ"));
  } else if (!data.plans) {
    const button = h("button", { class: "primary" }, "3案を作る");
    button.addEventListener("click", action(button, slot, async () => { await post("/api/pay/plans"); await render(); }));
    blocks.push(heading(`支給・${run.period_name}`, "原資の3案を作ります。"), h("dl", { class: "stats" }, stat("対象", num(run.total), "人"), stat("支給日", dayLabel(run.payment_date))), button, slot);
  } else {
    const top = topOf(data.plans);
    blocks.push(heading(`支給・${run.period_name}`, decided ? `案${decided.plan_id}「${decided.plan_name}」に決まりました。` : "理事長の決定を待っています。"));
    blocks.push(h("div", { class: "plans" }, data.plans.map((plan, order) => planRow(plan, top, order, { chosen: decided && decided.plan_id === plan.id }))));
    if (decided) {
      const button = h("button", { class: "primary" }, "給与システム用の CSV を出す");
      button.addEventListener("click", action(button, slot, async () => {
        const response = await api("/api/pay/export", { method: "POST" });
        const link = h("a", { href: URL.createObjectURL(await response.blob()), download: `賞与-${data.month}-案${decided.plan_id}.csv` });
        link.click();
        URL.revokeObjectURL(link.href);
        toast("CSV を出しました");
        await render();
      }));
      blocks.push(h("p", { class: "small" }, `${decided.by}さん（${jst(decided.at)}）`), button);
    } else if (data.chair) {
      const live = data.chair.link && data.chair.link.status === "回答待ち";
      const make = h("button", { class: live ? "ghost" : "primary" }, live ? "URLを作り直す" : "理事長のURLを作る");
      make.addEventListener("click", action(make, slot, () => makeLink(data.chair.id, [])));
      blocks.push(h("section", { class: "card" }, h("div", { class: "card-head" }, avatar(data.chair.name), h("div", { class: "id" }, h("h2", null, data.chair.name), h("small", null, data.chair.role))), linkState(data.chair, slot), make));
    }
    blocks.push(slot);
  }
  if (data.decision && data.decision.expired) {
    blocks.unshift(h("div", { class: "errors rise", role: "alert" }, h("b", null, "前の決定は失効しました"), h("span", null, `案${data.decision.plan_id}の決定の後に、入力かルールか係数が変わりました。`)));
  }
  frame("pay", blocks);
}

// 履歴
async function historyScreen() {
  const data = await api("/api/history");
  frame("history", [heading("履歴", "誰がいつ何をしたか"), h("div", { class: "scroll" }, h("table", null,
    h("thead", null, h("tr", null, ["いつ", "誰が", "何を", "結果", "版"].map((name) => h("th", null, name)))),
    h("tbody", null, data.entries.map((row) => h("tr", null, h("td", null, jst(row.at)), h("td", null, `${row.user}（${row.role}）`), h("td", null, `${row.action}：${row.target.replace(/^(\d{4}-\d{2})/, (month) => monthLabel(month))}`), h("td", null, row.result), h("td", null, row.version))))))]);
}

// ログイン
function loginScreen() {
  const { environment } = state.session;
  const slot = h("div");
  const id = h("input", { type: "text", name: "username", autocomplete: "username", required: true, "aria-label": "ログインID" });
  const password = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true, "aria-label": "パスワード" });
  const button = h("button", { class: "primary wide", type: "submit" }, "ログイン");
  const submit = action(button, slot, async () => { await post("/api/login", { login_id: id.value, password: password.value }); clearHash(); await boot(); });
  document.getElementById("app").replaceChildren(h("div", { class: "gate" },
    h("div", { class: "gate-art", "aria-hidden": "true" }, Array.from({ length: 220 }, (_, index) => h("i", { class: "dot", style: `--i:${index}` }))),
    h("form", { class: "gate-form rise", onsubmit: (event) => { event.preventDefault(); submit(); } },
      h("div", { class: "brand big" }, "kounin"), h("label", null, "ログインID", id), h("label", null, "パスワード", password), slot, button,
      h("div", { class: "gate-foot" }, h("span", null, "法令判断は参考情報です"), h("span", null, environment)))));
  id.focus();
}

// 一時URLの画面。ログインせず、トークンの見出しだけで動く。トークンは URL の # の後ろにあり、サーバーのログに残らない
async function linkApi(path, data) {
  const response = await fetch(path, {
    method: data ? "POST" : "GET", credentials: "omit",
    headers: { "X-Link-Token": location.hash.slice(1), ...(data ? { "Content-Type": "application/json" } : {}) },
    body: data ? JSON.stringify(data) : undefined,
  });
  const body = await response.json();
  if (!response.ok) throw Object.assign(new Error(body.error || "うまくいきませんでした"), { closed: response.status === 410, done: body.done });
  return body;
}
const solo = (...children) => document.getElementById("app").replaceChildren(h("div", { class: "solo" }, h("div", { class: "brand" }, "kounin"), children, h("p", { class: "small" }, "法令判断は参考情報です")));
async function leave(node) {
  if (!moving()) return;
  node.classList.add("leaving");
  await wait(260);
}

function questionCard(row) {
  if (row.answered) return null;
  const slot = h("div");
  const box = kept(row.index, { "aria-label": row.text });
  const send = h("button", { class: "primary" }, "送る");
  const card = h("section", { class: "card" }, h("h2", null, row.text), box, slot, send);
  send.addEventListener("click", action(send, slot, async () => { await linkApi("/api/link/answer", { index: row.index, text: box.value }); delete state.drafts[row.index]; await leave(card); await linkPage(); }));
  return card;
}
async function linkPage() {
  let data;
  try { data = await linkApi("/api/link"); }
  catch (error) {
    solo(h("div", { class: "closed rise" }, error.done ? h("span", { class: "ring" }, mark()) : null, h("h1", null, error.message)));
    return;
  }
  const blocks = [h("div", null, h("div", { class: "eyebrow" }, monthLabel(data.month)), h("h1", null, `${data.name}さん`)),
    h("div", { class: "track" }, meter("link", data.total ? data.done / data.total : 0), h("span", { class: "small" }, `${data.done} / ${data.total}`)),
    h("p", { class: "small" }, `${data.by}さん（人事）より ・ ${jst(data.expires_at)} まで`)];
  for (const bundle of data.bundles) {
    const slot = h("div");
    const rows = bundle.people.map((person) => (bundle.kind === "rank"
      ? rankRow(person, data.ranks, slot, async (rank, row) => { await linkApi("/api/link/rank", { item_id: person.item_id, rank }); await leave(row); await linkPage(); })
      : judgeRow(person, slot, async (group, row) => { await linkApi("/api/link/judge", { item_id: person.item_id, group }); await leave(row); await linkPage(); })));
    blocks.push(h("section", { class: "card" }, h("h2", null, bundle.title), slot, h("div", null, rows)));
  }
  blocks.push(...data.questions.map(questionCard));
  if (data.decide && data.decide.changed) blocks.push(h("div", { class: "errors", role: "alert" }, h("b", null, "案が変わりました"), h("span", null, "人事担当に新しいURLをもらってください。")));
  else if (data.decide) {
    const slot = h("div");
    const top = topOf(data.decide.plans);
    blocks.push(h("section", { class: "block" }, h("h2", null, "原資の3案から1案を決めてください"), slot, h("div", { class: "plans" }, data.decide.plans.map((plan, order) => {
      const button = h("button", { class: "ghost" }, "この案に決める");
      button.addEventListener("click", action(button, slot, async () => { await linkApi("/api/link/decide", { plan_id: plan.id }); await linkPage(); }));
      return planRow(plan, top, order, { button });
    }))));
  }
  solo(blocks);
}

const SCREENS = { today: todayScreen, inbox: inboxScreen, ask: askScreen, change: changeScreen, pay: payScreen, history: historyScreen };
const currentScreen = () => { const wanted = location.hash.slice(1); return state.session.screens.includes(wanted) ? wanted : state.session.screens[0]; };

async function render() {
  const screen = currentScreen();
  try {
    state.pulse = await api("/api/pulse");
    state.drawn = state.pulse.signature;
    await SCREENS[screen]();
  } catch (error) {
    if (!state.session.user) return;      // セッションが切れた時は boot がログイン画面を出している
    frame(screen, [problem(error.message)]);
  }
  if (!state.quiet) window.scrollTo(0, 0);
}

// 答えが届いたら、人事の画面をその場で動かす。入力の途中は待つ
const busy = () => document.querySelector("[data-hold]") || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
async function beat() {
  if (document.hidden || location.pathname === "/q" || !state.session || !state.session.user) return;
  if (["ask", "change"].includes(currentScreen()) || busy()) return;
  let pulse;
  try { pulse = await api("/api/pulse"); } catch { return; }
  if (pulse.signature === state.drawn) return;
  state.quiet = true;
  await render();
  state.quiet = false;
}

async function boot() {
  if (location.pathname === "/q") { await linkPage(); return; }
  state.session = await (await fetch("/api/session", { credentials: "same-origin" })).json();
  if (state.session.user) await render();
  else loginScreen();
}

window.addEventListener("hashchange", () => {
  if (location.pathname === "/q") linkPage();
  else if (state.session && state.session.user) { state.fresh = {}; state.prevGroups = null; render(); }
});
setInterval(beat, PULSE_MS);
boot();
