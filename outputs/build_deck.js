const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const FA = require("react-icons/fa");

// ---------- palette ----------
const C = {
  ink: "14171C", panel: "1E232B", steel: "2C333D", line: "3A434F",
  paper: "FFFFFF", cloud: "F2F4F7", cloud2: "E7EBF0",
  body: "3C4655", muted: "8A94A6", white: "FFFFFF",
  mh: "009BFF",       // MatterHackers blue (through-line + Build)
  thrifty: "27AE60",  // green = value
  build: "009BFF",    // blue = core
  pro: "C0392B",      // red = PRO / premium
  usa: "1F3A93",      // USA badge blue
  copper: "E07B2C",   // composite/engineering accent (now a sub-range of PRO)
  copperDeep: "B85F18",
};
const FONT_H = "Arial", FONT_B = "Arial";

const pres = new pptxgen();
pres.defineLayout({ name: "W", width: 13.333, height: 7.5 });
pres.layout = "W";
pres.author = "Brand Strategy";
pres.title = "First-Party Filament Brand Refresh v2";
const PW = 13.333, PH = 7.5;

async function icon(Comp, color, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color, size: String(size) }));
  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + png.toString("base64");
}
const shadow = () => ({ type: "outer", color: "000000", blur: 9, offset: 3, angle: 90, opacity: 0.22 });
function pageNum(s, n) { s.addText(String(n).padStart(2, "0"), { x: PW - 1.0, y: PH - 0.5, w: 0.7, h: 0.3, fontSize: 9, color: C.muted, align: "right", fontFace: FONT_B }); }
function kicker(s, t, c) { s.addText(t.toUpperCase(), { x: 0.7, y: 0.5, w: 11, h: 0.32, fontSize: 11.5, bold: true, color: c, charSpacing: 3, fontFace: FONT_H }); }
function titleLine(s, t, dark) { s.addText(t, { x: 0.68, y: 0.82, w: 12, h: 0.95, fontSize: 31, bold: true, color: dark ? C.white : C.ink, fontFace: FONT_H, lineSpacing: 33 }); }

(async () => {
const ICN = {
  flag: await icon(FA.FaFlagUsa, "#" + C.white),
  cube: await icon(FA.FaCube, "#" + C.white),
  tag: await icon(FA.FaTag, "#" + C.white),
  industry: await icon(FA.FaIndustry, "#" + C.white),
  warn: await icon(FA.FaExclamationTriangle, "#" + C.white),
  check: await icon(FA.FaCheck, "#" + C.white),
  checkC: await icon(FA.FaCheckCircle, "#" + C.thrifty),
  shield: await icon(FA.FaShieldAlt, "#" + C.white),
  hammer: await icon(FA.FaHammer, "#" + C.white),
  palette: await icon(FA.FaPalette, "#" + C.white),
  dollar: await icon(FA.FaDollarSign, "#" + C.white),
  layers: await icon(FA.FaLayerGroup, "#" + C.white),
  arrowR: await icon(FA.FaArrowRight, "#" + C.mh),
  globe: await icon(FA.FaGlobeAmericas, "#" + C.white),
  filter: await icon(FA.FaFilter, "#" + C.white),
  cut: await icon(FA.FaCut, "#" + C.white),
  certificate: await icon(FA.FaCertificate, "#" + C.white),
};

// USA badge mock (reusable) - clean stacked "MADE IN / USA" on a blue roundel
function usaBadge(s, x, y, d) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: C.white }, line: { color: C.usa, width: 2.25 } });
  s.addShape(pres.shapes.OVAL, { x: x + d * 0.1, y: y + d * 0.1, w: d * 0.8, h: d * 0.8, fill: { color: C.usa } });
  s.addText([
    { text: "MADE IN", options: { fontSize: d * 8.5, bold: true, color: C.white, breakLine: true, charSpacing: 0.5 } },
    { text: "USA", options: { fontSize: d * 20, bold: true, color: C.white } },
  ], { x, y, w: d, h: d, align: "center", valign: "middle", margin: 0, fontFace: FONT_H });
}

