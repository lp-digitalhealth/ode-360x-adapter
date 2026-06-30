# Running a FHIR server — HAPI & Firely (beginner guide)

Written for people who have **never run a FHIR server before**. A "FHIR server" is just a
web application that stores healthcare data and speaks the FHIR REST API. You talk to it
over normal web URLs.

Three things every FHIR server has, which you'll use below to check it's working:
- a **base URL** — the root of the FHIR API (e.g. `http://localhost:8080/fhir`)
- a **`/metadata` endpoint** — returns the server's *CapabilityStatement*; if this loads,
  the server is alive and healthy
- often a **web UI** — a browser page for clicking around the data (HAPI has a nice one)

> **Never put real patient data (PHI) in these test setups.** They have no security turned
> on by default and are for development and testing only.

---

## Option 0 — Try one in your browser, install nothing

The gentlest start. These are public test servers — shared, periodically wiped, **test data only**:
- **HAPI public server:** https://hapi.fhir.org/  (FHIR base: `https://hapi.fhir.org/baseR4`)
- **Firely public sandbox:** https://server.fire.ly

Open the first link, click around, and you've seen a FHIR server. When you want your *own*,
read on.

---

## What you need for your own server: Docker

Both servers run most easily as a **Docker container** — a self-contained package that
includes everything (you do **not** need to install Java or .NET yourself).

1. Install **Docker Desktop**: https://www.docker.com/products/docker-desktop/
   (Windows, Mac, or Linux. Accept the defaults.)
2. Launch Docker Desktop and wait until it says it's running.
3. Open a terminal: **PowerShell** on Windows, **Terminal** on Mac/Linux. You'll paste
   commands there.

That's the only prerequisite for the Docker steps below.

---

## HAPI FHIR — the easy one (no license needed)

HAPI is open source and free. There's nothing to sign up for.

### Start it
Paste this into your terminal:
```bash
docker run -d --name hapi -p 8080:8080 hapiproject/hapi:latest
```
What this does: downloads HAPI the first time (a few minutes — it's a big image), then starts
it in the background (`-d`) named `hapi`, and connects your computer's port 8080 to the
server. The **first startup can take a minute or two** even after download.

### Confirm it's working
In your browser:
- Web UI: **http://localhost:8080/**
- Health check: **http://localhost:8080/fhir/metadata** ← if you get a big JSON/XML page, it works
- **FHIR base URL** (the address you give other tools, and the adapter): **http://localhost:8080/fhir**

### Stop / start / remove
```bash
docker stop hapi      # stop it
docker start hapi     # start it again (data is kept while the container exists)
docker logs hapi      # see what it's doing / troubleshoot
docker rm -f hapi     # delete the container entirely
```

That's it. For most ODE work, HAPI is all you need, and it ships ready for FHIR R4.

---

## Firely Server — needs a free license

Firely is a commercial server with a **free evaluation license**. Slightly more setup than
HAPI because of the license file, but still straightforward.

### Step 1 — Get the free license
Go to **https://fire.ly/firely-server-trial/**, fill in the form, and Firely emails you a
**license file** (and a link to download the server if you want the non-Docker version).
The evaluation license is **time-limited and renewable** — if it expires, just request a new
one from the same page. (Terms can change; check what the signup page says. As of late 2025
the evaluation license also limited how long the server runs before it needs a restart.)

Save the license file — it's named **`firelyserver-license.json`** — into a folder you'll use
as your working directory, e.g. `C:\FirelyServer` (Windows) or `~/firely` (Mac/Linux).

### Step 2 — Start it (Docker)
Open your terminal **in that folder**, then:

**Windows (PowerShell):**
```powershell
docker run -d -p 8080:4080 --name firely.server -v ${PWD}/firelyserver-license.json:/app/firelyserver-license.json firely/server
```
**Mac/Linux:**
```bash
docker run -d -p 8080:4080 --name firely.server -v "$PWD/firelyserver-license.json:/app/firelyserver-license.json" firely/server
```
Note the port mapping is **8080:4080** — Firely listens on 4080 *inside* the container, and
we expose it as 8080 on your computer. The `-v ...` part hands the license file to the server.

### Confirm it's working
In your browser:
- Landing page: **http://localhost:8080/**
- Health check: **http://localhost:8080/metadata**
- **FHIR base URL:** **http://localhost:8080** (Firely serves FHIR at the root — e.g.
  `http://localhost:8080/Patient`)

Firely comes preloaded with STU3 and R4 conformance resources.

### Stop / start / remove
```bash
docker stop firely.server
docker start firely.server
docker logs firely.server      # if it won't start, the log usually says "license ..." 
docker rm -f firely.server
```

### Not using Docker? (Windows native)
Prefer to run it directly on Windows: download the Firely Server binaries + license from the
trial page, install the **ASP.NET Core Runtime 8.x Hosting Bundle**
(https://dotnet.microsoft.com/download/dotnet/8.0), unzip the server into a folder, drop the
license file in, point `appsettings.json` at it, and run the start script. It serves on port
**4080** by default (browse `http://localhost:4080/`). The Docker route above is easier for
first-timers.

---

## Picking between them

- **Just getting started, or testing the ODE adapter?** Use **HAPI** — no license, huge
  community, and it's what the project's `docker-compose` already uses.
- **Want to evaluate Firely specifically** (e.g. its validation, or because your org uses
  .NET)? Use the **free trial** above, or click the public sandbox to look first.

## Common gotchas
- **"Port is already in use" / page won't load** — something else is on 8080. Change the
  first number, e.g. `-p 8090:8080` (HAPI) or `-p 8090:4080` (Firely), then browse `:8090`.
- **HAPI seems down right after starting** — give it 1–2 minutes; watch `docker logs hapi`.
- **Firely exits immediately** — almost always the license file: wrong name, wrong folder,
  or expired. Re-check Step 1 and `docker logs firely.server`.
- **Docker command not found** — Docker Desktop isn't installed or isn't running yet.

---
*Commands and URLs reflect the official HAPI JPA Starter and Firely Server docs. Image tags,
ports, and trial terms can change — confirm against the vendor docs if something looks off.*
