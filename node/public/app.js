"use strict";

const state = { persona: "dental", meta: null, lastAction: null, view: "initiate" };

const PERSONA_ROLE = {
  dental:
    "The dental PMS (FHIR) initiates the referral. You simulate the 360X replies the medical peer sends back; the bridge converts them to FHIR so the PMS can read its inbox.",
  medical:
    "The medical EHR (360X) initiates the referral. You simulate the dental FHIR replies; the bridge emits the 360X the EHR would ingest.",
};
const INITIATE_HINT = {
  dental: "Builds the ODE Native FHIR referral (Patient, Coverage, referring + rendering PractitionerRole, coded Condition, ServiceRequest, Task) AND the degraded 360X PCC-55 Referral Note.",
  medical: "Builds a 360X PCC-55 Referral Note carrying the full dataset → the bridge reconstructs the FHIR the dentist reads (enough to diagnose, treat, and bill).",
};
const DIR_HINT = { dental: "dentist → physician", medical: "physician → dentist" };
const REFER_BY = { dental: "the dentist", medical: "the physician" };
const RENDER_TO = { dental: "the physician / specialist", medical: "the dentist" };
const INTAKE_LEDE = {
  dental: "Referring a patient out to a physician/specialist. Capture enough for the receiver to diagnose, treat, and bill — plus who is referring and who will render care.",
  medical: "Referring a patient to a dentist. Capture enough for the dentist to treat and bill — plus the referring and rendering provider details.",
};
const REPLY_INTRO = {
  dental: "These replies come FROM the physician/specialist you referred to. Each carries a little content, then Send shows the 360X payload (HL7 v2 + C-CDA) that arrives — the resulting FHIR lands in the Inbox tab.",
  medical: "These replies come FROM the dentist you referred to. Each carries a little content, then Send shows the 360X payload the EHR ingests — the resulting FHIR lands in the Inbox tab.",
};

// Minimal, mostly-prefilled inputs per reply (the responder side). Rendered inside
// each reply card; ids are `rf_<key>` and read into form.reply.<key>.
const DECLINE_REASONS = ["out-of-network", "wrong-specialty", "insufficient-info", "capacity", "patient-declined"];
const DISPOSITIONS = ["cleared", "not-cleared", "partial"];
const APPT_TYPES = ["in-person", "tele"];
const NOSHOW_REASONS = ["no-show", "cancelled-late", "transport"];
const REPLY_FIELDS = {
  "pcc56-accept": [
    { key: "acceptNote", label: "Acknowledgment note", type: "text", value: "Referral received and accepted; patient will be seen." },
    { key: "expectedBy", label: "Expected to be seen by", type: "date" },
  ],
  "pcc56-decline": [
    { key: "declineReason", label: "Reason", type: "select", options: DECLINE_REASONS },
    { key: "declineComment", label: "Comment", type: "text", value: "Unable to accept this referral." },
  ],
  "pcc59-interim": [
    { key: "interimNote", label: "Interim finding / note", type: "text", value: "Evaluation underway; findings pending." },
  ],
  "pcc57-outcome": [
    { key: "disposition", label: "Disposition", type: "select", options: DISPOSITIONS },
    { key: "outcomeNote", label: "Outcome note", type: "text", value: "Active disease remediated; cleared for treatment." },
  ],
  "pcc60-appointment": [
    { key: "apptStart", label: "Appointment date/time", type: "datetime-local" },
    { key: "apptType", label: "Type", type: "select", options: APPT_TYPES },
    { key: "location", label: "Location", type: "text", value: "Main clinic, Suite 200" },
  ],
  "pcc61-noshow": [
    { key: "noshowReason", label: "Reason", type: "select", options: NOSHOW_REASONS },
    { key: "reschedule", label: "Reschedule note", type: "text", value: "Attempting to reschedule; will advise." },
  ],
};
const REPLY_FIELD_KEYS = Array.from(new Set([].concat.apply([], Object.values(REPLY_FIELDS).map((a) => a.map((f) => f.key)))));

