"use strict";
/*
 * Symmetric 360X Edge Server — harness UI server (zero dependencies, Node stdlib).
 *
 * Hosts TWO mirror developer harnesses over ONE shared payload library
 * (lib/payloads.js):
 *
 *   Dental persona  — the PMS (FHIR) initiates a referral; the UI simulates every
 *                     360X reply the medical peer could send. Replies flow through
 *                     the bridge, are written to FHIR, and the PMS reads its inbox.
 *   Medical persona — the EHR (360X) initiates; the UI simulates the dental FHIR
 *                     replies; the bridge emits the 360X the EHR would ingest.
 *
 * The Node layer only builds wire payloads and forwards them to the Python bridge:
 *   browser -> this server (/api/*) -> bridge (/ode/referral, /360x/inbound,
 *              /ode/task-event, /ode/appointment-event, /episodes/:id/inbox)
 *
 * Every payload is also EXPORTABLE (GET /api/export) as files for offline testing.
 *
 * Run:  node server.js   |   ADAPTER_URL=http://127.0.0.1:8000 PORT=4000 node server.js
 */

const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const P = require("./lib/payloads");

const PORT = parseInt(process.env.PORT || "4000", 10);
const ADAPTER_URL = process.env.ADAPTER_URL || "http://127.0.0.1:8000";
const PUBLIC_DIR = path.join(__dirname, "public");

// Canonical sample Referral Note C-CDA (shared with the Python reference).
const SAMPLE_CDA_PATH = path.join(__dirname, "..", "python", "samples", "referral_request.xml");
function loadSampleCda() {
  try {
    return fs.readFileSync(SAMPLE_CDA_PATH, "utf8");
  } catch (e) {
    console.warn(`[warn] could not read sample C-CDA at ${SAMPLE_CDA_PATH}: ${e.message}`);
    return (
      '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<ClinicalDocument xmlns="urn:hl7-org:v3">' +
      '<code code="57133-1" codeSystem="2.16.840.1.113883.6.1" displayName="Referral note"/>' +
      "<title>Referral for Dental Clearance</title></ClinicalDocument>"
    );
  }
}
const SAMPLE_CDA = loadSampleCda();

// --------------------------------------------------------------------------- //
// Build the {endpoint, kind, payload|body} action spec for a persona + action.
// action is "initiate" or a reply id from P.REPLY_CATALOG.
// --------------------------------------------------------------------------- //
function buildAction(persona, action, referralId, form) {
  if (persona === "dental") {
    if (action === "initiate") return P.dentalInitiate(referralId, SAMPLE_CDA, form);
    if (action === "cancel") return P.dentalCancel(referralId, form);
    return P.dentalReply(action, referralId, form);
  }
  if (persona === "medical") {
    if (action === "initiate") return P.medicalInitiate(referralId, SAMPLE_CDA, form);
    if (action === "cancel") return P.medicalCancel(referralId, form);
    return P.medicalReply(action, referralId, form);
  }
  return null;
}

// Form is passed from the browser as base64(JSON) on export links.
function decodeForm(b64) {
  if (!b64) return null;
  try {
    return JSON.parse(Buffer.from(b64, "base64").toString("utf8"));
  } catch (e) {
    return null;
  }
}

// --------------------------------------------------------------------------- //
// Adapter HTTP client (built-in http/https; no dependencies).
// --------------------------------------------------------------------------- //
function adapterRequest(method, pathname, bodyObj) {
  return new Promise((resolve, reject) => {
    let url;
    try {
      url = new URL(pathname, ADAPTER_URL);
    } catch (e) {
      return reject(e);
    }
    const lib = url.protocol === "https:" ? https : http;
    const data = bodyObj != null ? Buffer.from(JSON.stringify(bodyObj)) : null;
    const opts = {
      method: method,
      hostname: url.hostname,
      port: url.port || (url.protocol === "https:" ? 443 : 80),
      path: url.pathname + url.search,
      headers: {},
    };
    if (data) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["Content-Length"] = data.length;
    }
    const req = lib.request(opts, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        let json;
        try {
          json = text ? JSON.parse(text) : null;
        } catch (e) {
          json = text;
        }
        resolve({ status: res.statusCode, body: json });
      });
    });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