// =====================================================================
// SLIDE 1 — TITLE
// =====================================================================
let s = pres.addSlide();
s.background = { color: C.ink };
for (let i = 0; i < 7; i++) s.addShape(pres.shapes.RECTANGLE, { x: 9.0 + i * 0.62, y: 0, w: 0.06, h: PH, fill: { color: C.line, transparency: i % 2 ? 55 : 30 } });
s.addText("BRAND REFRESH  -  REVISED DIRECTION", { x: 0.8, y: 1.5, w: 9.5, h: 0.4, fontSize: 13, bold: true, color: C.mh, charSpacing: 4, fontFace: FONT_H });
s.addText("First-Party Filament:\nThree Lines, One System", { x: 0.75, y: 2.0, w: 9.8, h: 2.0, fontSize: 45, bold: true, color: C.white, fontFace: FONT_H, lineSpacing: 47 });
s.addText("Simplifying the names, folding the engineering composites into PRO, and making country-of-origin a clear, per-product label.", { x: 0.8, y: 4.2, w: 8.7, h: 0.9, fontSize: 15.5, color: C.cloud2, fontFace: FONT_B, lineSpacing: 22 });
const chips = [["MatterHackers ThriftyMake", C.thrifty], ["MatterHackers Build", C.build], ["MatterHackers PRO", C.pro]];
let cx = 0.8;
chips.forEach(([t, col]) => {
  const w = 0.34 + t.length * 0.108;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: cx, y: 5.5, w, h: 0.48, fill: { color: C.steel }, line: { color: col, width: 1.5 }, rectRadius: 0.24 });
  s.addText(t, { x: cx, y: 5.5, w, h: 0.48, fontSize: 11.5, bold: true, color: C.white, align: "center", valign: "middle", fontFace: FONT_B });
  cx += w + 0.24;
});
s.addText("Prepared for the first-party product line refresh  ·  June 2026", { x: 0.8, y: 6.7, w: 9, h: 0.3, fontSize: 10.5, color: C.muted, fontFace: FONT_B });

// =====================================================================
// SLIDE 2 — WHAT'S CHANGING (two decisions)
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "The revised direction", C.mh);
titleLine(s, "Two decisions reshape the portfolio");
s.addText("This refresh simplifies how the lines are named and gives the engineering composites a home - without launching a new brand.", { x: 0.7, y: 1.62, w: 12, h: 0.4, fontSize: 13.5, color: C.body, fontFace: FONT_B });

const dec = [
  { num: "01", ic: ICN.tag, col: C.mh, t: "Simplify the names", b: "Drop \"Series.\" Every line carries the full MatterHackers wordmark: MatterHackers ThriftyMake, MatterHackers Build, and MatterHackers PRO." },
  { num: "02", ic: ICN.layers, col: C.pro, t: "Fold engineering into PRO", b: "NylonX, NylonG and NylonK join PRO instead of a separate line. PRO becomes the premium-performance tier - and we label country-of-origin on each SKU." },
];
for (let i = 0; i < 2; i++) {
  const x = 0.7 + i * 6.25, w = 5.9, y = 2.45, h = 3.9;
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 }, shadow: shadow() });
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h: 0.13, fill: { color: dec[i].col } });
  s.addText(dec[i].num, { x: x + 0.35, y: y + 0.35, w: 1.6, h: 0.9, fontSize: 44, bold: true, color: dec[i].col, fontFace: FONT_H });
  s.addShape(pres.shapes.OVAL, { x: x + w - 1.35, y: y + 0.45, w: 0.9, h: 0.9, fill: { color: dec[i].col } });
  s.addImage({ data: dec[i].ic, x: x + w - 1.12, y: y + 0.68, w: 0.44, h: 0.44 });
  s.addText(dec[i].t, { x: x + 0.38, y: y + 1.5, w: w - 0.7, h: 0.6, fontSize: 22, bold: true, color: C.ink, fontFace: FONT_H });
  s.addText(dec[i].b, { x: x + 0.38, y: y + 2.2, w: w - 0.75, h: 1.5, fontSize: 13.5, color: C.body, fontFace: FONT_B, lineSpacing: 19, valign: "top" });
}
pageNum(s, 2);

// =====================================================================
// SLIDE 3 — WHY THIS SOLVES THE PROBLEM
// =====================================================================
s = pres.addSlide();
s.background = { color: C.ink };
kicker(s, "The problem it solves", C.copper);
titleLine(s, "PRO no longer has to mean \"Made in USA\"", true);
s.addText("The old tension: PRO was defined by domestic manufacturing, so premium imported composites had nowhere to live. Broadening PRO to mean performance - and labeling origin per SKU - dissolves it.", { x: 0.7, y: 1.66, w: 12, h: 0.7, fontSize: 14, color: C.cloud2, fontFace: FONT_B, lineSpacing: 19 });

