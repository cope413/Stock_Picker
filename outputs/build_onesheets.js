const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const FA = require("react-icons/fa");

const C = {
  ink: "14171C", steel: "2C333D", line: "3A434F",
  paper: "FFFFFF", cloud: "F2F4F7", cloud2: "E7EBF0",
  body: "3C4655", muted: "8A94A6", white: "FFFFFF",
  mh: "009BFF", thrifty: "27AE60", build: "009BFF", pro: "C0392B",
  usa: "1F3A93", copper: "E07B2C",
};
const FONT_H = "Arial", FONT_B = "Arial";

async function icon(Comp, color, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color, size: String(size) }));
  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + png.toString("base64");
}
const shadow = () => ({ type: "outer", color: "000000", blur: 7, offset: 2, angle: 90, opacity: 0.18 });

(async () => {
const W = await icon(FA.FaDollarSign, "#" + C.white);
const ICN = {
  dollar: await icon(FA.FaDollarSign, "#" + C.white),
  check: await icon(FA.FaCheckCircle, "#" + C.white),
  industry: await icon(FA.FaIndustry, "#" + C.white),
  sync: await icon(FA.FaSyncAlt, "#" + C.white),
  palette: await icon(FA.FaPalette, "#" + C.white),
  award: await icon(FA.FaAward, "#" + C.white),
  bullseye: await icon(FA.FaBullseye, "#" + C.white),
  hammer: await icon(FA.FaHammer, "#" + C.white),
  shield: await icon(FA.FaShieldAlt, "#" + C.white),
};

function usaBadge(pres, s, x, y, d) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: C.white }, line: { color: C.usa, width: 2.25 } });
  s.addShape(pres.shapes.OVAL, { x: x + d * 0.1, y: y + d * 0.1, w: d * 0.8, h: d * 0.8, fill: { color: C.usa } });
  s.addText([
    { text: "MADE IN", options: { fontSize: d * 8.5, bold: true, color: C.white, breakLine: true, charSpacing: 0.5 } },
    { text: "USA", options: { fontSize: d * 20, bold: true, color: C.white } },
  ], { x, y, w: d, h: d, align: "center", valign: "middle", margin: 0, fontFace: FONT_H });
}