// Persona-native wording for the shared intake fields. Same data, two vocabularies.
const TERMS = {
  dental: {
    sec6: "6 · Treatment plan & conditions",
    service: "Requested treatment",
    conditions: "Conditions / clinical findings",
    conditionsHint: "why you're referring — coded so the physician can diagnose & bill",
  },
  medical: {
    sec6: "6 · Requested service & diagnoses",
    service: "Requested service",
    conditions: "Diagnoses",
    conditionsHint: "medical diagnoses the dentist bills medical insurance with",
  },
};

// The friendly crosswalk — dental term <-> medical term <-> FHIR + code systems.
const TERM_ROWS = [
  { dental: "Condition / clinical finding", medical: "Diagnosis", fhir: "Condition", codes: "ICD-10-CM / SNOMED CT" },
  { dental: "Treatment plan / requested treatment", medical: "Requested service / procedure", fhir: "ServiceRequest.code", codes: "CPT / HCPCS / LOINC / SNOMED CT" },
  { dental: "Current medications", medical: "Medication list", fhir: "MedicationRequest", codes: "RxNorm" },
  { dental: "Dental plan / insurance", medical: "Health plan / coverage", fhir: "Coverage", codes: "—" },
  { dental: "Referring dentist", medical: "Referring provider", fhir: "ServiceRequest.requester", codes: "NPI / NUCC" },
  { dental: "Rendering physician / specialist", medical: "Rendering provider", fhir: "ServiceRequest.performer", codes: "NPI / NUCC" },
  { dental: "Reason for referral", medical: "Reason for referral", fhir: "ServiceRequest.reasonCode", codes: "—" },
  { dental: "Priority", medical: "Priority", fhir: "ServiceRequest.priority", codes: "—" },
  { dental: "Clinical narrative", medical: "Supporting clinical info", fhir: "ServiceRequest.note", codes: "—" },
];

function $(id) { return document.getElementById(id); }
function val(id) { const el = $(id); return el ? el.value.trim() : ""; }
function setText(id, text) { const el = $(id); if (el) el.textContent = text; }
function referralId() { return val("referralId") || "REF-1001"; }

function readForm() {
  return {
    priority: val("priority"),
    reason: val("reason"),
    supporting: val("supporting"),
    patient: {
      given: val("p_given"), family: val("p_family"), mrn: val("p_mrn"),
      birthDate: val("p_birthDate"), gender: val("p_gender"), phone: val("p_phone"),
      addressLine: val("p_line"), city: val("p_city"), state: val("p_state"),
      postalCode: val("p_zip"),
    },
    coverage: {
      payer: val("c_payer"), memberId: val("c_member"), group: val("c_group"),
      plan: val("c_plan"), relationship: val("c_rel"),
    },
    referringProvider: {
      name: val("rp_name"), npi: val("rp_npi"), specialty: val("rp_spec"),
      organization: val("rp_org"), phone: val("rp_phone"),
    },
    renderingProvider: {
      name: val("nd_name"), npi: val("nd_npi"), specialty: val("nd_spec"),
      organization: val("nd_org"),
    },
    service: { display: val("s_display"), code: val("s_code"), system: val("s_system") },
    diagnoses: readDx(),
    medications: readMeds(),
    reply: readReply(),
  };
}

// Per-reply content (minimal, mostly prefilled). Keys map into form.reply.* which
// the Node payload builders consume. Inputs are rendered dynamically per card.
function readReply() {
  const out = {};
  REPLY_FIELD_KEYS.forEach((k) => { out[k] = val("rf_" + k); });
  return out;
}