const probs = [
  { ic: ICN.warn, t: "Before: composites were orphaned", b: "NylonX/G/K are premium but imported - they broke PRO's American-made promise and sat on PRO URLs with no real home." },
  { ic: ICN.layers, t: "Now: PRO = performance", b: "PRO is the professional-grade tier. The composites belong there on merit, alongside the US-made precision staples." },
  { ic: ICN.shield, t: "The USA story is kept - as a badge", b: "\"Made in USA\" moves from the brand's gate to a visible, filterable per-SKU label. Still celebrated, no longer exclusionary." },
];
const pw = 3.78, pgap = 0.34, px0 = 0.7, py = 2.65, phh = 3.45;
for (let i = 0; i < 3; i++) {
  const x = px0 + i * (pw + pgap);
  s.addShape(pres.shapes.RECTANGLE, { x, y: py, w: pw, h: phh, fill: { color: C.steel }, line: { color: C.line, width: 1 }, shadow: shadow() });
  s.addShape(pres.shapes.OVAL, { x: x + 0.34, y: py + 0.36, w: 0.86, h: 0.86, fill: { color: C.ink }, line: { color: i === 2 ? C.mh : C.copper, width: 1.5 } });
  s.addImage({ data: probs[i].ic, x: x + 0.55, y: py + 0.57, w: 0.44, h: 0.44 });
  s.addText(probs[i].t, { x: x + 0.34, y: py + 1.4, w: pw - 0.62, h: 0.74, fontSize: 15.5, bold: true, color: C.white, fontFace: FONT_H, lineSpacing: 18, valign: "top" });
  s.addText(probs[i].b, { x: x + 0.34, y: py + 2.12, w: pw - 0.66, h: 1.2, fontSize: 12, color: C.cloud2, fontFace: FONT_B, lineSpacing: 16, valign: "top" });
}
pageNum(s, 3);

// =====================================================================
// SLIDE 4 — DECISION 1: NAMING (before/after)
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "Decision 1 - naming", C.mh);
titleLine(s, "Cleaner names, one consistent wordmark");
s.addText("Drop \"Series\" and lead with the full MatterHackers name. Shorter, more modern, and consistent across the shelf and the site.", { x: 0.7, y: 1.62, w: 12, h: 0.4, fontSize: 13, color: C.body, fontFace: FONT_B });

const renames = [
  { col: C.thrifty, from: "ThriftyMake", to: "MatterHackers ThriftyMake", note: "Distinct sub-brand name kept; just prefixed with MatterHackers for consistency." },
  { col: C.build, from: "MH Build Series", to: "MatterHackers Build", note: "\"MH\" expands to the full wordmark; \"Series\" dropped. Protects the #1-seller equity." },
  { col: C.pro, from: "PRO Series", to: "MatterHackers PRO", note: "\"Series\" dropped; PRO stands on its own as the premium-performance tier." },
];
let ry = 2.4;
renames.forEach((r) => {
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: ry, w: 12.0, h: 1.22, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 } });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: ry, w: 0.13, h: 1.22, fill: { color: r.col } });
  s.addText(r.from, { x: 1.0, y: ry, w: 3.0, h: 1.22, fontSize: 14, color: C.muted, strike: true, valign: "middle", fontFace: FONT_B });
  s.addImage({ data: ICN.arrowR, x: 4.15, y: ry + 0.45, w: 0.42, h: 0.42 });
  s.addText(r.to, { x: 4.8, y: ry, w: 4.1, h: 1.22, fontSize: 17, bold: true, color: r.col, valign: "middle", fontFace: FONT_H, lineSpacing: 19 });
  s.addText(r.note, { x: 9.05, y: ry + 0.14, w: 3.5, h: 0.95, fontSize: 11, color: C.body, fontFace: FONT_B, valign: "middle", lineSpacing: 14.5 });
  ry += 1.36;
});
s.addText("Design note: \"MatterHackers Build\" is longer than \"MH Build\" - the label system (slide 11) keeps the wordmark compact and the tier name dominant.", { x: 0.7, y: ry + 0.0, w: 12, h: 0.5, fontSize: 11, italic: true, color: C.muted, fontFace: FONT_B, lineSpacing: 14 });
pageNum(s, 4);

// =====================================================================
// SLIDE 5 — NEW 3-PILLAR ARCHITECTURE
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "The architecture", C.mh);
titleLine(s, "Three lines, one ladder");
s.addText("Value, core, and premium - with the engineering composites living inside PRO rather than in a fourth brand.", { x: 0.7, y: 1.62, w: 12, h: 0.4, fontSize: 13, color: C.body, fontFace: FONT_B });