const SHEETS = [
  {
    file: "MatterHackers_ThriftyMake_OneSheet.pptx",
    name: "ThriftyMake", tier: "VALUE LINE", col: C.thrifty, price: "$",
    tagline: "Quality that costs less.",
    promise: "The most affordable filament in our catalog.",
    desc: "MatterHackers ThriftyMake is the most affordable filament in our catalog - reliable, easy-to-print PLA+, ABS, and PETG at the lowest price per spool. It's made for makers who print constantly: prototypes, practice runs, jigs, and high-volume jobs where every dollar counts. You get dependable results and clean prints without paying for premium features you don't need.",
    highlights: [
      { ic: ICN.dollar, c: C.thrifty, t: "Lowest price per spool", b: "The best cost-per-kilogram in the MatterHackers catalog." },
      { ic: ICN.check, c: C.thrifty, t: "Reliable & easy to print", b: "Dependable results and clean prints, with no fuss." },
      { ic: ICN.industry, c: C.thrifty, t: "Built for volume", b: "Made for constant printing - prototypes, jigs, practice runs." },
    ],
    materials: ["PLA+", "ABS", "PETG"],
    proofBig: "Lowest price", proofSub: "in the entire MatterHackers catalog",
    bestFor: ["Hobbyists & classrooms", "High-volume printing", "Iterative prototyping", "Everyday practice prints"],
    meta: "MatterHackers ThriftyMake is our most affordable 3D printer filament - reliable PLA+, ABS, and PETG at the lowest price per spool. Quality that costs less.",
  },
  {
    file: "MatterHackers_Build_OneSheet.pptx",
    name: "Build", tier: "CORE LINE", col: C.build, price: "$$",
    tagline: "Consistency you can count on.",
    promise: "Our best-selling, do-everything filament.",
    desc: "MatterHackers Build is our best-selling filament - the everyday workhorse trusted by more makers than any other line we carry. What sets it apart is consistency: tight color matching and dependable performance from one spool to the next, so the print that worked yesterday works again today. Available in the widest range of materials and colors, Build delivers professional-looking results at a price that still fits the bench.",
    highlights: [
      { ic: ICN.sync, c: C.build, t: "Consistent, spool after spool", b: "Tight color matching and dependable performance, batch after batch." },
      { ic: ICN.palette, c: C.build, t: "The widest range", b: "PLA, ABS, PETG, TPU, ASA, Nylon and more - in every color." },
      { ic: ICN.award, c: C.build, t: "Pro looks, bench price", b: "Professional-looking results that still fit the budget." },
    ],
    materials: ["PLA", "ABS", "PETG", "TPU", "ASA", "Nylon", "PVA"],
    proofBig: "#1-selling", proofSub: "filament at MatterHackers",
    bestFor: ["Everyday printing", "Personal projects", "Production-ready prototypes", "Multi-material builds"],
    meta: "MatterHackers Build is our #1-selling 3D printer filament: consistent color and performance spool after spool, in the widest range of materials - at a price that fits the bench.",
  },
  {
    file: "MatterHackers_PRO_OneSheet.pptx",
    name: "PRO", tier: "PREMIUM - PROFESSIONAL LINE", col: C.pro, price: "$$$", badge: true,
    tagline: "Performance when it counts.",
    promise: "Professional-grade filament, engineered to perform.",
    desc: "MatterHackers PRO is our professional-grade line, engineered for work that has to perform. Every PRO filament is held to a tight +/-0.02mm diameter tolerance and rigorously tested for consistent, repeatable results - ideal for functional prototypes, manufacturing aids, and end-use parts. PRO spans precision staples alongside engineering-grade composites strong enough to replace machined metal, with country of origin clearly labeled on every SKU.",
    highlights: [
      { ic: ICN.bullseye, c: C.pro, t: "Tight +/-0.02mm tolerance", b: "Rigorously tested for consistent, repeatable results." },
      { ic: ICN.hammer, c: C.copper, t: "Engineering composites", b: "NylonX, NylonG and NylonK for metal-replacing strength." },
      { ic: ICN.shield, c: C.usa, t: "Transparent origin", b: "Every SKU labeled; Made in USA badge on qualifying materials." },
    ],
    materials: ["PLA", "Tough PLA", "ABS", "ASA", "PETG", "Nylon", "PPS", "NylonX", "NylonG", "NylonK"],
    proofBig: "+/-0.02mm", proofSub: "diameter tolerance, rigorously tested",
    bestFor: ["Functional prototypes", "Manufacturing aids", "End-use parts", "Metal-replacement parts"],
    meta: "MatterHackers PRO is our professional-grade 3D printer filament: +/-0.02mm tolerance, engineering composites (NylonX/G/K), and clear origin labeling. Performance when it counts.",
  },
];

