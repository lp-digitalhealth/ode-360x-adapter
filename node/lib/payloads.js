"use strict";
/*
 * Shared payload builders for BOTH harness personas (dental + medical).
 *
 * This is the single source of truth the Node layer uses to construct every 360X
 * envelope and every FHIR body it sends to the bridge — and the same builders back
 * the payload EXPORT feature, so what you export is exactly what gets pushed live.
 *
 * The Node layer never re-implements the mapping logic; it only builds the wire
 * payloads each transaction needs (mirroring python/ode_adapter/hl7v2.py field
 * bindings) and forwards them to the Python bridge:
 *
 *   dental persona:   PMS is FHIR, initiates; simulates the medical 360X replies.
 *   medical persona:  EHR is 360X, initiates; simulates the dental FHIR replies.
 *
 * See spec/mapping/360x-cow-crosswalk.md — this file implements the crosswalk's
 * transaction set on the harness side.
 */

const REFERRAL_SYSTEM = "urn:ohia:referral-id";
const DENTAL_ADDR = "intake@dentalgroup.direct.example.org";
const MEDICAL_ADDR = "referrals@oncology.direct.example.org";

const DEMO_PATIENT = {
  identifier: [{ system: "2.16.840.1.113883.19.5", value: "MRN-558211" }],
  name: [{ given: ["Wilma"], family: "Stonewright" }],
  gender: "female",
  birthDate: "1975-03-12",
};

// Dental-side results (used to demonstrate the loss profile on the 360X bridge).
const DENTAL_RESULTS = [
  {
    resourceType: "ClinicalImpression",
    summary: "Active dental disease remediated; patient cleared for radiation.",
  },
  {
    resourceType: "Procedure",
    code: {
      coding: [{ system: "http://www.ada.org/cdt", code: "D7140", display: "Extraction, erupted tooth" }],
      text: "Extraction, erupted tooth",
    },
    bodySite: [{ text: "30 (Universal)" }],
  },
  {
    resourceType: "Observation",
    _dental_perio: true,
    code: { text: "Periodontal pocket depth, tooth 30 distal" },
    valueQuantity: { value: 5, unit: "mm" },
  },
  {
    resourceType: "CarePlan",
    description: "Routine recall in 3 months; maintain oral hygiene during therapy.",
  },
];

const INTERIM_NOTE = [
  {
    resourceType: "ClinicalImpression",
    summary: "Extraction completed; healing as expected. Clearance pending 2-week review.",
  },
];

// Code-system OIDs (mirror config.OID_TO_SYSTEM) so the C-CDA we build is parsed
// back to the right FHIR system by the bridge.
const OID = {
  icd10: "2.16.840.1.113883.6.90",
  snomed: "2.16.840.1.113883.6.96",
  cpt: "2.16.840.1.113883.6.12",
  hcpcs: "2.16.840.1.113883.6.285",
  loinc: "2.16.840.1.113883.6.1",
  rxnorm: "2.16.840.1.113883.6.88",
  cdt: "2.16.840.1.113883.6.13",
  nucc: "2.16.840.1.113883.6.101",
};
const MRN_SYSTEM = "http://hospital.example.org/mrn";

// --------------------------------------------------------------------------- //
// Referral-grade defaults per direction. A real referral must carry enough for
// the receiver to diagnose, treat, AND bill — plus the referring + rendering
// provider identities. These prefill the intake form and back the fallbacks.
// --------------------------------------------------------------------------- //
// Physician -> dentist: dental clearance before head & neck radiotherapy.
const DEFAULT_MED_TO_DENTAL = {
  priority: "urgent",
  patient: { given: "Wilma", family: "Stonewright", mrn: "MRN-558211",
    birthDate: "1975-03-12", gender: "female", phone: "555-0142",
    address: { line: "88 Birch Ln", city: "Aurora", state: "IL", postalCode: "60505" } },
  coverage: { payer: "Aetna PPO", member_id: "W123456789", group: "GRP-4415",
    plan: "PPO", relationship: "self" },
  referring_provider: { name: "Dr. Evan Cho", npi: "1093817465",
    specialty: "Radiation Oncology", organization: "Metro Cancer Center", phone: "555-0100" },
  rendering_provider: { name: "Dr. Nadia Patel", npi: "1841299305",
    specialty: "General Dentistry", organization: "Riverside Dental Group" },
  service: { code: "99242", system: "cpt", display: "Office/outpatient consultation — pre-radiation dental clearance evaluation" },
  diagnoses: [{ code: "C01", system: "icd10", display: "Malignant neoplasm of base of tongue" }],
  medications: [
    { code: "1116635", system: "rxnorm", display: "Ondansetron 8 mg oral tablet" },
    { code: "197361", system: "rxnorm", display: "Amoxicillin 500 mg oral capsule" },
  ],
  reason_text: "Pre-radiation dental clearance. Evaluate and remediate active dental disease and extract non-restorable teeth prior to head & neck radiotherapy.",
  supporting_info: "Planned IMRT to the oropharynx starting in ~3 weeks; no dental evaluation on file. ECOG 1. Requesting clearance and treatment promptly to avoid delaying RT.",
};