// Prefill the intake form from a persona's referral-grade defaults.
function fillForm(d) {
  if (!d) return;
  const set = (id, v) => { const el = $(id); if (el != null && v != null) el.value = v; };
  set("priority", d.priority);
  set("reason", d.reason_text);
  set("supporting", d.supporting_info);
  const p = d.patient || {}, a = p.address || {};
  set("p_given", p.given); set("p_family", p.family); set("p_mrn", p.mrn);
  set("p_birthDate", p.birthDate); set("p_gender", p.gender); set("p_phone", p.phone);
  set("p_line", a.line); set("p_city", a.city); set("p_state", a.state); set("p_zip", a.postalCode);
  const c = d.coverage || {};
  set("c_payer", c.payer); set("c_member", c.member_id); set("c_group", c.group);
  set("c_plan", c.plan); set("c_rel", c.relationship);
  const rp = d.referring_provider || {};
  set("rp_name", rp.name); set("rp_npi", rp.npi); set("rp_spec", rp.specialty);
  set("rp_org", rp.organization); set("rp_phone", rp.phone);
  const nd = d.rendering_provider || {};
  set("nd_name", nd.name); set("nd_npi", nd.npi); set("nd_spec", nd.specialty);
  set("nd_org", nd.organization);
  const s = d.service || {};
  set("s_display", s.display); set("s_code", s.code); set("s_system", s.system);
  renderDxRows(d.diagnoses);
  renderMedRows(d.medications);
}
function encodeForm(form) {
  try { return btoa(unescape(encodeURIComponent(JSON.stringify(form)))); }
  catch (e) { return ""; }
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body != null) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  return r.json();
}

async function init() {
  state.meta = await api("GET", "/api/meta");
  wirePersona();
  wireWorkflow();
  wireTerminology();
  wireDx();
  wireMeds();
  wireTabs();
  wireInspector();
  $("initiateBtn").addEventListener("click", () => run("initiate"));
  $("cancelBtn").addEventListener("click", () => run("cancel"));
  $("refreshEpisodes").addEventListener("click", loadEpisodes);
  $("refreshInbox").addEventListener("click", loadInbox);
  $("referralId").addEventListener("change", () => { loadInbox(); });
  renderPersona();
  renderCrosswalk();
  loadEpisodes();
  checkHealth();
  setInterval(checkHealth, 8000);
}

