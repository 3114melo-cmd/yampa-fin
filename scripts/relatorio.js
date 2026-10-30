// Relatório diário de pagamentos do YampaFin (enviado por WhatsApp seg–sex 8h).
// Uso: node scripts/relatorio.js [AAAA-MM-DD] [saidaDir]
// Gera <saidaDir>/relatorio.json e <saidaDir>/relatorio.html a partir do index.html.
const fs = require('fs');
const path = require('path');
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); }
catch (e) { ({ JSDOM, VirtualConsole } = require('/home/claude/node_modules/jsdom')); }

const hojeArg = process.argv[2] || new Date(Date.now() - 3 * 3600e3).toISOString().slice(0, 10);
const outDir = process.argv[3] || '.';
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const vc = new VirtualConsole();
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://yampa-fin-2.vercel.app/', virtualConsole: vc, pretendToBeVisual: true });

setTimeout(() => {
  const w = dom.window;
  w.ENV_STONE = process.env.SALDO_STONE ? +process.env.SALDO_STONE : null;
  w.ENV_XP = process.env.SALDO_XP ? +process.env.SALDO_XP : null;
  const R = JSON.parse(w.eval(`(function(HOJE){
    buildTX();
    const d0 = new Date(HOJE+'T12:00:00');
    const dow = (d0.getDay()+6)%7;                       // 0 = segunda
    const iso = d => d.toISOString().slice(0,10);
    const seg = new Date(d0); seg.setDate(d0.getDate()-dow);
    const dom = new Date(seg); dom.setDate(seg.getDate()+6);
    const SEG = iso(seg), DOM = iso(dom);
    const PROT = (typeof PROTESTO_DIAS!=='undefined') ? PROTESTO_DIAS : {};
    const nomeForn = t => (t.cli||t.desc||'').trim();
    const protDias = t => { const k=Object.keys(PROT).find(k=>(nomeForn(t)+' '+(t.desc||'')).toUpperCase().includes(k.toUpperCase())); return k!==undefined?PROT[k]:null; };
    const addD = (s,n) => { const x=new Date(s+'T12:00:00'); x.setDate(x.getDate()+n); return iso(x); };
    const diasEntre = (a,b) => Math.round((new Date(b+'T12:00:00')-new Date(a+'T12:00:00'))/864e5);
    const prev = TX.filter(t=>t.status==='previsto' && t.type==='DEBIT' && t.amt>0.004);
    const atras = prev.filter(t=>t.date<HOJE).sort((a,b)=>a.date.localeCompare(b.date)).map(t=>{
      const pd=protDias(t); return {data:t.date, forn:nomeForn(t), desc:t.desc, valor:+t.amt.toFixed(2), cat:t.cat,
        diasAtraso:diasEntre(t.date,HOJE), protestoDias:pd, pagarAte: pd!=null? addD(t.date,pd) : null};
    });
    const semana = prev.filter(t=>t.date>=HOJE && t.date<=DOM).sort((a,b)=>a.date.localeCompare(b.date)).map(t=>({data:t.date, forn:nomeForn(t), desc:t.desc, valor:+t.amt.toFixed(2), cat:t.cat}));
    const receber = TX.filter(t=>t.status==='previsto' && t.type==='CREDIT' && t.date>=HOJE && t.date<=DOM).map(t=>({data:t.date, desc:t.desc, valor:+t.amt.toFixed(2)}));
    const saldo = c => TX.filter(t=>t.conta===c&&(t.status==='conciliado'||t.status==='transferencia')).reduce((s,t)=>s+(t.type==='CREDIT'?t.amt:-t.amt),0);
    // saldo: banco (env SALDO_STONE/SALDO_XP > SALDO_BANCO do app); Caixa pelo app
    const SB = (typeof SALDO_BANCO!=='undefined') ? SALDO_BANCO : {};
    const saldos = {Stone: ENV_STONE!==null ? ENV_STONE : (SB.Stone? SB.Stone.valor : +saldo('Stone').toFixed(2)),
                    XP:    ENV_XP!==null    ? ENV_XP    : (SB.XP? SB.XP.valor : +saldo('XP').toFixed(2)),
                    Caixa: +saldo('Caixa (Dinheiro)').toFixed(2),
                    fonte: ENV_STONE!==null ? 'banco (agora)' : ('banco em '+(SB.Stone?SB.Stone.em:'?'))};
    const sum = a => +a.reduce((s,x)=>s+x.valor,0).toFixed(2);
    const totAtras=sum(atras), totSemana=sum(semana), totReceber=sum(receber);
    const saldoTotal = +(saldos.Stone+saldos.XP+saldos.Caixa).toFixed(2);
    const precisaVender = Math.max(0, +(totAtras+totSemana-saldoTotal-totReceber).toFixed(2));
    const diasVenda = Math.max(1, 6 - dow);              // seg=6 … sáb=1 (dias de venda restantes até sábado, contando hoje)
    const mediaDia = +(precisaVender/diasVenda).toFixed(2);
    return JSON.stringify({diasVenda, mediaDia, hoje:HOJE, semana:{de:SEG, ate:DOM}, saldos, saldoTotal, atrasados:atras, totAtras, aPagar:semana, totSemana, aReceber:receber, totReceber, precisaVender});
  })('${hojeArg}')`));

  const fmt = v => 'R$ ' + v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const br = s => s ? s.split('-').reverse().join('/') : '';
  const diaSem = s => ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'][new Date(s + 'T12:00:00').getDay()];
  const linhasAtr = R.atrasados.map(a => `<tr><td>${br(a.data)}</td><td>${a.forn}<div class="d">${a.desc}</div></td><td class="n">${a.diasAtraso}d</td><td>${a.pagarAte ? '<b>' + br(a.pagarAte) + '</b>' : '<span class="m">prazo a informar</span>'}</td><td class="n">${fmt(a.valor)}</td></tr>`).join('');
  const linhasSem = R.aPagar.map(a => `<tr><td>${diaSem(a.data)} ${br(a.data)}</td><td>${a.forn}<div class="d">${a.desc}</div></td><td class="n">${fmt(a.valor)}</td></tr>`).join('');
  const out = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Pagamentos ${br(R.hoje)}</title><style>
body{font-family:Arial,Helvetica,sans-serif;color:#1f2430;margin:28px;font-size:12px}h1{font-size:19px;margin:0}h2{font-size:14px;margin:22px 0 6px;border-bottom:2px solid #e5e7eb;padding-bottom:4px}
.sub{color:#6b7280;margin:4px 0 14px}.cards{display:flex;gap:10px;flex-wrap:wrap}.c{flex:1;min-width:110px;border:1px solid #e5e7eb;border-radius:8px;padding:10px}.c .l{color:#6b7280;font-size:11px}.c .v{font-size:16px;font-weight:700;margin-top:3px}
.red{color:#b91c1c}.grn{color:#15803d}table{width:100%;border-collapse:collapse}td,th{padding:6px 5px;border-bottom:1px solid #f0f0f0;text-align:left;vertical-align:top}th{font-size:11px;color:#6b7280}.n{text-align:right;white-space:nowrap}.d{color:#9ca3af;font-size:10px}.m{color:#b45309}tfoot td{font-weight:700}
</style></head><body><h1>YampaFin · Previsão de pagamentos</h1><div class="sub">${diaSem(R.hoje)} ${br(R.hoje)} · semana ${br(R.semana.de)} a ${br(R.semana.ate)}</div>
<div class="cards"><div class="c"><div class="l">Saldo total</div><div class="v">${fmt(R.saldoTotal)}</div><div class="d">Stone ${fmt(R.saldos.Stone)} · XP ${fmt(R.saldos.XP)} · Caixa ${fmt(R.saldos.Caixa)}</div></div>
<div class="c"><div class="l">Atrasado</div><div class="v red">${fmt(R.totAtras)}</div></div><div class="c"><div class="l">A pagar até domingo</div><div class="v">${fmt(R.totSemana)}</div></div>
<div class="c"><div class="l">Precisa vender</div><div class="v ${R.precisaVender > 0 ? 'red' : 'grn'}">${fmt(R.precisaVender)}</div><div class="d">atrasado + semana − saldo${R.totReceber ? ' − a receber ' + fmt(R.totReceber) : ''}</div></div>
<div class="c"><div class="l">Média por dia</div><div class="v ${R.mediaDia > 0 ? 'red' : 'grn'}">${fmt(R.mediaDia)}</div><div class="d">${R.diasVenda} ${R.diasVenda === 1 ? 'dia' : 'dias'} de venda até sábado</div></div></div>
<h2>⚠️ Atrasados (${R.atrasados.length})</h2><table><thead><tr><th>Venc.</th><th>Fornecedor</th><th class="n">Atraso</th><th>Pagar até (cartório)</th><th class="n">Valor</th></tr></thead><tbody>${linhasAtr || '<tr><td colspan=5>Nada atrasado.</td></tr>'}</tbody><tfoot><tr><td colspan=4>Total atrasado</td><td class="n">${fmt(R.totAtras)}</td></tr></tfoot></table>
<h2>📅 A pagar de hoje até domingo (${R.aPagar.length})</h2><table><thead><tr><th>Dia</th><th>Descrição</th><th class="n">Valor</th></tr></thead><tbody>${linhasSem || '<tr><td colspan=3>Nada previsto.</td></tr>'}</tbody><tfoot><tr><td colspan=2>Total da semana</td><td class="n">${fmt(R.totSemana)}</td></tr></tfoot></table>
${R.aReceber.length ? '<h2>💰 A receber na semana</h2><table><tbody>' + R.aReceber.map(a => `<tr><td>${br(a.data)}</td><td>${a.desc}</td><td class="n">${fmt(a.valor)}</td></tr>`).join('') + '</tbody></table>' : ''}
<div class="sub" style="margin-top:20px">Saldos Stone e XP: ${R.saldos.fonte}. Caixa: saldo do app.</div></body></html>`;
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'relatorio.json'), JSON.stringify(R, null, 1));
  fs.writeFileSync(path.join(outDir, 'relatorio.html'), out);
  console.log(JSON.stringify({ hoje: R.hoje, saldoTotal: R.saldoTotal, totAtras: R.totAtras, nAtras: R.atrasados.length, totSemana: R.totSemana, nSemana: R.aPagar.length, totReceber: R.totReceber, precisaVender: R.precisaVender, mediaDia: R.mediaDia, diasVenda: R.diasVenda }));
  process.exit(0);
}, 3000);