// Dentist -> physician: suspicious oral lesion referred to ENT for biopsy.
const DEFAULT_DENTAL_TO_MED = {
  priority: "urgent",
  patient: { given: "Raymond", family: "Feld", mrn: "MRN-771903",
    birthDate: "1961-08-24", gender: "male", phone: "555-0197",
    address: { line: "12 Kestrel Ct", city: "Naperville", state: "IL", postalCode: "60540" } },
  coverage: { payer: "BlueCross BlueShield PPO", member_id: "BC55120097", group: "GRP-7781",
    plan: "PPO", relationship: "self" },
  referring_provider: { name: "Dr. Nadia Patel", npi: "1841299305",
    specialty: "General Dentistry", organization: "Riverside Dental Group", phone: "555-0170" },
  rendering_provider: { name: "Dr. Priya Anand", npi: "1558299034",
    specialty: "Otolaryngology (ENT)", organization: "Metro ENT & Head/Neck Surgery" },
  service: { code: "99244", system: "cpt", display: "Office consultation — evaluation of suspicious oral lesion" },
  diagnoses: [
    { code: "D37.09", system: "icd10", display: "Neoplasm of uncertain behavior of oral cavity" },
    { code: "K13.79", system: "icd10", display: "Other lesions of oral mucosa" },
  ],
  medications: [
    { code: "855332", system: "rxnorm", display: "Warfarin sodium 5 mg oral tablet" },
    { code: "617314", system: "rxnorm", display: "Atorvastatin 20 mg oral tablet" },
  ],
  reason_text: "Referral to ENT for evaluation and biopsy of a persistent, non-healing ulcerated lesion of the left lateral tongue; rule out malignancy.",
  supporting_info: "3-week history of a 1.5 cm indurated ulcer, left lateral tongue, no improvement after removing local irritants. 30 pack-year tobacco history. Clinical photograph attached.",
};

function defaultsFor(persona) {
  return persona === "dental" ? DEFAULT_DENTAL_TO_MED : DEFAULT_MED_TO_DENTAL;
}

// --------------------------------------------------------------------------- //
// Form → payload helpers. The UI's data-entry form is the source of the patient,
// referral reason, diagnosis, reply note, and appointment date used below.
// --------------------------------------------------------------------------- //
function pick(v, d) {
  return v !== undefined && v !== null && String(v).trim() !== "" ? v : d;
}

// Merge the intake form over the persona defaults into the rich referral shape the
// bridge's /ode/referral endpoint and referral-note builder consume.
function richReferral(referralId, form, persona) {
  const d = defaultsFor(persona);
  form = form || {};
  const pf = form.patient || {}, cf = form.coverage || {};
  const rf = form.referringProvider || {}, nf = form.renderingProvider || {};
  const sf = form.service || {}, addr = d.patient.address;
  // Persona -> ODE referral direction (selects the directional ServiceRequest profile).
  const direction = persona === "dental" ? "dental-to-medical" : "medical-to-dental";
  return {
    referral_id: referralId,
    direction,
    priority: pick(form.priority, d.priority),
    patient: {
      given: pick(pf.given, d.patient.given),
      family: pick(pf.family, d.patient.family),
      mrn: pick(pf.mrn, d.patient.mrn),
      birthDate: pick(pf.birthDate, d.patient.birthDate),
      gender: pick(pf.gender, d.patient.gender),
      phone: pick(pf.phone, d.patient.phone),
      address: {
        line: pick(pf.addressLine, addr.line),
        city: pick(pf.city, addr.city),
        state: pick(pf.state, addr.state),
        postalCode: pick(pf.postalCode, addr.postalCode),
      },
    },
    coverage: {
      payer: pick(cf.payer, d.coverage.payer),
      member_id: pick(cf.memberId, d.coverage.member_id),
      group: pick(cf.group, d.coverage.group),
      plan: pick(cf.plan, d.coverage.plan),
      relationship: pick(cf.relationship, d.coverage.relationship),
    },
    referring_provider: {
      name: pick(rf.name, d.referring_provider.name),
      npi: pick(rf.npi, d.referring_provider.npi),
      specialty: pick(rf.specialty, d.referring_provider.specialty),
      organization: pick(rf.organization, d.referring_provider.organization),
      phone: pick(rf.phone, d.referring_provider.phone),
    },
    rendering_provider: {
      name: pick(nf.name, d.rendering_provider.name),
      npi: pick(nf.npi, d.rendering_provider.npi),
      specialty: pick(nf.specialty, d.rendering_provider.specialty),
      organization: pick(nf.organization, d.rendering_provider.organization),
    },
    service: {
      code: pick(sf.code, d.service.code),
      system: pick(sf.system, d.service.system),
      display: pick(sf.display, d.service.display),
    },
    diagnoses: diagnosesFromForm(form, d),
    medications: medicationsFromForm(form, d),
    reason_text: pick(form.reason, d.reason_text),
    supporting_info: pick(form.supporting, d.supporting_info),
  };
}

