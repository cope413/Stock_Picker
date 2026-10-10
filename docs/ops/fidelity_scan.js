// Fidelity info-tab scan extractor (read-only). Run in Claude in Chrome's javascript_tool on
//   https://digital.fidelity.com/prgw/digital/research/quote/dashboard/view-all?symbol=TICKER
// Store it once per session:  sessionStorage.setItem('scanfn', <this file's text>)
// then per ticker, after navigating:  await eval(sessionStorage.getItem('scanfn'))
// Output is about 800 characters per ticker in the format `landry fidscan add` parses. The tool caps output near
// 1,000 characters and blocks text containing = & ?, so those are stripped. See docs/ops/process-and-dca.md.
(async () => {
  const root = () => document.querySelector('main') || document.body;
  for (let k = 0; k < 24; k++) {
    const t = root().innerText;
    if (t.includes('Key statistics for') && t.includes('Opinion date')) break;
    window.scrollTo(0, document.body.scrollHeight * (k % 4) / 3);
    await new Promise(r => setTimeout(r, 700));
  }
  const L = root().innerText.split(/\n+/).map(x => x.trim().replace(/\t+/g, ' ~ '))
    .filter(x => x && !/Opens in a new tab|^As of|^Provided/.test(x));
  const ix = k => L.findIndex(l => l.startsWith(k));
  const nx = (k, n = 1, from = 0) => {
    const i = L.findIndex((l, j) => j >= from && l.startsWith(k));
    return i < 0 ? 'NA' : L.slice(i + 1, i + 1 + n).join(' ');
  };
  // Fidelity shows no multi-period block for some names (ASML, TSM, PLD on 10/10/26): those print NA.
  const pp = L.indexOf('Price performance');
  const perf = ['1-month', '3-month', '6-month', 'YTD', '1-year', '2-year', '5-year']
    .map(k => k.replace('-month', 'm').replace('-year', 'y') + ' ' + (pp < 0 ? 'NA' : nx(k, 1, pp))).join(', ');
  const ev = ix('Upcoming events');
  const evt = ev < 0 ? 'NA' : L.slice(ev + 1, ev + 4).join(' ');
  const beats = L.filter(l => /^(Beat|Missed|Met)/.test(l)).join('; ');   // oldest quarter first
  const fa = ix('Fundamental analysis');
  const f = k => { const i = L.findIndex((l, j) => j > fa && l === k); return k + ' ' + (i < 0 ? 'NA' : L[i + 2]); };
  const ar = ix('Analyst ratings');
  const ess = ar < 0 ? 'NA' : L.slice(ar + 2, ar + 4).join(' ');
  const o1 = ix('Opinion date'), o2 = ix('Explore all research firms');
  const ops = [];
  if (o1 >= 0 && o2 > o1) {
    const S = L.slice(o1 + 1, o2);
    for (let i = 0; i + 3 < S.length; i += 4)
      ops.push(S[i].replace(/ \(i\)|, Inc|Research|Investment|Capital Management/g, '').trim().slice(0, 16)
        + ':' + S[i + 2] + '(' + S[i + 1] + ')');
  }
  const cmp = L.find(l => l.startsWith("P/E (This year's")) || 'NA';
  const ks = ['PE (TTM)', 'Gross margin', 'Return on equity', 'Long term debt/equity'].map(k => {
    const i = ix(k);
    return k.slice(0, 6) + ' ' + (i < 0 ? 'NA' : L.slice(i, i + 3).join(' ').replace(k, '').replace('(TTM)', ''));
  }).join('; ');
  return [
    document.title.split(' ')[0],
    'PERF ' + perf,
    'EVT ' + evt,
    'EPS ' + beats,
    'PE ttm ' + nx('P/E (TTM)') + '; 5y ' + nx('P/E (5-year avg)') + '; fwd(co~ind~peer) ' + cmp.replace(/^.*?~ /, ''),
    'EPSg ttm ' + nx('EPS growth (TTM vs prior TTM)') + '; qtr ' + nx('EPS growth (last qtr'),
    'FA ' + [f('Valuation'), f('Quality'), f('Growth Stability'), f('Financial Health')].join(', '),
    'ESS ' + ess,
    'OPS ' + ops.join(', '),
    'KS ' + ks,
  ].join('\n').replace(/[=&?]/g, ' ');
})()