function wirePersona() {
  document.querySelectorAll("#personaToggle button").forEach((b) => {
    b.addEventListener("click", () => {
      state.persona = b.dataset.persona;
      document.querySelectorAll("#personaToggle button").forEach((x) => x.classList.toggle("active", x === b));
      renderPersona();
    });
  });
}
function wireWorkflow() {
  document.querySelectorAll("#workflow button").forEach((b) => {
    b.addEventListener("click", () => setView(b.dataset.view));
  });
  setView("initiate");
}
function wireTerminology() {
  const toggle = $("termToggle");
  toggle.addEventListener("click", () => {
    const body = $("termBody");
    const open = body.hidden;
    body.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    $("termGuide").classList.toggle("open", open);
  });
}
function renderTerminologyGuide(persona) {
  const dCls = persona === "dental" ? " active-col" : "";
  const mCls = persona === "medical" ? " active-col" : "";
  const head =
    `<tr><th class="tg-dental${dCls}">Dental (PMS)</th>` +
    `<th class="tg-medical${mCls}">Medical (EHR)</th>` +
    `<th>FHIR</th><th>Code systems</th></tr>`;
  const rows = TERM_ROWS.map((r) =>
    `<tr><td class="tg-dental${dCls}">${r.dental}</td>` +
    `<td class="tg-medical${mCls}">${r.medical}</td>` +
    `<td><code>${r.fhir}</code></td><td>${r.codes}</td></tr>`).join("");
  $("termTable").innerHTML = head + rows;
}
function wireDx() {
  $("dxAdd").addEventListener("click", () => addDxRow());
}
function addDxRow(v) {
  v = v || {};
  const rows = $("dxRows");
  const row = document.createElement("div");
  row.className = "dx-row";
  row.innerHTML =
    '<input class="dx-display" type="text" placeholder="Condition / diagnosis" />' +
    '<input class="dx-code" type="text" placeholder="Code" />' +
    '<select class="dx-system"><option value="icd10">ICD-10-CM</option><option value="snomed">SNOMED CT</option></select>' +
    '<button type="button" class="dx-del" title="Remove" aria-label="Remove">&times;</button>';
  rows.appendChild(row);
  row.querySelector(".dx-display").value = v.display || "";
  row.querySelector(".dx-code").value = v.code || "";
  if (v.system) row.querySelector(".dx-system").value = v.system;
  row.querySelector(".dx-del").addEventListener("click", () => {
    row.remove();
    if (!rows.children.length) addDxRow();
  });
}
function renderDxRows(list) {
  const rows = $("dxRows");
  rows.innerHTML = "";
  (list && list.length ? list : [{}]).forEach((d) => addDxRow(d));
}
function readDx() {
  const out = [];
  document.querySelectorAll("#dxRows .dx-row").forEach((row) => {
    const display = row.querySelector(".dx-display").value.trim();
    const code = row.querySelector(".dx-code").value.trim();
    const system = row.querySelector(".dx-system").value;
    if (display || code) out.push({ display, code, system });
  });
  return out;
}
function wireMeds() {
  $("medAdd").addEventListener("click", () => addMedRow());
}
function addMedRow(v) {
  v = v || {};
  const rows = $("medRows");
  const row = document.createElement("div");
  row.className = "dx-row med-row";
  row.innerHTML =
    '<input class="med-display" type="text" placeholder="Medication (name, strength, form)" />' +
    '<input class="med-code" type="text" placeholder="RxNorm" />' +
    '<select class="med-system"><option value="rxnorm">RxNorm</option><option value="snomed">SNOMED CT</option></select>' +
    '<button type="button" class="dx-del" title="Remove" aria-label="Remove">&times;</button>';
  rows.appendChild(row);
  row.querySelector(".med-display").value = v.display || "";
  row.querySelector(".med-code").value = v.code || "";
  if (v.system) row.querySelector(".med-system").value = v.system;
  row.querySelector(".dx-del").addEventListener("click", () => {
    row.remove();
    if (!rows.children.length) addMedRow();
  });
}
function renderMedRows(list) {
  const rows = $("medRows");
  rows.innerHTML = "";
  (list && list.length ? list : [{}]).forEach((m) => addMedRow(m));
}
function readMeds() {
  const out = [];
  document.querySelectorAll("#medRows .med-row").forEach((row) => {
    const display = row.querySelector(".med-display").value.trim();
    const code = row.querySelector(".med-code").value.trim();
    const system = row.querySelector(".med-system").value;
    if (display || code) out.push({ display, code, system });
  });
  return out;
}
function setView(view) {
  state.view = view;
  document.querySelectorAll("#workflow button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("intakeForm").hidden = view !== "initiate";
  $("replyCard").hidden = view !== "replies";
  if (view === "replies") $("replyRef").textContent = referralId();
}

function wireTabs() {
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.addEventListener("click", () => {
      $("inspector").classList.remove("collapsed");
      document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
      ["result", "inbox", "crosswalk"].forEach((t) => { $("tab-" + t).hidden = t !== b.dataset.tab; });
      if (b.dataset.tab === "inbox") loadInbox();
    });
  });
}
function wireInspector() {
  const toggle = () => $("inspector").classList.toggle("collapsed");
  $("collapseBtn").addEventListener("click", toggle);
  $("inspectorBar").addEventListener("click", (e) => {
    if (e.target.closest(".tabs") || e.target.closest(".collapse")) return;
    toggle();
  });
}