function diagnosesFromForm(form, d) {
  const list = (form && form.diagnoses) || [];
  const out = list
    .filter((x) => x && (x.code || x.display))
    .map((x) => ({ code: x.code || "", system: x.system || "icd10", display: x.display || x.code }));
  return out.length ? out : d.diagnoses;
}

function medicationsFromForm(form, d) {
  const list = (form && form.medications) || [];
  const out = list
    .filter((x) => x && (x.code || x.display))
    .map((x) => ({ code: x.code || "", system: x.system || "rxnorm", display: x.display || x.code }));
  return out.length ? out : (d.medications || []);
}

// FHIR Patient (recordTarget / reply subject) from the intake form.
function patientFromForm(form, persona) {
  const d = defaultsFor(persona).patient;
  const f = (form && form.patient) || {};
  const p = {
    identifier: [{ system: MRN_SYSTEM, value: pick(f.mrn, d.mrn) }],
    name: [{ given: [pick(f.given, d.given)].filter(Boolean), family: pick(f.family, d.family) }],
    gender: pick(f.gender, d.gender),
    birthDate: pick(f.birthDate, d.birthDate),
  };
  return p;
}

// Accept an ISO datetime-local (YYYY-MM-DDTHH:MM) and return an HL7 v2 timestamp.
function apptFromForm(form) {
  const v = reply(form).apptStart || (form && form.appointmentStart);
  if (!v) return futureApptTs();
  const digits = v.replace(/[-:T]/g, "");
  return digits.length >= 12 ? digits.slice(0, 12) + "00" : futureApptTs();
}
function dtToV2(v) {
  if (!v) return "";
  const digits = String(v).replace(/[-:T]/g, "");
  return digits.length >= 12 ? digits.slice(0, 12) + "00" : "";
}
function dateToV2(v) {
  return v ? String(v).replace(/-/g, "").slice(0, 8) : "";
}

// The per-reply content the UI collects (minimal, mostly prefilled). See app.js.
function reply(form) { return (form && form.reply) || {}; }

// The rendering/consulting provider (the peer that replies) — used to prefill the
// accepting provider on an accept, and the booking provider on an appointment.
function renderingProviderOf(form, persona) {
  const d = defaultsFor(persona).rendering_provider || {};
  const f = (form && form.renderingProvider) || {};
  return {
    name: pick(f.name, d.name), npi: pick(f.npi, d.npi),
    specialty: pick(f.specialty, d.specialty),
    organization: pick(f.organization, d.organization),
  };
}
function providerString(p) {
  return [p.npi || "", p.name || ""].join("^").replace(/^\^|\^$/g, "");
}

// Dental result resources for the medical persona's outcome reply. Keeps the CDT +
// perio items (to demonstrate the loss profile) and overlays the entered note.
function resultResourcesFromForm(form) {
  const out = DENTAL_RESULTS.map((r) => Object.assign({}, r));
  const ci = out.find((r) => r.resourceType === "ClinicalImpression");
  const r = reply(form);
  if (r.outcomeNote && ci) ci.summary = r.outcomeNote;
  return out;
}

const esc = (s) => String(s || "").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));

// Trailing newline per <item> so the bridge parser reads one line per item.
function xhtmlList(items) {
  return "<list>" + items.filter(Boolean).map((i) => "<item>" + esc(i) + "</item>\n").join("") + "</list>";
}

function dxOid(system) { return OID[(system || "icd10").toLowerCase()] || OID.icd10; }
function svcOid(system) { return OID[(system || "cpt").toLowerCase()] || OID.cpt; }
function medOid(system) { return OID[(system || "rxnorm").toLowerCase()] || OID.rxnorm; }