const ladder = [
  { n: "ThriftyMake", t: "VALUE", col: C.thrifty, ic: ICN.dollar, promise: "Lowest cost per spool", h: 3.15 },
  { n: "Build", t: "CORE", col: C.build, ic: ICN.cube, promise: "Best all-round value and the widest range", h: 3.8 },
  { n: "PRO", t: "PREMIUM - PROFESSIONAL", col: C.pro, ic: ICN.shield, promise: "Top performance and tightest tolerances, now including the engineering composites - origin labeled per SKU", h: 4.4 },
];
const lw = 3.92, lgap = 0.34, lx0 = 0.7, baseY = 6.85;
for (let i = 0; i < 3; i++) {
  const x = lx0 + i * (lw + lgap);
  const y = baseY - ladder[i].h;
  s.addShape(pres.shapes.RECTANGLE, { x, y, w: lw, h: ladder[i].h, fill: { color: ladder[i].col }, shadow: shadow() });
  s.addShape(pres.shapes.OVAL, { x: x + 0.32, y: y + 0.3, w: 0.72, h: 0.72, fill: { color: C.white, transparency: 84 } });
  s.addImage({ data: ladder[i].ic, x: x + 0.5, y: y + 0.48, w: 0.37, h: 0.37 });
  s.addText(ladder[i].t, { x: x + 0.34, y: y + 1.16, w: lw - 1.1, h: 0.3, fontSize: 10, bold: true, color: C.white, charSpacing: 1.5, fontFace: FONT_B });
  s.addText([
    { text: "MatterHackers", options: { fontSize: 11, bold: true, color: C.white, breakLine: true, charSpacing: 0.5 } },
    { text: ladder[i].n, options: { fontSize: 26, bold: true, color: C.white } },
  ], { x: x + 0.34, y: y + 1.46, w: lw - 0.6, h: 0.95, fontFace: FONT_H, valign: "top", margin: 0 });
  s.addText(ladder[i].promise, { x: x + 0.34, y: y + 2.52, w: lw - 0.66, h: 1.55, fontSize: 13, bold: true, color: C.white, fontFace: FONT_B, lineSpacing: 17, valign: "top" });
}
// USA badge tucked top-right of the PRO bar, clear of text
usaBadge(s, lx0 + 2 * (lw + lgap) + lw - 1.02, baseY - ladder[2].h + 0.26, 0.74);
s.addText("Rising height = rising performance & price", { x: 0.7, y: 6.98, w: 8, h: 0.3, fontSize: 10.5, italic: true, color: C.muted, fontFace: FONT_B });
pageNum(s, 5);

// =====================================================================
// SLIDE 6 — REDEFINING PRO (strategic heart)
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "The strategic shift", C.pro);
titleLine(s, "Redefining PRO: performance first, origin labeled");
s.addText("The definition of PRO changes - from a manufacturing-origin claim to a performance standard. This is what makes folding in the composites work.", { x: 0.7, y: 1.62, w: 12, h: 0.5, fontSize: 13, color: C.body, fontFace: FONT_B, lineSpacing: 17 });

// was
s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.5, w: 5.8, h: 3.05, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 } });
s.addText("WAS", { x: 1.0, y: 2.72, w: 3, h: 0.3, fontSize: 12, bold: true, color: C.muted, charSpacing: 2, fontFace: FONT_B });
s.addText("\"American-made precision\"", { x: 1.0, y: 3.02, w: 5.2, h: 0.5, fontSize: 18, bold: true, color: C.ink, fontFace: FONT_H });
[["Origin was the boundary of the line", C.muted],
 ["Tolerance + Made-in-USA defined PRO", C.muted],
 ["Premium imports were locked out", C.pro]].forEach(([t, col], i) => {
  s.addShape(pres.shapes.OVAL, { x: 1.0, y: 3.74 + i * 0.5, w: 0.14, h: 0.14, fill: { color: col } });
  s.addText(t, { x: 1.28, y: 3.62 + i * 0.5, w: 5.0, h: 0.4, fontSize: 12.5, color: C.body, valign: "middle", fontFace: FONT_B });
});

// now
s.addShape(pres.shapes.RECTANGLE, { x: 6.85, y: 2.5, w: 5.8, h: 3.05, fill: { color: C.ink }, shadow: shadow() });
s.addShape(pres.shapes.RECTANGLE, { x: 6.85, y: 2.5, w: 5.8, h: 0.13, fill: { color: C.pro } });
s.addText("NOW", { x: 7.15, y: 2.74, w: 3, h: 0.3, fontSize: 12, bold: true, color: C.copper, charSpacing: 2, fontFace: FONT_B });
s.addText("\"Professional-grade performance\"", { x: 7.15, y: 3.04, w: 5.2, h: 0.5, fontSize: 18, bold: true, color: C.white, fontFace: FONT_H });
[["Tightest tolerances & engineered formulations", ICN.checkC],
 ["Spans US-made staples + global composites", ICN.checkC],
 ["Country-of-origin shown on every SKU", ICN.checkC]].forEach(([t, ic], i) => {
  s.addImage({ data: ic, x: 7.15, y: 3.72 + i * 0.5, w: 0.26, h: 0.26 });
  s.addText(t, { x: 7.55, y: 3.62 + i * 0.5, w: 4.9, h: 0.4, fontSize: 12.5, color: C.cloud2, valign: "middle", fontFace: FONT_B });
});