function renderPersona() {
  const p = state.persona;
  $("personaTitle").textContent = p === "dental" ? "Dental PMS" : "Medical EHR";
  $("personaRole").textContent = PERSONA_ROLE[p];
  $("initiateHint").textContent = INITIATE_HINT[p];
  $("replyIntro").textContent = REPLY_INTRO[p];
  $("inboxWho").textContent = p === "dental" ? "PMS" : "EHR";
  $("intakeLede").textContent = INTAKE_LEDE[p];
  $("dirHint").textContent = DIR_HINT[p];
  $("referBy").textContent = REFER_BY[p];
  $("renderTo").textContent = RENDER_TO[p];
  const t = TERMS[p];
  setText("sec6Title", t.sec6);
  setText("s_display_label", t.service);
  setText("dxSectionLabel", t.conditions);
  setText("dxSectionHint", t.conditionsHint);
  renderTerminologyGuide(p);
  if (state.meta && state.meta.defaults) fillForm(state.meta.defaults[p]);
  document.querySelector(".persona-banner").style.borderLeftColor = p === "dental" ? "#0a6b6b" : "#1a73e8";

  const desc = state.meta.descriptions[p];
  const list = $("replyList");
  list.innerHTML = "";
  $("replyOutput").hidden = true;
  state.meta.replies.forEach((r) => {
    const card = document.createElement("div");
    card.className = "reply-card";
    const fields = REPLY_FIELDS[r.id] || [];
    card.innerHTML =
      `<div class="rc-head"><span class="rc-label">${r.label}</span><span class="tag">${r.tx}</span></div>` +
      `<div class="rc-desc">${desc[r.id] || ""}</div>` +
      `<div class="rc-inputs">${fields.map(replyFieldHtml).join("")}</div>`;
    const send = document.createElement("button");
    send.type = "button";
    send.className = "btn primary small rc-send";
    send.textContent = "Send " + r.tx;
    send.addEventListener("click", () => run(r.id));
    card.appendChild(send);
    list.appendChild(card);
  });
}