// Enriched Referral Note C-CDA carrying the referral-grade dataset (referring +
// rendering providers in the header, coded diagnoses, requested service, coverage,
// priority, clinical justification). Structure mirrors
// python/ode_adapter/fhir_to_ccda._build_rich_referral_note so the bridge's
// transform_referral_note reconstructs the full FHIR the receiver reads.
function referralNoteCda(rich) {
  const ts = v2Timestamp();
  const p = rich.patient || {}, a = rich.address || p.address || {};
  const ref = rich.referring_provider || {}, rnd = rich.rendering_provider || {};
  const cov = rich.coverage || {}, svc = rich.service || {};
  const gender = { male: "M", female: "F" }[(p.gender || "").toLowerCase()] || "UN";
  const bd = (p.birthDate || "").replace(/-/g, "");

  const addr = (p.address || {});
  const addrXml = (addr.line || addr.city || addr.state || addr.postalCode)
    ? "<addr>" +
      (addr.line ? "<streetAddressLine>" + esc(addr.line) + "</streetAddressLine>" : "") +
      (addr.city ? "<city>" + esc(addr.city) + "</city>" : "") +
      (addr.state ? "<state>" + esc(addr.state) + "</state>" : "") +
      (addr.postalCode ? "<postalCode>" + esc(addr.postalCode) + "</postalCode>" : "") +
      "</addr>"
    : "";
  const telXml = p.phone ? '<telecom value="tel:' + esc(p.phone) + '"/>' : "";

  const authorSpec = ref.specialty ? '<code codeSystem="' + OID.nucc + '" displayName="' + esc(ref.specialty) + '"/>' : "";
  const authorOrg = ref.organization ? "<representedOrganization><name>" + esc(ref.organization) + "</name></representedOrganization>" : "";
  const author =
    (ref.npi ? '<id root="http://hl7.org/fhir/sid/us-npi" extension="' + esc(ref.npi) + '"/>' : '<id nullFlavor="UNK"/>') +
    authorSpec + "<assignedPerson><name>" + esc(ref.name || "Unknown Provider") + "</name></assignedPerson>" + authorOrg;

  const rndSpec = rnd.specialty ? '<code codeSystem="' + OID.nucc + '" displayName="' + esc(rnd.specialty) + '"/>' : "";
  const rndOrg = rnd.organization ? "<receivedOrganization><name>" + esc(rnd.organization) + "</name></receivedOrganization>" : "";
  const recipient =
    (rnd.npi ? '<id root="http://hl7.org/fhir/sid/us-npi" extension="' + esc(rnd.npi) + '"/>' : '<id nullFlavor="UNK"/>') +
    rndSpec + "<informationRecipient><name>" + esc(rnd.name || "Unknown Provider") + "</name></informationRecipient>" + rndOrg;

  // Sections.
  const sections = [];
  sections.push(section("42349-1", "Reason for Referral", "Reason for Referral",
    xhtmlList(["Priority: " + (rich.priority || "routine"), rich.reason_text || "Referral for evaluation."])));

  const dxLines = [], dxEntries = [], dentalDx = [];
  (rich.diagnoses || []).forEach((dx) => {
    if ((dx.system || "").toLowerCase() === "cdt") { dentalDx.push("Dental diagnosis (CDT): " + (dx.display || dx.code)); return; }
    if (dx.code) dxEntries.push('<entry><observation classCode="OBS" moodCode="EVN"><value code="' + esc(dx.code) + '" codeSystem="' + dxOid(dx.system) + '" displayName="' + esc(dx.display || "") + '"/></observation></entry>');
    dxLines.push((dx.display || "") + (dx.code ? " (" + dx.code + ")" : ""));
  });
  if (dxLines.length) sections.push(sectionEntries("11450-4", "Problem List", "Problems / Diagnoses", dxLines, dxEntries));

  const medLines = [], medEntries = [];
  (rich.medications || []).forEach((m) => {
    if (m.code) {
      medEntries.push('<entry><substanceAdministration classCode="SBADM" moodCode="EVN">' +
        '<consumable><manufacturedProduct><manufacturedMaterial>' +
        '<code code="' + esc(m.code) + '" codeSystem="' + medOid(m.system) + '" displayName="' + esc(m.display || "") + '"/>' +
        '</manufacturedMaterial></manufacturedProduct></consumable>' +
        '</substanceAdministration></entry>');
    }
    medLines.push((m.display || "") + (m.code ? " (" + m.code + ")" : ""));
  });
  if (medLines.length) sections.push(sectionEntries("10160-0", "Medications", "Current Medications", medLines, medEntries));

  if (svc.code || svc.display) {
    const codeAttr = svc.code ? 'code="' + esc(svc.code) + '" codeSystem="' + svcOid(svc.system) + '" ' : "";
    const svcEntry = '<entry><procedure classCode="PROC" moodCode="RQO"><code ' + codeAttr + 'displayName="' + esc(svc.display || "") + '"/></procedure></entry>';
    sections.push(sectionEntries("18776-5", "Plan of Treatment", "Requested Service",
      ["Requested: " + (svc.display || svc.code) + (svc.code ? " (" + svc.code + ")" : "")], [svcEntry]));
  }

  const covLines = [];
  if (cov.payer) covLines.push("Payer: " + cov.payer);
  if (cov.member_id) covLines.push("Member ID: " + cov.member_id);
  if (cov.group) covLines.push("Group: " + cov.group);
  if (cov.plan) covLines.push("Plan: " + cov.plan);
  if (cov.relationship) covLines.push("Relationship: " + cov.relationship);
  if (covLines.length) sections.push(section("48768-6", "Payers", "Insurance / Coverage", xhtmlList(covLines)));

  if (rich.supporting_info) sections.push(section("10164-2", "History of Present Illness", "Clinical Information", xhtmlList([rich.supporting_info])));
  if (dentalDx.length) sections.push(section("34109-9", "Note", "Dental Findings (narrative)", xhtmlList(dentalDx)));

  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<ClinicalDocument xmlns="urn:hl7-org:v3">\n' +
    '  <realmCode code="US"/>\n' +
    '  <templateId root="2.16.840.1.113883.10.20.22.1.4"/>\n' +
    '  <code code="57133-1" codeSystem="2.16.840.1.113883.6.1" displayName="Referral Note"/>\n' +
    "  <title>Referral Request</title>\n" +
    '  <effectiveTime value="' + ts + '"/>\n' +
    "  <recordTarget><patientRole>" +
    '<id root="' + MRN_SYSTEM + '" extension="' + esc(p.mrn || "") + '"/>' + addrXml + telXml +
    "<patient><name><given>" + esc(p.given || "") + "</given><family>" + esc(p.family || "") + "</family></name>" +
    '<administrativeGenderCode code="' + gender + '"/>' + (bd ? '<birthTime value="' + esc(bd) + '"/>' : "") +
    "</patient></patientRole></recordTarget>\n" +
    '  <author><time value="' + ts + '"/><assignedAuthor>' + author + "</assignedAuthor></author>\n" +
    "  <informationRecipient><intendedRecipient>" + recipient + "</intendedRecipient></informationRecipient>\n" +
    "  <component><structuredBody>\n" + sections.join("\n") + "\n  </structuredBody></component>\n" +
    "</ClinicalDocument>\n"
  );
}