function sendJson(res, status, obj) {
  const body = Buffer.from(JSON.stringify(obj));
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
  });
  res.end(body);
}

function sendDownload(res, filename, contentType, text) {
  const body = Buffer.from(text);
  res.writeHead(200, {
    "Content-Type": contentType,
    "Content-Length": body.length,
    "Content-Disposition": `attachment; filename="${filename}"`,
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      if (!text) return resolve({});
      try {
        resolve(JSON.parse(text));
      } catch (e) {
        reject(new Error("invalid JSON body"));
      }
    });
    req.on("error", reject);
  });
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
};

function serveStatic(req, res, urlPath) {
  let rel = urlPath === "/" ? "/index.html" : urlPath;
  rel = decodeURIComponent(rel.split("?")[0]);
  const filePath = path.join(PUBLIC_DIR, path.normalize(rel));
  if (!filePath.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    return res.end("forbidden");
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      return res.end("not found");
    }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(data);
  });
}

// --------------------------------------------------------------------------- //
// Run an action: build payload -> POST to the bridge -> return request+response.
// --------------------------------------------------------------------------- //
async function runAction(persona, action, referralId, form) {
  const spec = buildAction(persona, action, referralId, form);
  if (!spec) return { ok: false, error: `unknown action: ${persona}/${action}` };
  const outgoing = spec.payload || spec.body;
  try {
    const r = await adapterRequest("POST", spec.endpoint, outgoing);
    return {
      ok: r.status >= 200 && r.status < 300,
      persona,
      action,
      endpoint: spec.endpoint,
      kind: spec.kind,
      request: outgoing,
      status: r.status,
      response: r.body,
    };
  } catch (e) {
    return { ok: false, error: e.message, adapterUrl: ADAPTER_URL, request: outgoing };
  }
}