s.addText([
  { text: "What we protect:  ", options: { bold: true, color: C.pro } },
  { text: "the American-made story stays front-and-center as a badge buyers can see and filter - a real asset for government, defense and domestic-sourcing customers - without capping what PRO can include.", options: { color: C.body } },
], { x: 0.7, y: 5.85, w: 12, h: 0.9, fontSize: 13, fontFace: FONT_B, lineSpacing: 18 });
pageNum(s, 6);

// =====================================================================
// SLIDE 7 — ORIGIN TRANSPARENCY SYSTEM
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "Making origin transparent", C.usa.substring(0,6));
titleLine(s, "Every PRO SKU says where it's made");
s.addText("If origin is no longer the brand boundary, it has to be unmistakable at the product level. One badge, one filter, applied everywhere.", { x: 0.7, y: 1.62, w: 12, h: 0.5, fontSize: 13, color: C.body, fontFace: FONT_B, lineSpacing: 17 });

// left: badge + imported tag mock
s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.55, w: 4.0, h: 4.3, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 } });
s.addText("THE LABELS", { x: 0.95, y: 2.75, w: 3.5, h: 0.3, fontSize: 11, bold: true, color: C.muted, charSpacing: 2, fontFace: FONT_B });
usaBadge(s, 1.05, 3.2, 1.35);
s.addText("US-made SKUs", { x: 2.65, y: 3.45, w: 1.9, h: 0.4, fontSize: 13, bold: true, color: C.ink, fontFace: FONT_H });
s.addText("e.g. PRO PLA, ABS, ASA, Nylon, PPS", { x: 2.65, y: 3.82, w: 1.95, h: 0.6, fontSize: 10.5, color: C.body, fontFace: FONT_B, lineSpacing: 13 });
// imported pill
s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 1.05, y: 5.05, w: 1.35, h: 1.35, fill: { color: C.steel }, rectRadius: 0.12 });
s.addImage({ data: ICN.globe, x: 1.42, y: 5.28, w: 0.6, h: 0.6 });
s.addText("GLOBALLY\nSOURCED", { x: 1.05, y: 5.95, w: 1.35, h: 0.4, fontSize: 9, bold: true, color: C.white, align: "center", fontFace: FONT_B, lineSpacing: 10 });
s.addText("Imported SKUs", { x: 2.65, y: 5.3, w: 1.9, h: 0.4, fontSize: 13, bold: true, color: C.ink, fontFace: FONT_H });
s.addText("e.g. NylonX, NylonG, NylonK - country shown where known", { x: 2.65, y: 5.67, w: 1.95, h: 0.8, fontSize: 10.5, color: C.body, fontFace: FONT_B, lineSpacing: 13 });

// right: where it shows up
s.addText("Where it shows up", { x: 5.1, y: 2.55, w: 7, h: 0.4, fontSize: 16, bold: true, color: C.ink, fontFace: FONT_H });
const places = [
  [ICN.tag, "On the product page & spool label", "A clear origin line on every PRO listing and physical label - no digging required."],
  [ICN.filter, "As a store filter", "A site-wide \"Made in USA\" toggle so buyers who require domestic sourcing can shop it in one click."],
  [ICN.flag, "As a curated collection", "Feed a \"Made in USA\" collection into the government / defense storefront where it matters most."],
  [ICN.shield, "Backed by honesty", "Never hide that a composite is imported. Transparency protects the trust PRO has built."],
];
let qy = 3.15;
places.forEach(([ic, h, b]) => {
  s.addShape(pres.shapes.OVAL, { x: 5.1, y: qy, w: 0.6, h: 0.6, fill: { color: C.usa } });
  s.addImage({ data: ic, x: 5.27, y: qy + 0.17, w: 0.26, h: 0.26 });
  s.addText(h, { x: 5.86, y: qy - 0.02, w: 6.7, h: 0.34, fontSize: 14, bold: true, color: C.ink, fontFace: FONT_H });
  s.addText(b, { x: 5.86, y: qy + 0.32, w: 6.75, h: 0.55, fontSize: 11.5, color: C.body, fontFace: FONT_B, lineSpacing: 15, valign: "top" });
  qy += 0.92;
});
pageNum(s, 7);

// =====================================================================
// SLIDE 8 — INSIDE PRO (structure)
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "Inside PRO", C.pro);
titleLine(s, "One PRO banner, two material families");
s.addText("Both share PRO's tolerances, QA and premium price band. An optional \"Composite\" descriptor aids navigation - no separate brand needed.", { x: 0.7, y: 1.62, w: 12, h: 0.5, fontSize: 13, color: C.body, fontFace: FONT_B, lineSpacing: 17 });