function replyFieldHtml(f) {
  const id = "rf_" + f.key;
  if (f.type === "select") {
    const opts = (f.options || []).map((o) => `<option value="${o}">${o}</option>`).join("");
    return `<div class="field"><label for="${id}">${f.label}</label><select id="${id}">${opts}</select></div>`;
  }
  const v = f.value != null ? ` value="${escapeAttr(f.value)}"` : "";
  return `<div class="field"><label for="${id}">${f.label}</label><input id="${id}" type="${f.type}"${v} /></div>`;
}
function escapeAttr(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function run(action) {
  const ref = referralId();
  const form = readForm();
  const result = await api("POST", `/api/run/${state.persona}/${action}`, { referralId: ref, form });
  state.lastAction = { persona: state.persona, action, referralId: ref, kind: result.kind, form };
  renderResult(result);
  renderExport();
  loadEpisodes();
  loadInbox();
  // After sending the referral, surface the reply/workflow actions automatically.
  if (action === "initiate" && result.ok) setView("replies");
  // Responder replies: show the friendly summary + the prominent 360X payload.
  if (REPLY_FIELDS[action]) renderReplyOutput(action, form, result);
}

// The 360X envelope (v2 + documents) for a reply: the request we posted inbound, or
// the envelope the bridge emitted outbound (response.packaged).
function envelopeOf(result) {
  if (result.kind === "360x-in") return result.request || null;
  return (result.response && result.response.packaged) || null;
}

function renderReplyOutput(action, form, result) {
  const out = $("replyOutput");
  out.hidden = false;
  const label = actionLabel(action);
  if (!result.ok) {
    out.innerHTML = `<div class="ro-head"><span class="ro-title">${label}</span><span class="pill rejected">error</span></div>` +
      `<div class="ro-summary">${escapeHtml((result.error) || "The bridge rejected this reply.")}</div>`;
    return;
  }
  const env = envelopeOf(result);
  const doc = env && env.documents && env.documents[0];
  const summary = replySummary(action, form);
  let html =
    `<div class="ro-head"><span class="ro-title">Received: ${label}</span><span class="tag">${action.split("-")[0].toUpperCase()}</span></div>` +
    `<div class="ro-summary">${escapeHtml(summary)}</div>`;

  if (env) {
    html += `<div class="ro-sub">360X payload ${doc ? "· includes a C-CDA document" : "· HL7 v2 only"}</div>`;
    if (doc) {
      html += `<div class="ccda" id="ccdaView"></div>` +
        `<div class="ro-actions">` +
        `<button type="button" class="btn ghost small" id="ccdaRawBtn">View raw C-CDA</button>` +
        `<a class="btn ghost small" id="ccdaDl" download="${action}-${referralId()}.xml">Download C-CDA</a>` +
        `</div>` +
        `<pre class="code ccda-raw" id="ccdaRaw" hidden></pre>`;
    }
    html += `<details class="ro-v2"><summary>HL7 v2 message</summary><pre class="code">${escapeHtml(env.hl7v2 || "(none)")}</pre></details>`;
  } else {
    html += `<div class="ro-sub muted">No 360X envelope on this result — see the inspector below.</div>`;
  }
  out.innerHTML = html;

  if (env && doc) {
    renderCcda($("ccdaView"), doc.content);
    const raw = $("ccdaRaw");
    $("ccdaRawBtn").addEventListener("click", () => {
      raw.hidden = !raw.hidden;
      raw.textContent = raw.hidden ? "" : doc.content;
    });
    $("ccdaDl").href = "data:application/xml;charset=utf-8," + encodeURIComponent(doc.content);
  }
  out.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// Plain-language, business-user-friendly summary of what the reply carried.
function replySummary(action, form) {
  const r = (form && form.reply) || {};
  const who = (state.persona === "dental" ? val("nd_name") : val("nd_name")) || "the provider";
  switch (action) {
    case "pcc56-accept":
      return `${who} accepted this referral.` + (r.expectedBy ? ` Patient to be seen by ${r.expectedBy}.` : "") + (r.acceptNote ? ` Note: ${r.acceptNote}` : "");
    case "pcc56-decline":
      return `${who} declined this referral — reason: ${r.declineReason || "insufficient-info"}.` + (r.declineComment ? ` ${r.declineComment}` : "");
    case "pcc59-interim":
      return `Interim update: ${r.interimNote || "evaluation underway."}`;
    case "pcc57-outcome":
      return `Outcome received — disposition: ${r.disposition || "cleared"}.` + (r.outcomeNote ? ` ${r.outcomeNote}` : "");
    case "pcc60-appointment":
      return `Appointment booked${r.apptStart ? " for " + r.apptStart.replace("T", " ") : ""}${r.apptType ? " (" + r.apptType + ")" : ""}${r.location ? " at " + r.location : ""}.`;
    case "pcc61-noshow":
      return `Patient no-show — reason: ${r.noshowReason || "no-show"}.` + (r.reschedule ? ` ${r.reschedule}` : "");
    default:
      return label(action);
  }
}

// Render a C-CDA into readable section headings + narrative (business-friendly).
function renderCcda(container, xml) {
  container.innerHTML = "";
  let dom;
  try { dom = new DOMParser().parseFromString(xml, "application/xml"); }
  catch (e) { container.textContent = "(could not parse C-CDA)"; return; }
  if (dom.querySelector("parsererror")) { container.textContent = "(could not parse C-CDA)"; return; }
  const titleEl = dom.getElementsByTagName("title")[0];
  const docTitle = titleEl ? titleEl.textContent : "Clinical Document";
  const head = document.createElement("div");
  head.className = "ccda-title";
  head.textContent = docTitle;
  container.appendChild(head);

  const sections = dom.getElementsByTagName("section");
  for (let i = 0; i < sections.length; i++) {
    const sec = sections[i];
    const st = sec.getElementsByTagName("title")[0];
    const codeEl = sec.getElementsByTagName("code")[0];
    const heading = (st && st.textContent) || (codeEl && codeEl.getAttribute("displayName")) || "Section";
    const block = document.createElement("div");
    block.className = "ccda-sec";
    const h = document.createElement("div");
    h.className = "ccda-sec-h";
    h.textContent = heading;
    block.appendChild(h);
    const items = sec.getElementsByTagName("item");
    if (items.length) {
      const ul = document.createElement("ul");
      for (let j = 0; j < items.length; j++) {
        const li = document.createElement("li");
        li.textContent = items[j].textContent.trim();
        ul.appendChild(li);
      }
      block.appendChild(ul);
    } else {
      const textEl = sec.getElementsByTagName("text")[0];
      const p = document.createElement("p");
      p.textContent = textEl ? textEl.textContent.trim() : "";
      block.appendChild(p);
    }
    container.appendChild(block);
  }
}
function label(a) { return a; }

function renderResult(result) {
  $("inspector").classList.remove("collapsed");
  const line = $("resultLine");
  line.textContent = (result.ok ? "OK · " : "ERROR · ") + actionLabel(result.action) + (result.endpoint ? " → " + result.endpoint : "");
  line.style.color = result.ok ? "" : "var(--err)";
  $("reqEndpoint").textContent = result.endpoint ? `→ ${result.endpoint}` : "";
  $("reqJson").textContent = pretty(result.request != null ? result.request : { error: result.error });
  $("respJson").textContent = pretty(result.response != null ? result.response : result);
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === "result"));
  ["result", "inbox", "crosswalk"].forEach((t) => { $("tab-" + t).hidden = t !== "result"; });
}