function section(code, name, title, textXml) {
  return "    <component><section>\n" +
    '      <code code="' + code + '" codeSystem="2.16.840.1.113883.6.1" displayName="' + esc(name) + '"/>\n' +
    "      <title>" + esc(title) + "</title>\n" +
    "      <text>" + textXml + "</text>\n" +
    "    </section></component>";
}

function sectionEntries(code, name, title, lines, entries) {
  return "    <component><section>\n" +
    '      <code code="' + code + '" codeSystem="2.16.840.1.113883.6.1" displayName="' + esc(name) + '"/>\n' +
    "      <title>" + esc(title) + "</title>\n" +
    "      <text>" + xhtmlList(lines) + "</text>\n" +
    "      " + entries.join("") + "\n" +
    "    </section></component>";
}

// --------------------------------------------------------------------------- //
// HL7 v2 builders — mirror python/ode_adapter/hl7v2.py so the bridge parses the
// referral id (ORC-2), order status (ORC-5), and appointment time (SCH-11) exactly.
// --------------------------------------------------------------------------- //
function v2Timestamp(d) {
  d = d || new Date();
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return (
    d.getUTCFullYear() +
    p(d.getUTCMonth() + 1) +
    p(d.getUTCDate()) +
    p(d.getUTCHours()) +
    p(d.getUTCMinutes()) +
    p(d.getUTCSeconds())
  );
}

// opts: { appointmentStart, appointmentEnd, note, acceptingProvider, statusReason,
//         periodEnd } — the reply-content fields degraded onto the v2 side
// (ORC-12/15/16, SCH-11/12, NTE). Mirrors python/ode_adapter/hl7v2.build.
function setIdx(seg, idx, val) {
  while (seg.length <= idx) seg.push("");
  seg[idx] = val;
}
function buildV2(messageType, referralId, orderStatus, opts) {
  opts = opts || {};
  const ts = v2Timestamp();
  const controlId = "ADP" + ts;
  const msh = [
    "MSH", "^~\\&", "OHIA-360ODE-ADAPTER", "OHIA",
    "EHR", "ORG", ts, "", messageType, controlId, "P", "2.5.1",
  ].join("|");
  const orderControl = messageType.indexOf("OSU") === 0 ? "SC" : "NW";
  const orc = ["ORC", orderControl, referralId, "", "", orderStatus || ""];
  if (opts.acceptingProvider) setIdx(orc, 12, opts.acceptingProvider);
  if (opts.periodEnd) setIdx(orc, 15, opts.periodEnd);
  if (opts.statusReason) setIdx(orc, 16, opts.statusReason);
  let msg = msh + "\r" + orc.join("|") + "\r";
  if (messageType.indexOf("SIU") === 0) {
    const sch = ["SCH"].concat(new Array(12).fill(""));
    sch[11] = opts.appointmentStart || "";
    sch[12] = opts.appointmentEnd || "";
    msg += sch.join("|") + "\r";
  }
  if (opts.note) msg += ["NTE", "1", "", opts.note].join("|") + "\r";
  return msg;
}

function msgId() {
  return "<" + Date.now() + "-" + Math.random().toString(16).slice(2) + "@harness>";
}