for (const cfg of SHEETS) {
  const pres = new pptxgen();
  pres.defineLayout({ name: "OS", width: 8.5, height: 11 });
  pres.layout = "OS";
  const s = pres.addSlide();
  s.background = { color: C.paper };

  // ---- header band ----
  const HH = 2.7;
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 8.5, h: HH, fill: { color: cfg.col } });
  // subtle filament lines motif
  for (let i = 0; i < 6; i++) s.addShape(pres.shapes.RECTANGLE, { x: 5.7 + i * 0.5, y: 0, w: 0.05, h: HH, fill: { color: C.white, transparency: 88 } });
  s.addText("MATTERHACKERS", { x: 0.55, y: 0.5, w: 5, h: 0.3, fontSize: 13, bold: true, color: C.white, charSpacing: 3, fontFace: FONT_H });
  s.addText(cfg.name, { x: 0.5, y: 0.86, w: 6.2, h: 1.0, fontSize: 50, bold: true, color: C.white, fontFace: FONT_H });
  s.addText(cfg.tier, { x: 0.56, y: 1.92, w: 6.5, h: 0.3, fontSize: 11.5, bold: true, color: C.white, charSpacing: 2, fontFace: FONT_B });
  s.addText("“" + cfg.tagline + "”", { x: 0.56, y: 2.18, w: 6.0, h: 0.4, fontSize: 19, italic: true, bold: true, color: C.white, fontFace: FONT_H });
  if (cfg.badge) usaBadge(pres, s, 6.95, 0.62, 1.15);

  // ---- promise ----
  s.addText("THE PROMISE", { x: 0.55, y: 2.88, w: 5, h: 0.28, fontSize: 11.5, bold: true, color: cfg.col, charSpacing: 2, fontFace: FONT_H });
  s.addText(cfg.promise, { x: 0.55, y: 3.14, w: 7.4, h: 0.5, fontSize: 18, bold: true, color: C.ink, fontFace: FONT_H });

  // ---- description ----
  s.addText(cfg.desc, { x: 0.55, y: 3.74, w: 7.4, h: 1.1, fontSize: 11.5, color: C.body, fontFace: FONT_B, lineSpacing: 16, valign: "top" });

  // ---- highlights ----
  let hy = 4.9;
  cfg.highlights.forEach((h) => {
    s.addShape(pres.shapes.OVAL, { x: 0.55, y: hy, w: 0.6, h: 0.6, fill: { color: h.c } });
    s.addImage({ data: h.ic, x: 0.72, y: hy + 0.17, w: 0.26, h: 0.26 });
    s.addText(h.t, { x: 1.33, y: hy - 0.02, w: 6.6, h: 0.32, fontSize: 14, bold: true, color: C.ink, fontFace: FONT_H });
    s.addText(h.b, { x: 1.33, y: hy + 0.3, w: 6.6, h: 0.3, fontSize: 11, color: C.body, fontFace: FONT_B });
    hy += 0.66;
  });

  // ---- materials (wraps to a 2nd row) ----
  s.addText("MATERIALS", { x: 0.55, y: 6.94, w: 5, h: 0.28, fontSize: 11.5, bold: true, color: C.muted, charSpacing: 2, fontFace: FONT_H });
  let mx = 0.55, my = 7.24;
  cfg.materials.forEach((m) => {
    const w = 0.34 + m.length * 0.108;
    if (mx + w > 7.95) { mx = 0.55; my += 0.48; }
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: mx, y: my, w, h: 0.4, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 }, rectRadius: 0.2 });
    s.addText(m, { x: mx, y: my, w, h: 0.4, fontSize: 11, bold: true, color: cfg.col, align: "center", valign: "middle", fontFace: FONT_B });
    mx += w + 0.16;
  });

  // ---- proof + best-for columns (fixed position) ----
  const colY = 8.3;
  s.addShape(pres.shapes.RECTANGLE, { x: 0.55, y: colY, w: 3.55, h: 1.32, fill: { color: cfg.col }, shadow: shadow() });
  s.addText(cfg.proofBig, { x: 0.78, y: colY + 0.22, w: 3.1, h: 0.6, fontSize: 28, bold: true, color: C.white, fontFace: FONT_H });
  s.addText(cfg.proofSub, { x: 0.78, y: colY + 0.82, w: 3.1, h: 0.42, fontSize: 11, color: C.white, fontFace: FONT_B, lineSpacing: 13 });
  s.addShape(pres.shapes.RECTANGLE, { x: 4.35, y: colY, w: 3.6, h: 1.32, fill: { color: C.cloud }, line: { color: C.cloud2, width: 1 } });
  s.addText("BEST FOR", { x: 4.6, y: colY + 0.15, w: 3, h: 0.28, fontSize: 11, bold: true, color: cfg.col, charSpacing: 2, fontFace: FONT_H });
  s.addText(cfg.bestFor.map((b) => ({ text: b, options: { bullet: { code: "2022" }, color: C.body, fontSize: 11, breakLine: true, fontFace: FONT_B, paraSpaceAfter: 2 } })),
    { x: 4.62, y: colY + 0.44, w: 3.2, h: 0.85, valign: "top" });

  // ---- meta description footer ----
  const fy = 9.78;
  s.addShape(pres.shapes.RECTANGLE, { x: 0.55, y: fy, w: 7.4, h: 0.66, fill: { color: C.cloud2 } });
  s.addText("SEARCH / META DESCRIPTION", { x: 0.72, y: fy + 0.08, w: 6, h: 0.22, fontSize: 8.5, bold: true, color: C.muted, charSpacing: 1.5, fontFace: FONT_H });
  s.addText(cfg.meta, { x: 0.72, y: fy + 0.28, w: 7.1, h: 0.34, fontSize: 9.5, italic: true, color: C.body, fontFace: FONT_B, lineSpacing: 11, valign: "top" });

  // ---- bottom brand bar ----
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 10.6, w: 8.5, h: 0.4, fill: { color: cfg.col } });
  s.addText("matterhackers.com", { x: 0.55, y: 10.6, w: 4, h: 0.4, fontSize: 11, bold: true, color: C.white, valign: "middle", fontFace: FONT_B });
  s.addText("MatterHackers " + cfg.name + "  ·  " + cfg.price, { x: 4.0, y: 10.6, w: 3.95, h: 0.4, fontSize: 11, bold: true, color: C.white, align: "right", valign: "middle", fontFace: FONT_B });

  await pres.writeFile({ fileName: cfg.file });
  console.log("WROTE " + cfg.file);
}
})();