function actionLabel(action) {
  if (action === "initiate") return "Initiate referral";
  const r = state.meta.replies.find((x) => x.id === action);
  return r ? r.label : action;
}

function renderExport() {
  const a = state.lastAction;
  const bar = $("exportBar");
  if (!a) { bar.hidden = true; return; }
  bar.hidden = false;
  const fq = encodeForm(a.form);
  const q = (part) => `/api/export?persona=${encodeURIComponent(a.persona)}&action=${encodeURIComponent(a.action)}&referralId=${encodeURIComponent(a.referralId)}&part=${part}&form=${encodeURIComponent(fq)}`;
  const is360x = a.kind === "360x-in";
  setChip("exp-all", q("all"), true);
  setChip("exp-envelope", q("envelope"), is360x);
  setChip("exp-hl7", q("hl7"), is360x);
  setChip("exp-cda", q("cda"), is360x);
  setChip("exp-fhir", q("fhir"), !is360x);
}
function setChip(id, href, enabled) { const el = $(id); el.href = href; el.classList.toggle("disabled", !enabled); }

async function loadEpisodes() {
  const r = await api("GET", "/api/episodes");
  const box = $("episodes");
  const eps = (r && r.body && r.body.episodes) || [];
  if (!eps.length) { box.innerHTML = '<p class="muted">No episodes yet.</p>'; return; }
  box.innerHTML = "";
  eps.forEach((e) => {
    const el = document.createElement("div");
    el.className = "episode";
    const bs = e.business_status ? ` · ${e.business_status}` : "";
    el.innerHTML =
      `<div class="er-id">${e.referral_id}<span class="pill ${e.status}">${e.status}</span></div>` +
      `<div class="er-meta">${(e.initiated_by || "?")}-initiated${bs}</div>`;
    el.addEventListener("click", () => { $("referralId").value = e.referral_id; loadInbox(); });
    box.appendChild(el);
  });
}

async function loadInbox() {
  const ref = referralId();
  const r = await api("GET", `/api/inbox/${encodeURIComponent(ref)}`);
  const body = (r && r.body) || {};
  // Status rail
  $("statusBig").textContent = body.status || "—";
  $("statusBusiness").textContent = body.business_status ? "businessStatus: " + body.business_status : "";
  $("statusSource").textContent = body.source ? "read source: " + body.source : "";
  $("statusTag").textContent = body.status ? `${ref} · ${body.status}` : ref;

  const meta = [];
  if (body.source) meta.push(`source: ${body.source}`);
  if (body.status) meta.push(`Task: ${body.status}`);
  $("inboxMeta").textContent = meta.length ? "· " + meta.join(" · ") : "";

  const list = $("inboxList");
  const resources = body.resources || [];
  if (!resources.length) { list.innerHTML = '<p class="muted">No FHIR yet for this referral. Run an action, then refresh.</p>'; return; }
  list.innerHTML = "";
  resources.forEach((res) => {
    const el = document.createElement("div");
    el.className = "res";
    el.innerHTML =
      `<div class="res-head"><span class="rt-type">${res.resourceType}</span><span class="muted">${resourceSummary(res)}</span></div>` +
      `<pre class="code">${escapeHtml(pretty(res))}</pre>`;
    list.appendChild(el);
  });
}