function envelope(referralId, transaction, v2, documents, opts) {
  opts = opts || {};
  return {
    direct_message_id: msgId(),
    submission_set_id: "urn:ohia:submissionset:" + referralId,
    sender_direct_address: opts.sender || MEDICAL_ADDR,
    recipient_direct_address: opts.recipient || DENTAL_ADDR,
    transaction: transaction,
    hl7v2: v2,
    documents: documents || [],
  };
}

// A minimal Consultation Note C-CDA the bridge's transform_consultation_note can
// parse (section 51847-2 Assessment and Plan). Used for simulated medical replies
// (PCC-57 outcome / PCC-59 interim) in the dental persona.
function consultationNoteCda(patient, assessment, plan, opts) {
  opts = opts || {};
  const ts = v2Timestamp();
  const ident = (patient && patient.identifier && patient.identifier[0]) || {};
  const name = (patient && patient.name && patient.name[0]) || {};
  const given = (name.given || []).join(" ");
  const esc = (s) => String(s || "").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
  const title = opts.interim ? "Interim Consultation Note" : "Referral Outcome — Consultation Note";
  const items = [];
  if (opts.disposition) items.push("Disposition: " + opts.disposition);
  if (assessment) items.push("Assessment: " + assessment);
  if (plan) items.push("Plan: " + plan);
  const list = "<list>" + items.map((i) => "<item>" + esc(i) + "</item>\n").join("") + "</list>";
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<ClinicalDocument xmlns="urn:hl7-org:v3">\n' +
    '  <realmCode code="US"/>\n' +
    '  <code code="11488-4" codeSystem="2.16.840.1.113883.6.1" displayName="Consultation Note"/>\n' +
    "  <title>" + esc(title) + "</title>\n" +
    '  <effectiveTime value="' + ts + '"/>\n' +
    "  <recordTarget><patientRole>" +
    '<id root="' + esc(ident.system || "urn:ohia") + '" extension="' + esc(ident.value || "") + '"/>' +
    "<patient><name><given>" + esc(given) + "</given><family>" + esc(name.family || "") + "</family></name></patient>" +
    "</patientRole></recordTarget>\n" +
    "  <component><structuredBody>\n" +
    "    <component><section>\n" +
    '      <code code="51847-2" codeSystem="2.16.840.1.113883.6.1" displayName="Assessment and Plan"/>\n' +
    "      <title>Assessment and Plan</title>\n" +
    "      <text>" + list + "</text>\n" +
    "    </section></component>\n" +
    "  </structuredBody></component>\n" +
    "</ClinicalDocument>\n"
  );
}

function task(referralId, status) {
  return {
    resourceType: "Task",
    id: "task-" + referralId,
    status: status,
    identifier: [{ system: REFERRAL_SYSTEM, value: referralId }],
  };
}

function futureApptTs() {
  const d = new Date(Date.now() + 7 * 24 * 3600 * 1000);
  d.setUTCHours(14, 30, 0, 0);
  return v2Timestamp(d);
}

// --------------------------------------------------------------------------- //
// DENTAL persona — PMS (FHIR) initiates; simulates the medical peer's 360X replies.
// initiate -> POST /ode/referral ; replies -> POST /360x/inbound
// --------------------------------------------------------------------------- //
function dentalInitiate(referralId, sampleReferralCda, form) {
  const rich = richReferral(referralId, form, "dental");
  rich.sender = DENTAL_ADDR;
  rich.recipient = MEDICAL_ADDR;
  return { endpoint: "/ode/referral", kind: "fhir-out", body: rich };
}

