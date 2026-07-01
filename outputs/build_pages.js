const fs = require("fs");

const CSS = (acc) => `
  :root{
    --blue:#009BFF; --blue-d:#0082d6; --blue-dd:#00659f;
    --green:#4a9d33; --green-d:#3f8a2b;                 /* add-to-cart (constant) */
    --acc:${acc.a}; --acc-d:${acc.d}; --acc-dd:${acc.dd}; --acc-bg:${acc.bg}; --acc-border:${acc.bd};
    --orange:#e8641e;
    --ink:#23262b; --body:#4c5560; --muted:#8a94a2;
    --border:#e3e7ec; --border2:#eef1f4;
    --bg:#fff; --gray:#f5f6f8; --dark:#20242b;
    --wrap:1240px;
    --sans:"Helvetica Neue",Helvetica,Arial,"Segoe UI",Roboto,sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{font-family:var(--sans);color:var(--body);background:var(--bg);font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
  a{color:var(--blue);text-decoration:none}
  a:hover{text-decoration:underline}
  .wrap{max-width:var(--wrap);margin:0 auto;padding:0 20px}
  h1,h2,h3,h4{color:var(--ink);line-height:1.2;font-weight:700}
  .util{background:#eef1f4;color:#5a6270;font-size:12.5px}
  .util .wrap{display:flex;justify-content:space-between;align-items:center;height:34px}
  .util b{color:var(--ink)}
  @media(max-width:700px){.util .wrap>span:last-child{display:none}}
  header.site{border-bottom:1px solid var(--border);background:#fff}
  .head-in{display:flex;align-items:center;gap:24px;height:70px}
  .logo{font-size:23px;font-weight:800;color:var(--ink);letter-spacing:-.02em;white-space:nowrap}
  .logo b{color:var(--blue);font-weight:800}
  .search{flex:1;display:flex;max-width:620px}
  .search input{flex:1;border:1.5px solid var(--border);border-right:none;border-radius:5px 0 0 5px;padding:10px 14px;font-size:14px;outline:none}
  .search input:focus{border-color:var(--blue)}
  .search button{background:var(--blue);border:none;color:#fff;padding:0 18px;border-radius:0 5px 5px 0;cursor:pointer}
  .head-ic{display:flex;gap:22px;align-items:center;color:var(--body);font-size:13px;font-weight:600}
  .head-ic a{color:var(--body);display:flex;flex-direction:column;align-items:center;gap:2px}
  .head-ic svg{width:22px;height:22px;stroke:var(--body);fill:none;stroke-width:1.7}
  @media(max-width:860px){.search{display:none}}
  .catnav{background:var(--dark)}
  .catnav .wrap{display:flex;gap:2px;height:44px;align-items:stretch;overflow-x:auto}
  .catnav a{color:#d3d8df;font-size:13.5px;font-weight:600;display:flex;align-items:center;padding:0 16px;white-space:nowrap}
  .catnav a:hover{background:rgba(255,255,255,.08);color:#fff;text-decoration:none}
  .catnav a.on{color:#fff;box-shadow:inset 0 -3px 0 var(--blue)}
  .banner{background:var(--acc-bg);border-bottom:1px solid var(--acc-border)}
  .banner .wrap{display:flex;align-items:center;justify-content:space-between;gap:30px;padding:34px 20px}
  .banner .txt{max-width:680px}
  .banner .tier{display:inline-block;background:var(--acc);color:#fff;font-size:11.5px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;padding:5px 12px;border-radius:4px}
  .banner h1{font-size:40px;font-weight:800;color:var(--ink);margin:12px 0 4px;letter-spacing:-.02em}
  .banner h1 small{display:block;font-size:14px;font-weight:800;letter-spacing:.2em;color:var(--acc-d);margin-bottom:2px}
  .banner .tag{font-size:20px;font-style:italic;font-weight:700;color:var(--acc-d);margin-bottom:10px}
  .banner p{font-size:15.5px;color:var(--body)}
  .banner .onote{font-size:12.5px;color:var(--body);margin-top:10px;display:flex;align-items:center;gap:8px;font-weight:600}
  .banner .onote .dot{width:16px;height:16px;border-radius:50%;background:#1F3A93;flex-shrink:0}
  .banner .art{flex-shrink:0}
  .banner .art svg{width:150px;height:150px}
  @media(max-width:820px){.banner .art{display:none}}
  .crumb{font-size:12.5px;color:var(--muted);padding:14px 0}
  .crumb a{color:var(--muted)}
  .crumb span{color:var(--ink);font-weight:600}
  .intro{padding:4px 0 26px;max-width:900px}
  .intro h2{font-size:20px;margin-bottom:10px}
  .intro p{font-size:15px;margin-bottom:10px}
  .subs{padding:6px 0 30px}
  .subs .h{font-size:17px;font-weight:700;color:var(--ink);margin-bottom:14px}
  .sub-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .sub{display:flex;gap:14px;align-items:center;border:1px solid var(--border);border-radius:8px;padding:16px;background:#fff;transition:.12s}
  .sub:hover{border-color:var(--acc);box-shadow:0 6px 16px rgba(0,0,0,.06);text-decoration:none}
  .sub .sp{width:52px;height:52px;flex-shrink:0}
  .sub b{color:var(--ink);font-size:15px;display:block}
  .sub span{color:var(--body);font-size:13px}
  @media(max-width:760px){.sub-grid{grid-template-columns:1fr}}
  .main{display:grid;grid-template-columns:212px 1fr;gap:28px;padding:8px 0 40px}
  .side h4{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);border-bottom:1px solid var(--border);padding-bottom:8px;margin:18px 0 10px}
  .side h4:first-child{margin-top:0}
  .side label{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--body);padding:4px 0;cursor:pointer}
  .side label input{accent-color:var(--blue)}
  .side .cnt{color:var(--muted);font-size:12px;margin-left:auto}
  .side .clear{font-size:12.5px;font-weight:600}
  @media(max-width:860px){.main{grid-template-columns:1fr}.side{display:none}}
  .grid-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
  .grid-head .res{font-size:13px;color:var(--muted)}
  .grid-head select{border:1px solid var(--border);border-radius:5px;padding:7px 10px;font-size:13px;color:var(--body)}
  .prod-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
  @media(max-width:1100px){.prod-grid{grid-template-columns:repeat(3,1fr)}}
  @media(max-width:760px){.prod-grid{grid-template-columns:repeat(2,1fr)}}
  .prod{border:1px solid var(--border);border-radius:8px;background:#fff;overflow:hidden;display:flex;flex-direction:column;transition:.12s;position:relative}
  .prod:hover{box-shadow:0 8px 20px rgba(0,0,0,.09)}
  .ribbons{position:absolute;top:8px;left:8px;display:flex;gap:5px;z-index:2}
  .rb{font-size:9.5px;font-weight:800;letter-spacing:.03em;text-transform:uppercase;padding:3px 7px;border-radius:3px;color:#fff}
  .rb.bs{background:var(--blue)}
  .rb.ep{background:#f0f3f6;color:#5a6270}
  .swatch{height:158px;display:flex;align-items:center;justify-content:center;border-bottom:1px solid var(--border2)}
  .swatch svg{width:96px;height:96px}
  .pin{padding:13px 14px 15px;display:flex;flex-direction:column;flex:1}
  .pin .mat{font-size:10.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--acc-d)}
  .pin h4{font-size:13.5px;font-weight:600;color:var(--ink);margin:5px 0 10px;line-height:1.35;flex:1}
  .pin h4 a{color:var(--ink)}
  .origin{display:inline-block;font-size:9px;font-weight:800;letter-spacing:.04em;padding:3px 7px;border-radius:3px;margin-bottom:9px;text-transform:uppercase;align-self:flex-start}
  .origin.usa{background:#eaf0fb;color:#1F3A93}
  .origin.imp{background:#eef1f4;color:#5a6270}
  .price{font-size:18px;font-weight:800;color:var(--ink)}
  .bulk{font-size:11.5px;color:var(--muted);margin:2px 0 11px}
  .add{background:var(--green);color:#fff;border:none;border-radius:5px;padding:10px;font-weight:700;font-size:13px;cursor:pointer;width:100%}
  .add:hover{background:var(--green-d)}
  .eta{font-size:12px;color:var(--muted);text-align:center;padding:10px 0 2px;font-weight:600}
  .compare{background:var(--gray);border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
  .compare .wrap{padding:34px 20px}
  .compare .h{font-size:19px;font-weight:700;color:var(--ink);margin-bottom:4px}
  .compare .sh{font-size:14px;color:var(--body);margin-bottom:20px}
  .cmp-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .cmp{background:#fff;border:1px solid var(--border);border-radius:8px;padding:18px 20px;position:relative}
  .cmp.on{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc)}
  .cmp .t{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;padding:3px 9px;border-radius:4px;color:#fff;display:inline-block;margin-bottom:9px}
  .cmp h3{font-size:17px;color:var(--ink)}
  .cmp h3 small{display:block;font-size:11px;letter-spacing:.14em;color:var(--muted);font-weight:700}
  .cmp p{font-size:13.5px;color:var(--body);margin:7px 0 10px}
  .cmp .lvl{font-size:12.5px;font-weight:700;color:var(--muted)}
  .here{position:absolute;top:-10px;right:14px;background:var(--acc);color:#fff;font-size:10px;font-weight:800;letter-spacing:.05em;padding:3px 10px;border-radius:12px;text-transform:uppercase}
  @media(max-width:760px){.cmp-grid{grid-template-columns:1fr}}
  .guides{padding:38px 0}
  .guides .h{font-size:19px;font-weight:700;color:var(--ink);margin-bottom:16px}
  .g-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
  .g{border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#fff}
  .g .thumb{height:96px;background:linear-gradient(135deg,#e8f4ff,#d4e9fb)}
  .g .gin{padding:14px}
  .g .gin b{font-size:13.5px;color:var(--ink);display:block;line-height:1.35}
  .g .gin span{font-size:12px;color:var(--muted);display:block;margin-top:6px}
  @media(max-width:900px){.g-grid{grid-template-columns:repeat(2,1fr)}}
  .news{background:var(--dark);color:#cdd4dd}
  .news .wrap{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:26px 20px;flex-wrap:wrap}
  .news b{color:#fff;font-size:16px}
  .news form{display:flex}
  .news input{border:none;border-radius:5px 0 0 5px;padding:11px 14px;font-size:14px;width:260px;outline:none}
  .news button{background:var(--blue);border:none;color:#fff;font-weight:700;padding:0 20px;border-radius:0 5px 5px 0;cursor:pointer}
  footer{background:#181b21;color:#8b95a2;font-size:13px;padding:36px 0 26px}
  .foot-grid{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:26px;padding-bottom:24px;border-bottom:1px solid #2a2f38}
  footer h5{color:#fff;font-size:13px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px}
  footer a{color:#8b95a2;display:block;padding:3px 0}
  footer a:hover{color:#fff}
  .foot-logo{font-size:20px;font-weight:800;color:#fff;margin-bottom:10px}
  .foot-logo b{color:var(--blue)}
  .foot-bot{padding-top:18px;font-size:12px;color:#6b7480}
  @media(max-width:820px){.foot-grid{grid-template-columns:1fr 1fr}}
`;