// --------------------------------------------------------------------------- //
// Router.
// --------------------------------------------------------------------------- //
async function handleApi(req, res, urlPath, query) {
  // Metadata for the UI to render both personas.
  if (req.method === "GET" && urlPath === "/api/meta") {
    return sendJson(res, 200, {
      adapterUrl: ADAPTER_URL,
      addresses: { dental: P.DENTAL_ADDR, medical: P.MEDICAL_ADDR },
      replies: P.REPLY_CATALOG,
      descriptions: { dental: P.DENTAL_REPLY_DESC, medical: P.MEDICAL_REPLY_DESC },
      defaults: { dental: P.DEFAULT_DENTAL_TO_MED, medical: P.DEFAULT_MED_TO_DENTAL },
    });
  }

  // Pass-through reads.
  if (req.method === "GET" && (urlPath === "/api/plugins" || urlPath === "/api/episodes" || urlPath === "/api/health")) {
    const target = urlPath === "/api/health" ? "/healthz" : urlPath.replace("/api", "");
    try {
      const r = await adapterRequest("GET", target, null);
      return sendJson(res, 200, { ok: r.status >= 200 && r.status < 300, status: r.status, body: r.body });
    } catch (e) {
      return sendJson(res, 200, { ok: false, error: e.message, adapterUrl: ADAPTER_URL });
    }
  }

  // Inbox: the FHIR the PMS/EHR reads for a referral.
  if (req.method === "GET" && urlPath.indexOf("/api/inbox/") === 0) {
    const referralId = decodeURIComponent(urlPath.slice("/api/inbox/".length));
    try {
      const r = await adapterRequest("GET", `/episodes/${encodeURIComponent(referralId)}/inbox`, null);
      return sendJson(res, 200, { ok: r.status >= 200 && r.status < 300, status: r.status, body: r.body });
    } catch (e) {
      return sendJson(res, 200, { ok: false, error: e.message, adapterUrl: ADAPTER_URL });
    }
  }

  // Export a payload as a downloadable file (no live push).
  //   /api/export?persona=&action=&referralId=&part=all|envelope|hl7|cda|fhir
  if (req.method === "GET" && urlPath === "/api/export") {
    const { persona, action, referralId, part } = query;
    const spec = buildAction(persona, action || "initiate", referralId || "REF-1001", decodeForm(query.form));
    if (!spec) return sendJson(res, 404, { ok: false, error: "unknown action" });
    const base = `${persona}-${action || "initiate"}-${referralId || "REF-1001"}`;
    const p = part || "all";

    if (p === "hl7") {
      const v2 = spec.payload ? spec.payload.hl7v2 : null;
      if (!v2) return sendJson(res, 400, { ok: false, error: "no HL7 v2 for this action (FHIR side)" });
      return sendDownload(res, `${base}.hl7`, "text/plain; charset=utf-8", v2);
    }
    if (p === "cda") {
      const docs = (spec.payload && spec.payload.documents) || [];
      if (!docs.length) return sendJson(res, 400, { ok: false, error: "no C-CDA document for this action" });
      return sendDownload(res, `${base}.xml`, "application/xml; charset=utf-8", docs[0].content);
    }
    if (p === "envelope") {
      if (!spec.payload) return sendJson(res, 400, { ok: false, error: "no 360X envelope (FHIR side)" });
      return sendDownload(res, `${base}.envelope.json`, "application/json; charset=utf-8", JSON.stringify(spec.payload, null, 2));
    }
    if (p === "fhir") {
      if (!spec.body) return sendJson(res, 400, { ok: false, error: "no FHIR body (360X side)" });
      return sendDownload(res, `${base}.fhir.json`, "application/json; charset=utf-8", JSON.stringify(spec.body, null, 2));
    }
    // all: a manifest bundling every part for offline testing.
    const manifest = {
      persona, action: action || "initiate", referralId: referralId || "REF-1001",
      endpoint: spec.endpoint, kind: spec.kind,
      "360x": spec.payload || null,
      fhir: spec.body || null,
    };
    return sendDownload(res, `${base}.json`, "application/json; charset=utf-8", JSON.stringify(manifest, null, 2));
  }

  // Run an action:  POST /api/run/:persona/:action   { referralId }
  if (req.method === "POST" && urlPath.indexOf("/api/run/") === 0) {
    const parts = urlPath.slice("/api/run/".length).split("/");
    const persona = parts[0];
    const action = parts[1];
    let payload;
    try {
      payload = await readBody(req);
    } catch (e) {
      return sendJson(res, 400, { ok: false, error: e.message });
    }
    const referralId = (payload && payload.referralId) || "REF-1001";
    const result = await runAction(persona, action, referralId, payload && payload.form);
    return sendJson(res, 200, result);
  }

  return sendJson(res, 404, { ok: false, error: "no such api route" });
}

const server = http.createServer((req, res) => {
  const full = (req.url || "/");
  const urlPath = full.split("?")[0];
  const query = {};
  const qs = full.indexOf("?") >= 0 ? full.slice(full.indexOf("?") + 1) : "";
  qs.split("&").forEach((pair) => {
    if (!pair) return;
    const [k, v] = pair.split("=");
    query[decodeURIComponent(k)] = decodeURIComponent(v || "");
  });
  if (urlPath.indexOf("/api/") === 0) {
    handleApi(req, res, urlPath, query).catch((e) => {
      sendJson(res, 500, { ok: false, error: e.message });
    });
    return;
  }
  serveStatic(req, res, urlPath);
});

server.listen(PORT, () => {
  console.log(`Symmetric 360X Edge Server — harness UI  ->  http://localhost:${PORT}`);
  console.log(`Proxying bridge at                       ->  ${ADAPTER_URL}`);
  console.log(`(start the bridge with: uvicorn ode_adapter.app:app  in ../python)`);
});
