"use strict";
// Toutes les données serveur/LLM sont insérées via textContent (jamais innerHTML) -> pas de XSS.
const STEPS = ["Définir", "Explorer", "Évaluer", "Le faire", "Apprendre"];
const $ = (id) => document.getElementById(id);
let current = null; // SessionView

function el(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  Object.assign(n, props);
  for (const c of children) n.append(c);
  return n;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : data.detail?.message || res.statusText);
    err.missing = data.detail?.missing;
    throw err;
  }
  return data;
}

function addMsg(role, text) {
  const m = el("div", { className: `msg ${role}`, textContent: text });
  $("chat").append(m);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function renderStepper(step) {
  $("stepper").replaceChildren(
    ...STEPS.map((name, i) =>
      el("li", { className: i + 1 < step ? "done" : i + 1 === step ? "current" : "", textContent: `${i + 1}. ${name}` })
    )
  );
}

function card(title, ...lines) {
  return el("div", { className: "card" }, el("b", { textContent: title }), ...lines.filter(Boolean).map((t) => el("div", { textContent: t })));
}

function renderState(st) {
  const box = $("state");
  box.replaceChildren();
  if (st.problem_statement) box.append(card("Problème", st.problem_statement));
  if (st.root_cause)
    box.append(card("Cause racine", st.root_cause, st.root_cause_confirmed ? "✔ confirmée" : "… à confirmer"));
  st.solutions.forEach((s) => box.append(card(`${s.selected ? "★ " : ""}${s.title}`, s.description)));
  st.scenarios.forEach((s) =>
    box.append(card(`Scénarios — ${s.solution_title}`, `Meilleur : ${s.best_case || "—"}`, `Pire : ${s.worst_case || "—"}`, `Si échec : ${s.if_it_fails || "—"}`))
  );
  st.evaluations.forEach((e) =>
    box.append(card(`Évaluation — ${e.solution_title}`, `+ ${e.pros.join(" ; ")}`, `− ${e.cons.join(" ; ")}`, e.stress_test_summary))
  );
  if (st.chosen_solution) box.append(card("Option choisie", st.chosen_solution));
  st.action_plan.forEach((a) => box.append(card(a.action, `Resp. ${a.owner || "?"} · ${a.deadline || "?"}`, `Mesure : ${a.success_metric || "?"}`)));
  if (st.decision_summary) box.append(card("Synthèse de la décision", st.decision_summary));
}

function renderSources(sources) {
  const box = $("sources");
  box.replaceChildren();
  if (!sources?.length) return box.append(el("div", { className: "kv", textContent: "Aucune source utilisée à ce tour." }));
  for (const s of sources) {
    const cls = s.reliability_score >= 80 ? "badge" : s.reliability_score >= 50 ? "badge mid" : "badge low";
    box.append(
      el("div", { className: "card" },
        el("b", { textContent: `[${s.ref}] ${s.title}` }),
        el("span", { className: cls, textContent: `fiabilité ${s.reliability_score}` }),
        el("span", { className: "kv", textContent: ` ${s.source_type} · pertinence ${s.similarity.toFixed(2)}` }),
        el("div", { textContent: s.excerpt })
      )
    );
  }
}

function renderControls(view) {
  renderStepper(view.current_step);
  renderState(view.state);
  const g = view.guardrails;
  const locked = view.status !== "active";
  $("composer").hidden = locked;
  $("review-form").hidden = view.status !== "awaiting_review";
  $("advance").disabled = !g.can_advance;
  $("guard").className = g.can_advance ? "ok" : "";
  $("guard").textContent = locked
    ? (view.status === "reviewed" ? "Décision évaluée — merci, la leçon a enrichi la base." : "Décision validée. Reviens plus tard avec ton rapport d'évaluation.")
    : g.can_advance ? "✔ Étape complète : tu peux passer à la suivante." : "À compléter : " + g.missing.join(" · ");
}

async function openSession(id) {
  current = await api(`/api/sessions/${id}`);
  $("chat").replaceChildren();
  let lastSources = [];
  current.messages.forEach((m) => { addMsg(m.role, m.content); if (m.role === "assistant") lastSources = m.sources; });
  renderSources(lastSources);
  renderControls(current);
  await refreshList();
}

async function refreshList() {
  const list = await api("/api/sessions");
  $("session-list").replaceChildren(
    ...list.map((s) => {
      const li = el("li", { className: current?.id === s.id ? "active" : "" },
        el("span", { textContent: s.title }),
        el("small", { textContent: `Étape ${s.current_step} · ${s.status}` }));
      li.onclick = () => openSession(s.id);
      return li;
    })
  );
}

async function afterTurn(result) {
  addMsg("assistant", result.reply);
  renderSources(result.sources);
  current = await api(`/api/sessions/${current.id}`);
  renderControls(current);
  await refreshList();
}

$("new-session").onclick = async () => {
  const title = prompt("Titre de la décision ?", "Nouvelle décision");
  if (title === null) return;
  $("new-session").disabled = true;
  try {
    const r = await api("/api/sessions", { method: "POST", body: { title } });
    await openSession(r.session_id);
  } catch (e) { alert(e.message); }
  $("new-session").disabled = false;
};

$("composer").onsubmit = async (ev) => {
  ev.preventDefault();
  const text = $("input").value.trim();
  if (!text || !current) return;
  $("input").value = "";
  addMsg("user", text);
  $("send").disabled = true;
  try { await afterTurn(await api(`/api/sessions/${current.id}/messages`, { method: "POST", body: { content: text } })); }
  catch (e) { addMsg("system", `Erreur : ${e.message}`); }
  $("send").disabled = false;
};

$("advance").onclick = async () => {
  if (!current) return;
  $("advance").disabled = true;
  try {
    const r = await api(`/api/sessions/${current.id}/advance`, { method: "POST" });
    addMsg("system", `→ Étape ${r.step}`);
    await afterTurn(r);
  } catch (e) { addMsg("system", `Impossible d'avancer : ${(e.missing || [e.message]).join(" · ")}`); }
};

$("review-form").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/reviews", { method: "POST", body: { session_id: current.id, outcome: $("outcome").value, report: $("report").value } });
    $("report").value = "";
    await openSession(current.id);
  } catch (e) { addMsg("system", `Erreur : ${e.message}`); }
};

refreshList();