function spool(c) {
  const lightish = ["#eef1f5", "#eff2f6"].includes(c.toLowerCase());
  const stroke = lightish ? "#c7cfda" : "rgba(0,0,0,.14)";
  return `<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">`
    + `<ellipse cx="50" cy="86" rx="30" ry="5" fill="rgba(0,0,0,.06)"/>`
    + `<circle cx="50" cy="48" r="40" fill="${c}" stroke="${stroke}" stroke-width="2"/>`
    + `<circle cx="50" cy="48" r="25" fill="#fff" opacity=".92"/>`
    + `<circle cx="50" cy="48" r="8" fill="${c}" stroke="${stroke}" stroke-width="1.3"/></svg>`;
}
function subSpool(c) {
  return `<svg class="sp" viewBox="0 0 100 100"><circle cx="50" cy="50" r="42" fill="${c}"/><circle cx="50" cy="50" r="26" fill="#fff" opacity=".93"/><circle cx="50" cy="50" r="9" fill="${c}"/></svg>`;
}

function page(cfg) {
  const products = cfg.products.map(p => {
    const ribbons = `<div class="ribbons">${p.bs ? '<span class="rb bs">Best Seller</span>' : ''}${p.ep ? '<span class="rb ep">Expert Pick</span>' : ''}</div>`;
    const bg = (p.c.toLowerCase() === "#eef1f5") ? "#f7f9fb" : "#fcfdfe";
    const origin = p.origin ? `<span class="origin ${p.origin === 'usa' ? 'usa' : 'imp'}">${p.origin === 'usa' ? 'Made in USA' : 'Imported'}</span>` : '';
    const action = p.eta ? `<div class="eta">${p.eta}</div>` : `<button class="add">Add to Cart</button>`;
    const bulk = p.bulk ? `<div class="bulk">${p.bulk}</div>` : `<div class="bulk">Volume pricing available</div>`;
    return `<div class="prod">${ribbons}<div class="swatch" style="background:${bg}">${spool(p.c)}</div>`
      + `<div class="pin"><div class="mat">${p.mat}</div><h4><a href="#">${p.n}</a></h4>${origin}`
      + `<div class="price">${p.price}</div>${bulk}${action}</div></div>`;
  }).join("\n");

  const subs = cfg.subs.map(x => `<a class="sub" href="#products">${subSpool(x.color)}<span><b>${x.title}</b>${x.desc}</span></a>`).join("\n");

  const filters = cfg.filters.map(g => `<h4>${g.name}</h4>` + g.items.map(it =>
    `<label><input type="checkbox"${it.on ? ' checked' : ''}> ${it.label} <span class="cnt">${it.n}</span></label>`).join("")).join("\n");

  const cmp = cfg.compare.map((c, i) => `<div class="cmp${i === cfg.activeIdx ? ' on' : ''}">`
    + (i === cfg.activeIdx ? '<div class="here">You\'re here</div>' : '')
    + `<span class="t" style="background:${c.col}">${c.tier}</span>`
    + `<h3><small>MATTERHACKERS</small>${c.name}</h3><p>${c.p}</p><div class="lvl">${c.lvl}</div></div>`).join("\n");

  const guides = cfg.guides.map(g => `<a class="g" href="#"><div class="thumb"></div><div class="gin"><b>${g.t}</b><span>${g.s}</span></div></a>`).join("\n");

  const onote = cfg.onote ? `<div class="onote"><span class="dot"></span>${cfg.onote}</div>` : '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>${cfg.title}</title>
<meta name="description" content="${cfg.meta}" />
<style>${CSS(cfg.acc)}</style>
</head>
<body>
<div class="util"><div class="wrap"><span><b>FREE, FAST Shipping</b> on orders over $35 in the U.S.*</span><span>Customer Service &nbsp;·&nbsp; +1 (800) 613-4290</span></div></div>
<header class="site"><div class="wrap head-in">
  <div class="logo">Matter<b>Hackers</b></div>
  <div class="search"><input type="text" placeholder="Search 20,000+ products" /><button aria-label="Search"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" width="18" height="18"><circle cx="11" cy="11" r="7"/><path d="m21 21-4-4"/></svg></button></div>
  <div class="head-ic"><a href="#"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>Account</a><a href="#"><svg viewBox="0 0 24 24"><circle cx="9" cy="20" r="1.6"/><circle cx="18" cy="20" r="1.6"/><path d="M2 3h3l2.4 13h11L21 7H6"/></svg>Cart</a></div>
</div></header>
<div class="catnav"><div class="wrap">
  <a href="#">3D Printers</a><a class="on" href="#">3D Printer Filament</a><a href="#">Bambu Lab</a>
  <a href="#">PLA</a><a href="#">ABS</a><a href="#">PETG</a>
  <a href="#">ThriftyMake</a><a href="#">Build</a><a href="#">PRO</a><a href="#">Sale</a>
</div></div>
<div class="banner"><div class="wrap">
  <div class="txt">
    <span class="tier">${cfg.tier}</span>
    <h1><small>MATTERHACKERS</small>${cfg.h1}</h1>
    <div class="tag">"${cfg.tagline}"</div>
    <p>${cfg.blurb}</p>
    ${onote}
  </div>
  <div class="art">${spool(cfg.artColor)}</div>
</div></div>
<div class="wrap">
  <div class="crumb"><a href="#">Home</a> &nbsp;›&nbsp; <a href="#">Store</a> &nbsp;›&nbsp; <a href="#">3D Printer Filament</a> &nbsp;›&nbsp; <span>${cfg.h1}</span></div>
  <div class="intro"><h2>${cfg.introH}</h2><p>${cfg.introP1}</p><p>${cfg.introP2}</p></div>
  <div class="subs"><div class="h">${cfg.subHead}</div><div class="sub-grid">${subs}</div></div>
  <div class="main" id="products">
    <aside class="side">
      <div style="display:flex;justify-content:space-between;align-items:center"><strong style="font-size:14px;color:var(--ink)">Filter</strong><a class="clear" href="#">Clear</a></div>
      ${filters}
    </aside>
    <div>
      <div class="grid-head"><div class="res">${cfg.count} products</div><select><option>Sort: Best Selling</option><option>Price: Low to High</option><option>Price: High to Low</option><option>Newest</option></select></div>
      <div class="prod-grid">${products}</div>
      <div style="text-align:center;margin-top:26px"><a href="#" style="display:inline-block;border:1.5px solid var(--blue);color:var(--blue);font-weight:700;border-radius:6px;padding:12px 26px">Load more ${cfg.short} filament</a></div>
    </div>
  </div>
</div>
<div class="compare" id="compare"><div class="wrap">
  <div class="h">Where ${cfg.short} fits</div>
  <div class="sh">Three lines, one simple ladder — ${cfg.ladderNote}</div>
  <div class="cmp-grid">${cmp}</div>
</div></div>
<div class="wrap guides"><div class="h">Guides &amp; Articles</div><div class="g-grid">${guides}</div></div>
<div class="news"><div class="wrap"><b>Get the latest from MatterHackers</b><form onsubmit="return false"><input type="email" placeholder="Email address" /><button>Subscribe</button></form></div></div>
<footer><div class="wrap">
  <div class="foot-grid">
    <div><div class="foot-logo">Matter<b>Hackers</b></div><p style="max-width:340px">${cfg.footBlurb}</p></div>
    <div><h5>Shop</h5><a href="#">3D Printer Filament</a><a href="#">ThriftyMake</a><a href="#">Build</a><a href="#">PRO</a></div>
    <div><h5>Browse</h5><a href="#">News</a><a href="#">Guides</a><a href="#">Support</a><a href="#">Professional</a></div>
    <div><h5>MatterHackers</h5><a href="#">About</a><a href="#">Contact</a><a href="#">Rewards</a><a href="#">Returns</a></div>
  </div>
  <div class="foot-bot">© 2026 MatterHackers Inc. &nbsp;·&nbsp; Draft landing-page concept for internal review.</div>
</div></footer>
</body>
</html>`;
}

// ---------------- BUILD ----------------
const build = {
  file: "Build_Landing_Page.html",
  title: "MatterHackers Build Filament | MatterHackers",
  meta: "MatterHackers Build is our #1-selling 3D printer filament: consistent color and performance spool after spool, in the widest range of materials - at a price that fits the bench.",
  acc: { a: "#009BFF", d: "#0082d6", dd: "#00659f", bg: "#e9f4ff", bd: "#cfe6fb" },
  tier: "Core Line", h1: "Build Filament", short: "Build", artColor: "#009BFF",
  tagline: "Consistency you can count on.",
  blurb: "Consistent color and performance from one spool to the next, in the widest range of materials and colors — at a price that still fits the bench.",
  introH: "Consistent quality that more makers trust than any other",
  introP1: "MatterHackers Build is our best-selling filament — the everyday workhorse trusted by more makers than any other line we carry. What sets it apart is consistency: tight color matching and dependable performance from one spool to the next, so the print that worked yesterday works again today.",
  introP2: "Available in the widest range of materials and colors — PLA, ABS, PETG, TPU, ASA, Nylon and more — Build delivers professional-looking results at a price that still fits the bench. Just getting started or watching costs? Try <a href='#compare'>MatterHackers ThriftyMake</a>. Need professional tolerances and engineering materials? Step up to <a href='#compare'>MatterHackers PRO</a>.",
  subHead: "Build Filament Collections",
  subs: [
    { title: "Build PLA", desc: "The #1-selling PLA filament at MatterHackers.", color: "#1c1f24" },
    { title: "Build PETG", desc: "Tough, sturdy prints with easy printing.", color: "#009BFF" },
    { title: "Build ABS", desc: "Durable, temperature-resistant parts.", color: "#8a94a6" },
  ],
  count: 380,
  filters: [
    { name: "Material", items: [{ label: "PLA", n: 180, on: true }, { label: "PETG", n: 62 }, { label: "ABS", n: 45 }, { label: "TPU", n: 24 }, { label: "ASA", n: 18 }, { label: "Nylon", n: 14 }] },
    { name: "Color", items: [{ label: "Black", n: 46 }, { label: "White", n: 44 }, { label: "Gray", n: 30 }, { label: "Blue", n: 34 }, { label: "Red", n: 28 }] },
    { name: "Spool Weight", items: [{ label: "1 kg", n: 300, on: true }, { label: "3 kg", n: 20 }] },
    { name: "Packaging", items: [{ label: "Spools", n: 260 }, { label: "Refills", n: 120 }] },
  ],
  products: [
    { n: "Build PLA Filament — 1.75mm (1kg) — Black", mat: "PLA", price: "$22.99", bulk: "$19.31 in bulk", c: "#1c1f24", bs: true, ep: true },
    { n: "Build PLA Filament — 1.75mm (1kg) — White", mat: "PLA", price: "$22.99", bulk: "$19.31 in bulk", c: "#eef1f5", bs: true, ep: true },
    { n: "Build PLA Filament — 1.75mm (1kg) — Gray", mat: "PLA", price: "$22.99", bulk: "$19.31 in bulk", c: "#8a94a6", ep: true },
    { n: "Build PLA Filament — 1.75mm (1kg) — Royal Blue", mat: "PLA", price: "$22.99", bulk: "$19.31 in bulk", c: "#1f5fd0", ep: true },
    { n: "Build PETG Filament — 1.75mm (1kg) — Black", mat: "PETG", price: "$24.99", bulk: "$20.99 in bulk", c: "#20242b", bs: true, ep: true },
    { n: "Build ABS Filament — 1.75mm (1kg) — Black", mat: "ABS", price: "$22.99", bulk: "$19.31 in bulk", c: "#1c1f24", bs: true, ep: true },
    { n: "Build Tough PLA Filament — 1.75mm (1kg) — Black", mat: "Tough PLA", price: "$27.99", bulk: "$23.51 in bulk", c: "#2a2d33", ep: true },
    { n: "Build TPU Flexible Filament — 1.75mm (1kg) — Black", mat: "TPU", price: "$36.99", bulk: "$31.07 in bulk", c: "#1c1f24", ep: true, eta: "Est. In Stock: Aug 1st" },
  ],
  activeIdx: 1,
  ladderNote: "step down to ThriftyMake for the lowest price, or up to PRO for professional performance.",
  compare: [
    { tier: "Value", col: "#4a9d33", name: "ThriftyMake", p: "Reliable quality at the lowest catalog price, for high-volume everyday printing.", lvl: "$ · Lowest price" },
    { tier: "Core", col: "#009BFF", name: "Build", p: "Consistent color and performance spool after spool, in the widest range of materials.", lvl: "$$ · #1-selling filament" },
    { tier: "Premium", col: "#C0392B", name: "PRO", p: "Professional-grade performance and tight tolerances, including engineering composites.", lvl: "$$$ · Performance when it counts" },
  ],
  guides: [
    { t: "How To Succeed When 3D Printing With PLA Filament", s: "Best practices for great PLA prints." },
    { t: "How To Succeed When Printing With ABS", s: "Strong, heat-resistant parts, done right." },
    { t: "3D Printer Filament Comparison Guide", s: "Pick the right material for the job." },
    { t: "How To: Smooth and Finish Your PLA Prints", s: "Post-processing for show-ready parts." },
  ],
  footBlurb: "Our #1-selling filament — consistent color and performance, spool after spool, in the widest range of materials.",
};

// ---------------- PRO ----------------
const pro = {
  file: "PRO_Landing_Page.html",
  title: "MatterHackers PRO Filament | MatterHackers",
  meta: "MatterHackers PRO is our professional-grade 3D printer filament: +/-0.02mm tolerance, engineering composites (NylonX/G/K), and clear origin labeling. Performance when it counts.",
  acc: { a: "#C0392B", d: "#a93226", dd: "#8f281d", bg: "#fbeeec", bd: "#f2d6d1" },
  tier: "Premium · Professional Line", h1: "PRO Filament", short: "PRO", artColor: "#C0392B",
  tagline: "Performance when it counts.",
  blurb: "Professional-grade filament held to a tight ±0.02mm tolerance — from US-made precision staples to engineering composites strong enough to replace machined metal.",
  onote: "Every SKU labeled for country of origin — Made in USA where applicable.",
  introH: "Professional-grade performance, engineered to perform",
  introP1: "MatterHackers PRO is our professional-grade line, engineered for work that has to perform. Every PRO filament is held to a tight ±0.02mm diameter tolerance and rigorously tested for consistent, repeatable results — ideal for functional prototypes, manufacturing aids, and end-use parts.",
  introP2: "PRO spans precision staples like PLA, Tough PLA, ABS, ASA, PETG, Nylon and PPS alongside our engineering-grade composites — NylonX, NylonG and NylonK — strong enough to replace machined metal. Country of origin is clearly labeled on every SKU, with a Made in USA badge on qualifying materials. Looking for everyday value instead? See <a href='#compare'>MatterHackers Build</a> or <a href='#compare'>MatterHackers ThriftyMake</a>.",
  subHead: "PRO Filament Collections",
  subs: [
    { title: "PRO Precision Staples", desc: "US-made PLA, ABS, ASA, PETG, Nylon & PPS.", color: "#C0392B" },
    { title: "PRO Composite (Engineering)", desc: "NylonX, NylonG & NylonK — fiber-reinforced.", color: "#E07B2C" },
    { title: "PRO Support & Specialty", desc: "PVA supports, ESD-safe & more.", color: "#5a6270" },
  ],
  count: 400,
  filters: [
    { name: "Material", items: [{ label: "PLA", n: 70, on: true }, { label: "Tough PLA", n: 40 }, { label: "ABS", n: 44 }, { label: "ASA", n: 30 }, { label: "PETG", n: 38 }, { label: "Nylon", n: 26 }, { label: "PPS", n: 4 }] },
    { name: "Feature", items: [{ label: "Carbon Fiber", n: 12 }, { label: "Glass Fiber", n: 8 }, { label: "ESD-Safe", n: 6 }] },
    { name: "Origin", items: [{ label: "Made in USA", n: 340 }, { label: "Imported", n: 60 }] },
    { name: "Packaging", items: [{ label: "Spools", n: 360 }, { label: "Refills", n: 40 }] },
  ],
  products: [
    { n: "PRO PLA Filament — 1.75mm (1kg) — Black", mat: "PLA", price: "$52.00", bulk: "Volume pricing available", c: "#1c1f24", bs: true, ep: true, origin: "usa" },
    { n: "PRO Tough PLA Filament — 1.75mm (1kg) — Black", mat: "Tough PLA", price: "$57.00", bulk: "Volume pricing available", c: "#2a2d33", bs: true, ep: true, origin: "usa" },
    { n: "PRO ABS Filament — 1.75mm (1kg) — Jet Gray", mat: "ABS", price: "$52.00", bulk: "Volume pricing available", c: "#4a4f57", ep: true, origin: "usa" },
    { n: "PRO PETG Filament — 1.75mm (1kg) — Black", mat: "PETG", price: "$57.00", bulk: "Volume pricing available", c: "#20242b", bs: true, ep: true, origin: "usa" },
    { n: "PRO ASA Filament — 1.75mm (1kg) — Black", mat: "ASA", price: "$52.00", bulk: "Volume pricing available", c: "#22252b", origin: "usa", eta: "Est. In Stock: Jul 22nd" },
    { n: "PRO Nylon Filament — 1.75mm (0.75kg) — Black", mat: "Nylon", price: "$62.00", bulk: "Volume pricing available", c: "#1c1f24", ep: true, origin: "usa" },
    { n: "NylonX Carbon Fiber PA12 Filament — 1.75mm (0.5kg)", mat: "NylonX · Composite", price: "$63.00", bulk: "$55.44 in bulk", c: "#26282c", bs: true, ep: true, origin: "imported" },
    { n: "NylonG Glass Fiber Nylon Filament — 1.75mm (0.5kg)", mat: "NylonG · Composite", price: "$63.00", bulk: "Volume pricing available", c: "#3a3f45", ep: true, origin: "imported" },
  ],
  activeIdx: 2,
  ladderNote: "step down to Build for everyday consistency, or ThriftyMake for the lowest price.",
  compare: [
    { tier: "Value", col: "#4a9d33", name: "ThriftyMake", p: "Reliable quality at the lowest catalog price, for high-volume everyday printing.", lvl: "$ · Lowest price" },
    { tier: "Core", col: "#009BFF", name: "Build", p: "Consistent color and performance spool after spool, in the widest range of materials.", lvl: "$$ · #1-selling filament" },
    { tier: "Premium", col: "#C0392B", name: "PRO", p: "Professional-grade performance and tight tolerances, including engineering composites.", lvl: "$$$ · Performance when it counts" },
  ],
  guides: [
    { t: "The Science of PRO Series Materials", s: "How PRO filament is formulated and tested." },
    { t: "How to Succeed with NylonX", s: "Strong, durable carbon-fiber nylon prints." },
    { t: "3D Printing with Nylon and Composites", s: "A gentler touch for tough materials." },
    { t: "How To Succeed When 3D Printing With ASA", s: "Durable, UV-resistant outdoor parts." },
  ],
  footBlurb: "Professional-grade filament: ±0.02mm tolerance, engineering composites, and clear origin labeling on every SKU.",
};

for (const cfg of [build, pro]) {
  fs.writeFileSync(cfg.file, page(cfg));
  console.log("WROTE " + cfg.file);
}
