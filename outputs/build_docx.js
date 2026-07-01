const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, AlignmentType, HeadingLevel,
  LevelFormat, BorderStyle, ShadingType, Header, Footer, TabStopType, TabStopPosition,
} = require("docx");

const GRAY = "6B7480", INK = "222629", BODY = "3C4655";

const SHEETS = [
  {
    file: "MatterHackers_ThriftyMake_OneSheet.docx",
    name: "ThriftyMake", tier: "Value Line", price: "$ · Budget",
    accent: "1E8E4E", accentBright: "27AE60", tint: "E9F7EF",
    tagline: "Quality that costs less.",
    promise: "The most affordable filament in our catalog.",
    desc: "MatterHackers ThriftyMake is the most affordable filament in our catalog — reliable, easy-to-print PLA+, ABS, and PETG at the lowest price per spool. It’s made for makers who print constantly: prototypes, practice runs, jigs, and high-volume jobs where every dollar counts. You get dependable results and clean prints without paying for premium features you don’t need.",
    highlights: [
      ["Lowest price per spool", "The best cost-per-kilogram in the MatterHackers catalog."],
      ["Reliable & easy to print", "Dependable results and clean prints, with no fuss."],
      ["Built for volume", "Made for constant printing — prototypes, jigs, and practice runs."],
    ],
    materials: ["PLA+", "ABS", "PETG"],
    proofBig: "Lowest price", proofSub: "in the entire MatterHackers catalog",
    bestFor: ["Hobbyists & classrooms", "High-volume printing", "Iterative prototyping", "Everyday practice prints"],
    meta: "MatterHackers ThriftyMake is our most affordable 3D printer filament — reliable PLA+, ABS, and PETG at the lowest price per spool. Quality that costs less.",
  },
  {
    file: "MatterHackers_Build_OneSheet.docx",
    name: "Build", tier: "Core Line", price: "$$ · Core",
    accent: "0077C2", accentBright: "009BFF", tint: "E6F4FF",
    tagline: "Consistency you can count on.",
    promise: "Our best-selling, do-everything filament.",
    desc: "MatterHackers Build is our best-selling filament — the everyday workhorse trusted by more makers than any other line we carry. What sets it apart is consistency: tight color matching and dependable performance from one spool to the next, so the print that worked yesterday works again today. Available in the widest range of materials and colors, Build delivers professional-looking results at a price that still fits the bench.",
    highlights: [
      ["Consistent, spool after spool", "Tight color matching and dependable performance, batch after batch."],
      ["The widest range", "PLA, ABS, PETG, TPU, ASA, Nylon and more — in every color."],
      ["Pro looks, bench price", "Professional-looking results that still fit the budget."],
    ],
    materials: ["PLA", "ABS", "PETG", "TPU", "ASA", "Nylon", "PVA"],
    proofBig: "#1-selling", proofSub: "filament at MatterHackers",
    bestFor: ["Everyday printing", "Personal projects", "Production-ready prototypes", "Multi-material builds"],
    meta: "MatterHackers Build is our #1-selling 3D printer filament: consistent color and performance spool after spool, in the widest range of materials — at a price that fits the bench.",
  },
  {
    file: "MatterHackers_PRO_OneSheet.docx",
    name: "PRO", tier: "Premium · Professional Line", price: "$$$ · Premium",
    accent: "A93226", accentBright: "C0392B", tint: "FBEDEB",
    tagline: "Performance when it counts.",
    promise: "Professional-grade filament, engineered to perform.",
    desc: "MatterHackers PRO is our professional-grade line, engineered for work that has to perform. Every PRO filament is held to a tight ±0.02mm diameter tolerance and rigorously tested for consistent, repeatable results — ideal for functional prototypes, manufacturing aids, and end-use parts. PRO spans precision staples alongside engineering-grade composites strong enough to replace machined metal, with country of origin clearly labeled on every SKU.",
    highlights: [
      ["Tight ±0.02mm tolerance", "Rigorously tested for consistent, repeatable results."],
      ["Engineering composites", "NylonX, NylonG and NylonK for metal-replacing strength."],
      ["Transparent origin", "Every SKU labeled; Made in USA badge on qualifying materials."],
    ],
    materials: ["PLA", "Tough PLA", "ABS", "ASA", "PETG", "Nylon", "PPS", "NylonX", "NylonG", "NylonK"],
    proofBig: "±0.02mm", proofSub: "diameter tolerance, rigorously tested",
    bestFor: ["Functional prototypes", "Manufacturing aids", "End-use parts", "Metal-replacement parts"],
    meta: "MatterHackers PRO is our professional-grade 3D printer filament: ±0.02mm tolerance, engineering composites (NylonX/G/K), and clear origin labeling. Performance when it counts.",
  },
];

function sectionLabel(text, accent) {
  return new Paragraph({
    spacing: { before: 300, after: 90 },
    children: [new TextRun({ text: text.toUpperCase(), bold: true, color: accent, size: 22, allCaps: true, characterSpacing: 30 })],
  });
}

