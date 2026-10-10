#!/usr/bin/env node
// harvest.js <rpu> — the MiEspacio cycle, reimplemented from the proven
// newman-ubuntu recipes (docs/harvest-recipes.md): WAF-clear login, 2FA poll
// from the mailbox (IMAP, via the python mfa helper), enrollment with the
// exact razón social + total, then 6-download batches with a re-login between
// batches. Prints one JSON summary line on success.
//
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, TWOCAPTCHA_KEY, CFE_USER,
// CFE_PASS, MAIL_INTAKE_USER, MAIL_INTAKE_APP_PASSWORD, CHROME_PATH.
"use strict";

const puppeteer = require("puppeteer-core");
const { execFileSync } = require("child_process");
const path = require("path");
const os = require("os");

const RPU = process.argv[2];
if (!RPU || !/^\d{12}$/.test(RPU)) {
  console.error("usage: harvest.js <rpu-12-digits>");
  process.exit(2);
}

const CHROME = process.env.CHROME_PATH || "/usr/bin/google-chrome";
const PORTAL = "https://app.cfe.mx/Aplicaciones/CCFE/MiEspacio/MiEspacio.aspx";
const BATCH_SIZE = 6;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function solveCaptcha(page) {
  // 2captcha: the proven flow posts the captcha image and polls res.php.
  const imgSel = "img[src*='CaptchaImage'], img[src*='captcha']";
  await page.waitForSelector(imgSel, { timeout: 30000 });
  const src = await page.evaluate(
    (sel) => document.querySelector(sel)?.src || "", imgSel);
  let b64 = "";
  if (src.startsWith("data:")) {
    b64 = src.split(",")[1] || "";
  } else {
    const buf = await page.evaluate(async (s) => {
      const r = await fetch(s);
      const b = await r.blob();
      return new Promise((res) => {
        const fr = new FileReader();
        fr.onloadend = () => res(String(fr.result).split(",")[1] || "");
        fr.readAsDataURL(b);
      });
    }, src);
    b64 = buf;
  }
  const body = `key=${encodeURIComponent(process.env.TWOCAPTCHA_KEY || "")}`
    + `&method=base64&json=1&body=${encodeURIComponent(b64)}`;
  const inRes = await (await fetch(`http://2captcha.com/in.php?${body}`)).json();
  if (inRes.status !== 1) throw new Error("captcha: " + JSON.stringify(inRes));
  for (let i = 0; i < 60; i++) {
    await sleep(5000);
    const r = await (await fetch(
      `http://2captcha.com/res.php?key=${process.env.TWOCAPTCHA_KEY}`
      + `&action=get&json=1&id=${inRes.request}`)).json();
    if (r.status === 1) return r.request;
    if (r.request !== "CAPCHA_NOT_READY") throw new Error("captcha: " + r.request);
  }
  throw new Error("captcha timeout");
}

function mailboxCode() {
  // The 6-digit MFA code lands in the IMAP mailbox; the proven extraction
  // lives in the python helper (letter-spacing:8px block).
  const out = execFileSync("python3", [
    path.join(__dirname, "..", "tariff", "..", "mfa_code.py"),
  ], { encoding: "utf8", env: process.env });
  return out.trim();
}

async function loginCycle(page) {
  await page.goto(PORTAL, { waitUntil: "networkidle2", timeout: 90000 });
  await page.type("input[name*='user'], input[name*='Usuario']",
    process.env.CFE_USER || "");
  await page.type("input[type='password']", process.env.CFE_PASS || "");
  await page.type("input[name*='captcha'], input[id*='Captcha']",
    await solveCaptcha(page));
  await Promise.all([
    page.click("input[type='submit'], button[type='submit']"),
    page.waitForNavigation({ waitUntil: "networkidle2", timeout: 90000 }),
  ]);
  // 2FA code (CFE sends it per login; sessions sometimes stay warm).
  if (await page.$("input[name*='codigo'], input[name*='Código']")) {
    await page.type("input[name*='codigo'], input[name*='Código']",
      mailboxCode());
    await page.click("input[type='submit'], button[type='submit']");
    await sleep(3000);
  }
  const html = await page.content();
  if (!html.includes("Mi Espacio") && !html.includes("newmanenergy")) {
    throw new Error("session_expired");
  }
}