function dentalReply(id, referralId, form) {
  const back = { sender: MEDICAL_ADDR, recipient: DENTAL_ADDR };
  const patient = patientFromForm(form, "dental");
  const r = reply(form);
  const acc = renderingProviderOf(form, "dental");   // physician peer accepts
  switch (id) {
    case "pcc56-accept":
      return { endpoint: "/360x/inbound", kind: "360x-in",
        payload: envelope(referralId, "PCC-56", buildV2("OSU^O51", referralId, "IP", {
          note: pick(r.acceptNote, "Referral received and accepted; patient will be seen."),
          acceptingProvider: providerString(acc),
          periodEnd: dateToV2(r.expectedBy),
        }), [], back) };
    case "pcc56-decline":
      return { endpoint: "/360x/inbound", kind: "360x-in",
        payload: envelope(referralId, "PCC-56", buildV2("OSU^O51", referralId, "CA", {
          statusReason: pick(r.declineReason, "insufficient-info"),
          note: pick(r.declineComment, "Unable to accept this referral."),
        }), [], back) };
    case "pcc59-interim":
      return { endpoint: "/360x/inbound", kind: "360x-in",
        payload: envelope(referralId, "PCC-59", buildV2("OMG^O19", referralId, "", {
          note: pick(r.interimNote, "Evaluation underway; findings pending."),
        }),
          [{ id: referralId + "-interim", mime_type: "text/xml",
             content: consultationNoteCda(patient,
               pick(r.interimNote, "Extraction completed; healing well."),
               "Re-evaluate in 2 weeks.", { interim: true }) }], back) };
    case "pcc57-outcome":
      return { endpoint: "/360x/inbound", kind: "360x-in",
        payload: envelope(referralId, "PCC-57", buildV2("OMG^O19", referralId, "", {
          note: pick(r.disposition, "cleared"),
        }),
          [{ id: referralId + "-outcome", mime_type: "text/xml",
             content: consultationNoteCda(patient,
               pick(r.outcomeNote, "Active disease remediated; cleared for radiation."),
               "Routine recall in 3 months.",
               { disposition: pick(r.disposition, "cleared") }) }], back) };
    case "pcc60-appointment":
      return { endpoint: "/360x/inbound", kind: "360x-in",
        payload: envelope(referralId, "PCC-60", buildV2("SIU^S12", referralId, "", {
          appointmentStart: apptFromForm(form),
          appointmentEnd: dtToV2(r.apptEnd),
          note: pick(r.location, "Main clinic, Suite 200"),
          acceptingProvider: providerString(acc),
          statusReason: pick(r.apptType, "in-person"),
        }), [], back) };
    case "pcc61-noshow":
      return { endpoint: "/360x/inbound", kind: "360x-in",
        payload: envelope(referralId, "PCC-61", buildV2("SIU^S26", referralId, "", {
          statusReason: pick(r.noshowReason, "no-show"),
          note: pick(r.reschedule, "Attempting to reschedule; will advise."),
        }), [], back) };
    default:
      return null;
  }
}

// --------------------------------------------------------------------------- //
// MEDICAL persona — EHR (360X) initiates; simulates the dental FHIR replies.
// initiate -> POST /360x/inbound ; replies -> POST /ode/task-event | /ode/appointment-event
// --------------------------------------------------------------------------- //
function medicalInitiate(referralId, sampleReferralCda, form) {
  // Build the enriched Referral Note from the intake so the FHIR the bridge
  // reconstructs (what the dentist reads) carries the full diagnose/treat/bill set.
  const rich = richReferral(referralId, form, "medical");
  return {
    endpoint: "/360x/inbound",
    kind: "360x-in",
    payload: envelope(referralId, "PCC-55", buildV2("OMG^O19", referralId),
      [{ id: referralId + "-referral", mime_type: "text/xml", content: referralNoteCda(rich) }],
      { sender: MEDICAL_ADDR, recipient: DENTAL_ADDR }),
  };
}

function medicalReply(id, referralId, form) {
  const patient = patientFromForm(form, "medical");
  const r = reply(form);
  const acc = renderingProviderOf(form, "medical");   // dentist peer accepts
  switch (id) {
    case "pcc56-accept": {
      const t = task(referralId, "in-progress");
      t.owner = { display: acc.name };
      if (acc.npi) t.owner.identifier = [{ system: "http://hl7.org/fhir/sid/us-npi", value: acc.npi }];
      t.note = [{ text: pick(r.acceptNote, "Referral received and accepted; patient will be seen.") }];
      if (r.expectedBy) t.restriction = { period: { end: r.expectedBy } };
      return { endpoint: "/ode/task-event", kind: "fhir-out", body: { task: t, result_resources: [] } };
    }
    case "pcc56-decline": {
      const t = task(referralId, "rejected");
      const code = pick(r.declineReason, "insufficient-info");
      t.statusReason = { coding: [{ system: "urn:ohia:cow:decline-reason", code: code }], text: code };
      t.note = [{ text: pick(r.declineComment, "Unable to accept this referral.") }];
      return { endpoint: "/ode/task-event", kind: "fhir-out", body: { task: t, result_resources: [] } };
    }
    case "pcc59-interim": {
      const interim = [{ resourceType: "ClinicalImpression",
        summary: pick(r.interimNote, INTERIM_NOTE[0].summary) }];
      const t = task(referralId, "in-progress");
      t.note = [{ text: pick(r.interimNote, "Evaluation underway; findings pending.") }];
      return { endpoint: "/ode/task-event", kind: "fhir-out",
        body: { task: t, interim: true, result_resources: interim, patient: patient } };
    }
    case "pcc57-outcome": {
      const res = resultResourcesFromForm(form);
      const disposition = pick(r.disposition, "cleared");
      const ci = res.find((x) => x.resourceType === "ClinicalImpression");
      if (ci) ci.summary = "Disposition: " + disposition + ". " + (r.outcomeNote || ci.summary);
      return { endpoint: "/ode/task-event", kind: "fhir-out",
        body: { task: task(referralId, "completed"), result_resources: res, patient: patient } };
    }
    case "pcc60-appointment":
      return { endpoint: "/ode/appointment-event", kind: "fhir-out",
        body: { referral_id: referralId, no_show: false,
          appointment_start: apptFromForm(form), appointment_end: dtToV2(r.apptEnd),
          location: pick(r.location, "Main clinic, Suite 200"),
          provider: providerString(acc), appt_type: pick(r.apptType, "in-person") } };
    case "pcc61-noshow":
      return { endpoint: "/ode/appointment-event", kind: "fhir-out",
        body: { referral_id: referralId, no_show: true,
          reason: pick(r.noshowReason, "no-show"),
          reschedule: pick(r.reschedule, "Attempting to reschedule; will advise.") } };
    default:
      return null;
  }
}

