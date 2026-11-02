// Cobrança automática da Strongest pelo WhatsApp (modelo "cobranca_strongest").
// Envia uma mensagem para cada recebível da Strongest VENCIDO e ainda não recebido:
//   - pvr_strongtx_*  taxas de entrega da semana (vencem na segunda)
//   - pvr_strong_*    mensalidade R$ 600 (vence dia 10)
// Uso: WA_TOKEN=... [XP_JSON=arquivo.json] node scripts/cobranca-strongest.js [AAAA-MM-DD] [destino] [--dry]
//   XP_JSON (opcional): transações recentes da XP (formato Pluggy: date, amount, description, type) para conferir
//   pagamentos que ainda não foram importados no index.html. O token NUNCA fica no repositório.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); }
catch (e) { ({ JSDOM, VirtualConsole } = require('/home/claude/node_modules/jsdom')); }

const args = process.argv.slice(2);
const DRY = args.includes('--dry');
const pos = args.filter(a => !a.startsWith('--'));
const HOJE = pos[0] || new Date(Date.now() - 3 * 3600e3).toISOString().slice(0, 10);
const DESTINO = pos[1] || '5584986327208';
const PHONE_ID = '1413683815156427';
const TOKEN = process.env.WA_TOKEN;
if (!TOKEN && !DRY) { console.error('WA_TOKEN não definido'); process.exit(2); }

// transações XP extras (UTC → Natal, UTC−3)
let extra = [];
if (process.env.XP_JSON && fs.existsSync(process.env.XP_JSON)) {
  let j = JSON.parse(fs.readFileSync(process.env.XP_JSON, 'utf8'));
  j = Array.isArray(j) ? j : (j.results || j.transactions || j.data || []);
  extra = j.map(t => {
    const d = new Date(new Date(t.date).getTime() - 3 * 3600e3).toISOString().slice(0, 10);
    const amt = Math.abs(+t.amount);
    const credit = t.type ? t.type === 'CREDIT' : +t.amount > 0;
    return { date: d, amt, credit, desc: String(t.description || '') + ' ' + String((t.paymentData && t.paymentData.payer && t.paymentData.payer.name) || '') };
  }).filter(t => t.credit && /STRONGEST/i.test(t.desc));
}

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://yampa-fin-2.vercel.app/', virtualConsole: new VirtualConsole(), pretendToBeVisual: true });

setTimeout(() => {
  const w = dom.window;
  const lista = JSON.parse(w.eval(`(function(HOJE){
    buildTX();
    return JSON.stringify(TX.filter(t=>t.status==='previsto' && /^pvr_strong(tx)?_/.test(t.id) && t.date<HOJE)
      .map(t=>({id:t.id, date:t.date, amt:t.amt, desc:t.desc, comp:t.comp})));
  })('${HOJE}')`));

  const addD = (s, n) => { const x = new Date(s + 'T12:00:00'); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); };
  const pago = p => /^pvr_strongtx_/.test(p.id)
    ? extra.some(x => Math.abs(x.amt - p.amt) < 0.01 && x.date >= addD(p.date, -1) && x.date <= addD(p.date, 6))
    : extra.some(x => x.amt >= 500 && x.date.slice(0, 7) === (p.comp || p.date.slice(0, 7)));
  const vencidos = lista.filter(p => !pago(p));

  const num = v => v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const br = s => s.split('-').reverse().join('/');
  const MES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const rotulo = p => {
    if (/^pvr_strongtx_/.test(p.id)) { const m = p.desc.match(/semana .*$/i); return 'Taxas de entrega - ' + (m ? m[0] : 'semana'); }
    const c = (p.comp || p.date.slice(0, 7)).split('-'); return `Mensalidade ${MES[+c[1] - 1]}/${c[0].slice(2)}`;
  };

  const res = [];
  for (const p of vencidos) {
    const params = [rotulo(p), num(p.amt), br(p.date)];
    if (DRY) { res.push({ id: p.id, params, enviado: false, dry: true }); continue; }
    const msg = { messaging_product: 'whatsapp', to: DESTINO, type: 'template', template: { name: 'cobranca_strongest', language: { code: 'pt_BR' },
      components: [{ type: 'body', parameters: params.map(t => ({ type: 'text', text: t })) }] } };
    const r = JSON.parse(execFileSync('curl', ['-sS', '-H', 'Authorization: Bearer ' + TOKEN, '-H', 'Content-Type: application/json', '-X', 'POST',
      `https://graph.facebook.com/v21.0/${PHONE_ID}/messages`, '--data-binary', JSON.stringify(msg)]).toString());
    res.push({ id: p.id, params, enviado: !!(r.messages && r.messages[0]), erro: r.error ? r.error.message : undefined });
  }
  console.log(JSON.stringify({ hoje: HOJE, destino: DESTINO, conferidosXP: extra.length, vencidos: vencidos.length, mensagens: res }, null, 1));
  process.exit(res.some(r => r.erro) ? 4 : 0);
}, 3000);