async function enroll(page, nombre, total) {
  // agr2.js recipe: the EXACT razón social + total (no decimals).
  await page.type("input[name*='Nombre'], input[id*='Nombre']", nombre);
  await page.type("input[name*='Adeudo'], input[id*='Adeudo']", total);
  await page.click("input[type='submit'], button[type='submit']");
  await sleep(4000);
  if ((await page.content()).includes("no coincide")) {
    throw new Error("enroll_mismatch");
  }
}

async function harvestBatch(page, offset) {
  const counts = { pdfs: 0, xmls: 0 };
  for (let i = 0; i < BATCH_SIZE; i++) {
    const row = offset + i + 2; // ctl00/ctl01 are the header rows
    const gridSel = `#MainContent_GVHistorial_ctl${String(row).padStart(2, "0")}`;
    const rowEl = await page.$(gridSel);
    if (!rowEl) break;
    // Each grid row: PDF + XML postbacks (DescargaPDF / DescargaXMLFactura).
    const [pdf] = await Promise.all([
      page.waitForEvent("download", { timeout: 30000 }).catch(() => null),
      rowEl.$eval("input[name*='DescargaPDF']", (b) => b.click()).catch(() => null),
    ]);
    if (pdf) {
      counts.pdfs += 1;
      const p = await pdf.path();
      require("fs").copyFileSync(p, `/app/data/${RPU}/${path.basename(p)}`);
    }
    await sleep(1500);
    const [xml] = await Promise.all([
      page.waitForEvent("download", { timeout: 30000 }).catch(() => null),
      rowEl.$eval("input[name*='DescargaXMLFactura']", (b) => b.click()).catch(() => null),
    ]);
    if (xml) {
      counts.xmls += 1;
      const p = await xml.path();
      require("fs").copyFileSync(p, `/app/data/${RPU}/${path.basename(p)}`);
    }
    await sleep(1500);
  }
  return counts;
}

async function egressIsMX() {
  const r = await fetch("https://ipinfo.io/json");
  const j = await r.json();
  if (j.country !== "MX") {
    throw new Error(`geo_blocked: egress is ${j.country}, need MX (NordVPN -> Mexico)`);
  }
}

async function main() {
  await egressIsMX();
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const page = await browser.newPage();
  await page.setUserAgent(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    + "(KHTML, like Gecko) Chrome/126.0 Safari/537.36");
  try {
    await loginCycle(page);
    // Enrollment needs the app's nombre+total; the phone leg (Contigo)
    // supplies them. Fallback: they are passed via env for the container run.
    const nombre = process.env.CF_ENROLL_NOMBRE || "";
    const total = process.env.CF_ENROLL_TOTAL || "";
    if (nombre && total) await enroll(page, nombre, total);
    require("fs").mkdirSync(`/app/data/${RPU}`, { recursive: true });
    let pdfs = 0, xmls = 0, offset = 0;
    for (let batch = 0; batch < 12; batch++) {
      const c = await harvestBatch(page, offset);
      pdfs += c.pdfs; xmls += c.xmls;
      if (c.pdfs === 0 && c.xmls === 0) break;
      offset += BATCH_SIZE;
      await loginCycle(page); // 6-download budget: re-login between batches
    }
    console.log(JSON.stringify({
      rpu: RPU, months: xmls, pdfs, xmls,
      state: xmls >= 12 ? "harvested" : "partial",
    }));
    await browser.close();
    return 0;
  } catch (err) {
    console.error(err.message || String(err));
    await browser.close();
    return 1;
  }
}

main().then((rc) => process.exit(rc));