// PRO precision
s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.6, w: 5.85, h: 4.1, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 }, shadow: shadow() });
s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.6, w: 5.85, h: 0.13, fill: { color: C.pro } });
s.addText("PRO  ·  PRECISION STAPLES", { x: 1.0, y: 2.92, w: 5.3, h: 0.35, fontSize: 13, bold: true, color: C.pro, charSpacing: 1, fontFace: FONT_H });
usaBadge(s, 5.4, 2.85, 0.95);
s.addText("Domestic, functional workhorses", { x: 1.0, y: 3.32, w: 4.2, h: 0.4, fontSize: 13.5, bold: true, color: C.ink, fontFace: FONT_H });
["PLA  ·  Tough PLA  ·  ABS  ·  ASA", "PETG  ·  Nylon  ·  PPS  ·  PVA support", "+/-0.02mm tolerance, color-consistent", "Made in USA badge applies"].forEach((t, i) => {
  s.addShape(pres.shapes.OVAL, { x: 1.0, y: 3.95 + i * 0.58, w: 0.14, h: 0.14, fill: { color: C.pro } });
  s.addText(t, { x: 1.28, y: 3.82 + i * 0.58, w: 5.0, h: 0.45, fontSize: 12.5, color: C.body, valign: "middle", fontFace: FONT_B });
});

// PRO composite
s.addShape(pres.shapes.RECTANGLE, { x: 6.8, y: 2.6, w: 5.85, h: 4.1, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 }, shadow: shadow() });
s.addShape(pres.shapes.RECTANGLE, { x: 6.8, y: 2.6, w: 5.85, h: 0.13, fill: { color: C.copper } });
s.addText("PRO  ·  COMPOSITE  (ENGINEERING)", { x: 7.1, y: 2.92, w: 5.3, h: 0.35, fontSize: 13, bold: true, color: C.copperDeep, charSpacing: 1, fontFace: FONT_H });
s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 11.45, y: 2.85, w: 0.95, h: 0.5, fill: { color: C.steel }, rectRadius: 0.1 });
s.addText("IMPORTED", { x: 11.45, y: 2.85, w: 0.95, h: 0.5, fontSize: 8, bold: true, color: C.white, align: "center", valign: "middle", fontFace: FONT_B });
s.addText("Fiber-reinforced, metal-replacing", { x: 7.1, y: 3.32, w: 4.2, h: 0.4, fontSize: 13.5, bold: true, color: C.ink, fontFace: FONT_H });
[["NylonX", "carbon-fiber flagship"], ["NylonG", "glass-fiber stiffness"], ["NylonK", "Kevlar / aramid toughness"], ["Origin", "labeled per SKU; not US-made"]].forEach(([a, b2], i) => {
  s.addShape(pres.shapes.OVAL, { x: 7.1, y: 3.95 + i * 0.58, w: 0.14, h: 0.14, fill: { color: C.copper } });
  s.addText([{ text: a + "  ", options: { bold: true, color: C.ink } }, { text: "- " + b2, options: { color: C.body } }], { x: 7.38, y: 3.82 + i * 0.58, w: 5.0, h: 0.45, fontSize: 12.5, valign: "middle", fontFace: FONT_B });
});
pageNum(s, 8);

// =====================================================================
// SLIDE 9 — POSITIONING & MESSAGING MATRIX
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "Positioning system", C.mh);
titleLine(s, "One promise, one line, one tagline");
s.addText("How the simplified three-line family talks - at a glance.", { x: 0.7, y: 1.62, w: 12, h: 0.35, fontSize: 13, color: C.body, fontFace: FONT_B });

const head = (t) => ({ text: t, options: { bold: true, color: C.white, fill: { color: C.ink }, align: "left", valign: "middle", fontFace: FONT_B, fontSize: 11.5 } });
const cell = (t, o = {}) => ({ text: t, options: Object.assign({ color: C.body, valign: "middle", fontFace: FONT_B, fontSize: 11.5, align: "left" }, o) });
const mrows = [
  [head("Line"), head("Tier"), head("Positioning"), head("Tagline"), head("Proof point")],
  [{ text: "MatterHackers\nThriftyMake", options: { bold: true, color: C.thrifty, valign: "middle", fontFace: FONT_B, fontSize: 12 } },
   cell("Value"), cell("Reliable quality at the lowest catalog price"), cell("\"Quality that costs less.\"", { italic: true, bold: true, color: C.ink }), cell("Lowest price in the catalog")],
  [{ text: "MatterHackers\nBuild", options: { bold: true, color: C.build, valign: "middle", fontFace: FONT_B, fontSize: 12 } },
   cell("Core"), cell("Consistent color & performance, batch to batch"), cell("\"Consistency you can count on.\"", { italic: true, bold: true, color: C.ink }), cell("#1-selling filament at MatterHackers")],
  [{ text: "MatterHackers\nPRO", options: { bold: true, color: C.pro, valign: "middle", fontFace: FONT_B, fontSize: 12 } },
   cell("Premium / professional"), cell("Top performance for functional & end-use parts"), cell("\"Performance when it counts.\"", { italic: true, bold: true, color: C.ink }), cell("+/-0.02mm; composites; origin labeled")],
];
s.addTable(mrows, {
  x: 0.7, y: 2.2, w: 12.0, colW: [2.3, 1.9, 3.5, 2.6, 1.7], rowH: [0.5, 1.05, 1.05, 1.05],
  border: { pt: 0.5, color: "D7DDE5" }, fill: { color: C.white }, valign: "middle",
});
s.addText([
  { text: "The taglines ladder cleanly - ", options: { italic: true, color: C.muted } },
  { text: "Quality, Consistency, Performance", options: { italic: true, bold: true, color: C.body } },
  { text: " - so the two budget lines never both claim \"affordable,\" and PRO stays origin-neutral.", options: { italic: true, color: C.muted } },
], { x: 0.7, y: 6.25, w: 12, h: 0.4, fontSize: 11, fontFace: FONT_B });
pageNum(s, 9);