// PCC-58 Referral Cancellation — an INITIATOR action (Step 1 side). The initiator
// withdraws the referral: dental-initiated cancels via a FHIR Task change (bridge
// emits OSU^O51 CA); medical-initiated cancels via an inbound 360X OSU^O51 (CA).
function dentalCancel(referralId, form) {
  return { endpoint: "/ode/task-event", kind: "fhir-out",
    body: { task: task(referralId, "cancelled"), result_resources: [] } };
}
function medicalCancel(referralId, form) {
  return { endpoint: "/360x/inbound", kind: "360x-in",
    payload: envelope(referralId, "PCC-58", buildV2("OSU^O51", referralId, "CA"), [],
      { sender: MEDICAL_ADDR, recipient: DENTAL_ADDR }) };
}

// Transaction catalog shared with the UI (labels + descriptions per persona).
const REPLY_CATALOG = [
  { id: "pcc56-accept", label: "PCC-56 — Accept", tx: "PCC-56" },
  { id: "pcc56-decline", label: "PCC-56 — Decline", tx: "PCC-56" },
  { id: "pcc59-interim", label: "PCC-59 — Interim Consultation Note", tx: "PCC-59" },
  { id: "pcc57-outcome", label: "PCC-57 — Referral Outcome", tx: "PCC-57" },
  { id: "pcc60-appointment", label: "PCC-60 — Appointment", tx: "PCC-60" },
  { id: "pcc61-noshow", label: "PCC-61 — No-Show", tx: "PCC-61" },
];

const DENTAL_REPLY_DESC = {
  "pcc56-accept": "Physician peer accepts — carries the accepting provider (Task.owner), an acknowledgment note, and an expected-by date. Bridge writes Task.status=accepted.",
  "pcc56-decline": "Physician peer declines with a coded reason (Task.statusReason) + comment. Bridge sets Task.status=rejected and revokes the ServiceRequest.",
  "pcc59-interim": "Physician peer sends an interim note + finding. Bridge parses the C-CDA to interim resources incl. an Observation (Task.output); businessStatus=interim-results.",
  "pcc57-outcome": "Physician peer sends the final outcome + clearance disposition. Bridge parses the C-CDA, adds a coded clearance Observation, completes the loop.",
  "pcc60-appointment": "Physician peer books an appointment (SIU^S12) — start/end, provider, type, location. Bridge writes an Appointment (booked).",
  "pcc61-noshow": "Physician peer reports a no-show (SIU^S26) with a reason + reschedule note. Bridge writes a Communication.",
};

const MEDICAL_REPLY_DESC = {
  "pcc56-accept": "Dental side accepts — accepting provider (Task.owner), acknowledgment note, expected-by. Bridge emits OSU^O51 (IP) + ORC-12/ORC-15/NTE.",
  "pcc56-decline": "Dental side declines with a coded reason + comment. Bridge emits OSU^O51 (CA) + ORC-16/NTE.",
  "pcc59-interim": "Dental side sends an interim note + finding. Bridge builds a C-CDA Consultation Note and emits OMG^O19 + document.",
  "pcc57-outcome": "Dental work complete + clearance disposition. Bridge builds the outcome C-CDA (surfacing degrade notes for CDT/perio) and emits OMG^O19 + document.",
  "pcc60-appointment": "Dental side books an appointment — start/end, provider, type, location. Bridge emits SIU^S12.",
  "pcc61-noshow": "Patient no-show + reschedule. Bridge emits SIU^S26 No-Show Notification.",
};

module.exports = {
  REFERRAL_SYSTEM, DENTAL_ADDR, MEDICAL_ADDR, DEMO_PATIENT, DENTAL_RESULTS, INTERIM_NOTE,
  REPLY_CATALOG, DENTAL_REPLY_DESC, MEDICAL_REPLY_DESC,
  DEFAULT_MED_TO_DENTAL, DEFAULT_DENTAL_TO_MED, defaultsFor, richReferral, referralNoteCda,
  v2Timestamp, buildV2, msgId, envelope, consultationNoteCda, task, futureApptTs,
  dentalInitiate, dentalReply, medicalInitiate, medicalReply,
  dentalCancel, medicalCancel,
};
