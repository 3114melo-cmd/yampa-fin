// Valida o index.html do YampaFin depois de um sync.
// Uso: node scripts/validar.js
// Saída JSON: erros de script, telas com NaN/undefined, diferença app × banco (Stone e XP) e pendências a partir de 01/11/2026.
const fs = require('fs');
const path = require('path');
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); }
catch (e) { ({ JSDOM, VirtualConsole } = require('/home/claude/node_modules/jsdom')); }
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const erros = []; const vc = new VirtualConsole(); vc.on('jsdomError', e => erros.push(e.message));
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://yampa-fin-2.vercel.app/', virtualConsole: vc, pretendToBeVisual: true });
setTimeout(() => {
  const w = dom.window;
  const R = JSON.parse(w.eval(`(function(){buildTX();
    const sal = c => TX.filter(t=>t.conta===c && (t.status==='conciliado'||t.status==='transferencia'||t.status==='pendente')).reduce((s,t)=>s+(t.type==='CREDIT'?t.amt:-t.amt),0);
    const pend = TX.filter(t=>t.status==='pendente').sort((a,b)=>a.date.localeCompare(b.date));
    return JSON.stringify({
      stone:{app:+sal('Stone').toFixed(2), banco:SALDO_BANCO.Stone.valor, dif:+(sal('Stone')-SALDO_BANCO.Stone.valor).toFixed(2)},
      xp:{app:+sal('XP').toFixed(2), banco:SALDO_BANCO.XP.valor, dif:+(sal('XP')-SALDO_BANCO.XP.valor).toFixed(2)},
      pendentes: pend.map(t=>({id:t.id,conta:t.conta,date:t.date,hora:t.hora,type:t.type,amt:t.amt,cat:t.cat,desc:t.desc,cli:t.cli}))
    });})()`));
  R.telasComErro = [];
  for (const p of ['dash', 'conc', 'jour', 'cf', 'forn', 'cad', 'metas', 'dre']) {
    try { w.eval(`go('${p}',null)`); if (/NaN|undefined/.test(w.document.getElementById('ct').textContent)) R.telasComErro.push(p); }
    catch (e) { R.telasComErro.push(p + ': ' + e.message); }
  }
  R.erros = erros;
  R.ok = !erros.length && !R.telasComErro.length;
  console.log(JSON.stringify(R, null, 1));
  process.exit(R.ok ? 0 : 1);
}, 3000);