// =====================================================================
// SLIDE 10 — VISUAL IDENTITY
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "Visual identity direction", C.mh);
titleLine(s, "Three tier colors + a Made-in-USA badge");
s.addText("One label template across all three lines; color signals the tier; the USA badge overlays on qualifying SKUs.", { x: 0.7, y: 1.62, w: 12, h: 0.5, fontSize: 13, color: C.body, fontFace: FONT_B, lineSpacing: 17 });

const swatches = [
  { n: "ThriftyMake", col: C.thrifty, hex: "27AE60", mat: "PLA+ - ABS - PETG", badge: false },
  { n: "Build", col: C.build, hex: "009BFF", mat: "PLA - ABS - PETG - TPU", badge: false },
  { n: "PRO", col: C.pro, hex: "C0392B", mat: "Precision staples + composites", badge: true },
];
const sw = 3.92, sgap = 0.34, sx0 = 0.7, sy = 2.55;
for (let i = 0; i < 3; i++) {
  const x = sx0 + i * (sw + sgap);
  s.addShape(pres.shapes.RECTANGLE, { x, y: sy, w: sw, h: 2.15, fill: { color: swatches[i].col }, shadow: shadow() });
  s.addShape(pres.shapes.RECTANGLE, { x: x + 0.4, y: sy + 0.38, w: sw - 0.8, h: 1.25, fill: { color: C.white } });
  s.addShape(pres.shapes.RECTANGLE, { x: x + 0.4, y: sy + 0.38, w: sw - 0.8, h: 0.2, fill: { color: swatches[i].col } });
  s.addText("MatterHackers", { x: x + 0.58, y: sy + 0.66, w: sw - 1.4, h: 0.26, fontSize: 9, bold: true, color: C.muted, charSpacing: 1, fontFace: FONT_B });
  s.addText(swatches[i].n, { x: x + 0.58, y: sy + 0.88, w: sw - 1.4, h: 0.4, fontSize: 16, bold: true, color: C.ink, fontFace: FONT_H });
  s.addText(swatches[i].mat, { x: x + 0.58, y: sy + 1.3, w: sw - 1.0, h: 0.3, fontSize: 8.5, color: C.muted, fontFace: FONT_B });
  if (swatches[i].badge) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + sw - 1.6, y: sy + 0.62, w: 1.05, h: 0.34, fill: { color: C.usa }, rectRadius: 0.06 });
    s.addText("USA badge", { x: x + sw - 1.6, y: sy + 0.62, w: 1.05, h: 0.34, fontSize: 9, bold: true, color: C.white, align: "center", valign: "middle", fontFace: FONT_B });
  }
  s.addText("#" + swatches[i].hex, { x, y: sy + 1.78, w: sw, h: 0.28, fontSize: 10, bold: true, color: C.white, align: "center", fontFace: FONT_B });
}
const princ = [
  [ICN.palette, "Color = tier", "Green / blue / red read the value-core-premium ladder instantly, in store and online."],
  [ICN.certificate, "Badge = origin", "The Made-in-USA stamp overlays only on qualifying SKUs - a visible mark of pride and a filter."],
  [ICN.tag, "One wordmark lockup", "\"MatterHackers\" sits small above a dominant tier name - keeps the longer names tidy."],
];
const pcw = 3.92, pcg = 0.34, pcx = 0.7, pcy = 5.1;
for (let i = 0; i < 3; i++) {
  const x = pcx + i * (pcw + pcg);
  s.addShape(pres.shapes.RECTANGLE, { x, y: pcy, w: pcw, h: 1.8, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 } });
  if (i === 1) {
    usaBadge(s, x + 0.26, pcy + 0.24, 0.66);
  } else {
    s.addShape(pres.shapes.OVAL, { x: x + 0.28, y: pcy + 0.28, w: 0.6, h: 0.6, fill: { color: C.mh } });
    s.addImage({ data: princ[i][0], x: x + 0.42, y: pcy + 0.42, w: 0.32, h: 0.32 });
  }
  s.addText(princ[i][1], { x: x + 1.04, y: pcy + 0.3, w: pcw - 1.2, h: 0.55, fontSize: 13.5, bold: true, color: C.ink, fontFace: FONT_H, valign: "middle" });
  s.addText(princ[i][2], { x: x + 0.3, y: pcy + 0.96, w: pcw - 0.6, h: 0.8, fontSize: 11, color: C.body, fontFace: FONT_B, lineSpacing: 14.5, valign: "top" });
}
pageNum(s, 10);