function resourceSummary(res) {
  switch (res.resourceType) {
    case "Task": { const bs = res.businessStatus && (res.businessStatus.text || ""); return `${res.status || ""}${bs ? " · " + bs : ""}`; }
    case "Appointment": return res.status + (res.start ? " · " + res.start : "");
    case "ServiceRequest": {
      const c = (res.code && (res.code.text || firstDisplay(res.code))) || "referral";
      return `${res.priority || "routine"} · ${c}`;
    }
    case "Coverage": {
      const payer = (res.payor && res.payor[0] && (res.payor[0].display || res.payor[0].reference)) || "";
      return `${res.subscriberId ? "member " + res.subscriberId : ""}${payer ? " · " + payer : ""}`.trim();
    }
    case "Condition": return (res.code && (res.code.text || firstDisplay(res.code))) || "";
    case "Practitioner": return (res.name && res.name[0] && (res.name[0].text || [(res.name[0].given || []).join(" "), res.name[0].family].filter(Boolean).join(" "))) || "";
    case "PractitionerRole": return (res.specialty && res.specialty[0] && res.specialty[0].text) || "provider role";
    case "Organization": return res.name || "";
    case "Patient": return (res.name && res.name[0] && [(res.name[0].given || []).join(" "), res.name[0].family].filter(Boolean).join(" ")) || "";
  }
  if (res.code && res.code.text) return res.code.text;
  if (res.summary) return res.summary;
  if (res.description) return res.description;
  return "";
}
function firstDisplay(cc) { const c = (cc.coding || [])[0]; return c ? (c.display || c.code || "") : ""; }

function renderCrosswalk() {
  const rows = [
    ["PCC-55", "OMG^O19", "ServiceRequest + Task(requested)", "referral-sent"],
    ["PCC-56 accept", "OSU^O51 (IP)", "Task → accepted", "accepted"],
    ["PCC-56 decline", "OSU^O51 (CA)", "Task → rejected, Request revoked", "declined"],
    ["PCC-59", "OMG^O19 + doc", "Task in-progress + interim output", "interim-results"],
    ["PCC-57", "OMG^O19 + doc", "Task completed, Request completed", "outcome-final"],
    ["PCC-58", "OSU^O51 (CA)", "Task + Request revoked", "cancelled"],
    ["PCC-60", "SIU^S12", "Appointment (booked)", "appointment-booked"],
    ["PCC-61", "SIU^S26", "Communication (no-show)", "appointment-noshow"],
  ];
  $("xtable").innerHTML =
    "<tr><th>360X</th><th>HL7 v2</th><th>COW / FHIR</th><th>businessStatus</th></tr>" +
    rows.map((r) => `<tr><td><strong>${r[0]}</strong></td><td>${r[1]}</td><td>${r[2]}</td><td><code>${r[3]}</code></td></tr>`).join("");
}

async function checkHealth() {
  try {
    const r = await api("GET", "/api/health");
    const ok = r && r.ok;
    $("health").className = "health " + (ok ? "ok" : "bad");
    $("healthText").textContent = ok ? "bridge online" : "bridge offline";
  } catch (e) {
    $("health").className = "health bad";
    $("healthText").textContent = "bridge offline";
  }
}

function pretty(obj) { return typeof obj === "string" ? obj : JSON.stringify(obj, null, 2); }
function escapeHtml(s) { return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

init();