function build(cfg) {
  const kids = [];

  // brand line
  kids.push(new Paragraph({
    spacing: { after: 20 },
    children: [new TextRun({ text: "MATTERHACKERS", bold: true, color: GRAY, size: 20, characterSpacing: 60 })],
  }));
  // title
  kids.push(new Paragraph({
    spacing: { after: 40 },
    children: [new TextRun({ text: cfg.name, bold: true, color: cfg.accentBright, size: 60 })],
  }));
  // tier + tagline
  kids.push(new Paragraph({
    spacing: { after: 60 },
    children: [
      new TextRun({ text: cfg.tier.toUpperCase() + "   ", bold: true, color: cfg.accent, size: 20, characterSpacing: 20 }),
    ],
  }));
  kids.push(new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: cfg.accentBright, space: 8 } },
    spacing: { after: 200 },
    children: [new TextRun({ text: "“" + cfg.tagline + "”", italics: true, bold: true, color: INK, size: 32 })],
  }));

  // promise
  kids.push(sectionLabel("The promise", cfg.accent));
  kids.push(new Paragraph({
    spacing: { after: 160 },
    children: [new TextRun({ text: cfg.promise, bold: true, color: INK, size: 30 })],
  }));

  // description
  kids.push(sectionLabel("Description", cfg.accent));
  kids.push(new Paragraph({
    spacing: { after: 120, line: 300 },
    children: [new TextRun({ text: cfg.desc, color: BODY, size: 22 })],
  }));

  // highlights
  kids.push(sectionLabel("Why " + cfg.name, cfg.accent));
  cfg.highlights.forEach(([h, b]) => {
    kids.push(new Paragraph({
      numbering: { reference: "bul-" + cfg.name, level: 0 },
      spacing: { after: 80, line: 288 },
      children: [
        new TextRun({ text: h + " — ", bold: true, color: INK, size: 22 }),
        new TextRun({ text: b, color: BODY, size: 22 }),
      ],
    }));
  });

  // materials
  kids.push(sectionLabel("Materials", cfg.accent));
  const matRuns = [];
  cfg.materials.forEach((m, i) => {
    matRuns.push(new TextRun({ text: m, bold: true, color: cfg.accent, size: 24 }));
    if (i < cfg.materials.length - 1) matRuns.push(new TextRun({ text: "    ·    ", color: GRAY, size: 22 }));
  });
  kids.push(new Paragraph({ spacing: { after: 160 }, children: matRuns }));

  // proof callout (shaded paragraph)
  kids.push(sectionLabel("Proof point", cfg.accent));
  kids.push(new Paragraph({
    shading: { type: ShadingType.CLEAR, fill: cfg.tint },
    border: {
      top: { style: BorderStyle.SINGLE, size: 4, color: cfg.tint, space: 10 },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: cfg.tint, space: 10 },
      left: { style: BorderStyle.SINGLE, size: 30, color: cfg.accentBright, space: 12 },
      right: { style: BorderStyle.SINGLE, size: 4, color: cfg.tint, space: 10 },
    },
    spacing: { before: 40, after: 200, line: 300 },
    children: [
      new TextRun({ text: cfg.proofBig, bold: true, color: cfg.accent, size: 40 }),
      new TextRun({ text: "  " + cfg.proofSub, color: BODY, size: 22 }),
    ],
  }));

  // best for
  kids.push(sectionLabel("Best for", cfg.accent));
  cfg.bestFor.forEach((b) => {
    kids.push(new Paragraph({
      numbering: { reference: "bul-" + cfg.name, level: 0 },
      spacing: { after: 40 },
      children: [new TextRun({ text: b, color: BODY, size: 22 })],
    }));
  });

  // meta
  kids.push(sectionLabel("Search / meta description", cfg.accent));
  kids.push(new Paragraph({
    shading: { type: ShadingType.CLEAR, fill: "F2F4F7" },
    spacing: { before: 40, after: 40, line: 276 },
    children: [new TextRun({ text: cfg.meta, italics: true, color: BODY, size: 20 })],
  }));

  const doc = new Document({
    creator: "MatterHackers Brand",
    title: "MatterHackers " + cfg.name + " — One-Sheet",
    styles: { default: { document: { run: { font: "Arial", size: 22, color: BODY } } } },
    numbering: {
      config: [{
        reference: "bul-" + cfg.name,
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { run: { color: cfg.accentBright }, paragraph: { indent: { left: 360, hanging: 220 } } } }],
      }],
    },
    sections: [{
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 } } },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            border: { top: { style: BorderStyle.SINGLE, size: 6, color: "DDE2E8", space: 6 } },
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [
              new TextRun({ text: "matterhackers.com", bold: true, color: cfg.accent, size: 18 }),
              new TextRun({ text: "\tMatterHackers " + cfg.name + "   ·   " + cfg.price, color: GRAY, size: 18 }),
            ],
          })],
        }),
      },
      children: kids,
    }],
  });

  return Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(cfg.file, buf); console.log("WROTE " + cfg.file); });
}

(async () => { for (const c of SHEETS) await build(c); })();