// =====================================================================
// SLIDE 11 — ROLLOUT, RISKS & NEXT STEPS
// =====================================================================
s = pres.addSlide();
s.background = { color: C.paper };
kicker(s, "Execution", C.mh);
titleLine(s, "Rollout, risks & next steps");

// rollout strip
s.addText("Phased rollout", { x: 0.7, y: 1.66, w: 6, h: 0.35, fontSize: 15, bold: true, color: C.ink, fontFace: FONT_H });
const phases = [
  ["01", "Rename", "Roll out MatterHackers ThriftyMake / Build / PRO across site & labels."],
  ["02", "Re-home composites", "Move NylonX/G/K into PRO; 301-redirect old URLs to preserve SEO."],
  ["03", "Ship origin labels", "Add the Made-in-USA badge, per-SKU origin line, and store filter."],
  ["04", "Promote", "Relaunch PRO around performance; feature the USA collection for gov/defense."],
];
const fw = 2.92, fg = 0.28, fx0 = 0.7, fy = 2.1, fh = 1.95;
for (let i = 0; i < 4; i++) {
  const x = fx0 + i * (fw + fg);
  s.addShape(pres.shapes.RECTANGLE, { x, y: fy, w: fw, h: fh, fill: { color: C.ink }, shadow: shadow() });
  s.addShape(pres.shapes.RECTANGLE, { x, y: fy, w: fw, h: 0.11, fill: { color: C.mh } });
  s.addText(phases[i][0], { x: x + 0.26, y: fy + 0.2, w: 1.2, h: 0.55, fontSize: 26, bold: true, color: C.mh, fontFace: FONT_H });
  s.addText(phases[i][1], { x: x + 1.0, y: fy + 0.26, w: fw - 1.1, h: 0.45, fontSize: 14, bold: true, color: C.white, fontFace: FONT_H, valign: "middle" });
  s.addText(phases[i][2], { x: x + 0.28, y: fy + 0.86, w: fw - 0.5, h: 0.95, fontSize: 10.5, color: C.cloud2, fontFace: FONT_B, lineSpacing: 14, valign: "top" });
}

// risks
s.addText("Risks & mitigations", { x: 0.7, y: 4.35, w: 6, h: 0.35, fontSize: 15, bold: true, color: C.ink, fontFace: FONT_H });
const risks = [
  ["Diluting PRO's USA equity", "Lead PRO with performance, but keep the badge prominent and the USA collection curated."],
  ["SEO loss on moved/renamed pages", "301-redirect every old URL; keep product names (NylonX/G/K) verbatim in titles."],
  ["Origin data gaps", "Default imported SKUs to \"Globally sourced\"; add country as supplier data allows."],
];
const rw = 3.92, rg = 0.34, rx0 = 0.7, ryy = 4.8, rh = 1.65;
for (let i = 0; i < 3; i++) {
  const x = rx0 + i * (rw + rg);
  s.addShape(pres.shapes.RECTANGLE, { x, y: ryy, w: rw, h: rh, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 } });
  s.addShape(pres.shapes.RECTANGLE, { x, y: ryy, w: 0.1, h: rh, fill: { color: C.pro } });
  s.addText(risks[i][0], { x: x + 0.28, y: ryy + 0.2, w: rw - 0.5, h: 0.55, fontSize: 12.5, bold: true, color: C.ink, fontFace: FONT_H, lineSpacing: 15 });
  s.addText(risks[i][1], { x: x + 0.28, y: ryy + 0.78, w: rw - 0.5, h: 0.8, fontSize: 11, color: C.body, fontFace: FONT_B, lineSpacing: 14.5, valign: "top" });
}
s.addText([
  { text: "Next: ", options: { bold: true, color: C.mh } },
  { text: "approve the three names, sign off PRO's new definition, and brief design on the label + badge system.", options: { color: C.body } },
], { x: 0.7, y: 6.7, w: 12, h: 0.4, fontSize: 12.5, fontFace: FONT_B });
pageNum(s, 11);

await pres.writeFile({ fileName: "Filament_Brand_Refresh_Proposal.pptx" });
console.log("WROTE deck");
})();
