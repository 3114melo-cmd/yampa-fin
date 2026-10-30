// Gera o PDF do relatório (scripts/relatorio.js) e envia pelo WhatsApp Cloud API (modelo previsao_pagamentos).
// Uso: WA_TOKEN=... [SALDO_STONE=..] [SALDO_XP=..] node scripts/enviar-relatorio.js [AAAA-MM-DD] [destino]
// O token NUNCA fica no repositório: vem da variável WA_TOKEN.
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PHONE_ID = '1413683815156427';            // número remetente (app Avisos DHFIT)
const DESTINO = process.argv[3] || '5584986327208';
const TOKEN = process.env.WA_TOKEN;
if (!TOKEN) { console.error('WA_TOKEN não definido'); process.exit(2); }
const hoje = process.argv[2] || new Date(Date.now() - 3 * 3600e3).toISOString().slice(0, 10);
const out = fs.mkdtempSync(path.join(os.tmpdir(), 'rel-'));

execFileSync('node', [path.join(__dirname, 'relatorio.js'), hoje, out], { stdio: 'inherit', env: process.env });
const R = JSON.parse(fs.readFileSync(path.join(out, 'relatorio.json'), 'utf8'));
const num = v => v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const br = s => s.split('-').reverse().join('/');
const dia = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'][new Date(hoje + 'T12:00:00').getDay()];

(async () => {
  let pw; try { pw = require(require.resolve('playwright', { paths: [os.tmpdir(), '/opt/npm-tools'] })); } catch (e) { pw = require('playwright'); }
  const { chromium } = pw;
  const b = await chromium.launch(); const p = await b.newPage();
  await p.goto('file://' + path.join(out, 'relatorio.html'));
  const pdf = path.join(out, 'relatorio.pdf');
  await p.pdf({ path: pdf, format: 'A4', printBackground: true }); await b.close();

  // chamadas HTTP via curl (respeita o proxy do ambiente)
  const curl = args => JSON.parse(execFileSync('curl', ['-sS', '-H', 'Authorization: Bearer ' + TOKEN, ...args]).toString());
  const up = curl(['-X', 'POST', `https://graph.facebook.com/v21.0/${PHONE_ID}/media`, '-F', 'messaging_product=whatsapp', '-F', 'type=application/pdf', '-F', `file=@${pdf};type=application/pdf;filename=relatorio.pdf`]);
  if (!up.id) { console.error('upload falhou', JSON.stringify(up)); process.exit(3); }

  const nAtr = R.atrasados.length;
  const params = [`${dia} ${br(hoje).slice(0, 5)}`, num(R.saldoTotal), num(R.totAtras),
    nAtr === 0 ? 'nenhum boleto' : nAtr === 1 ? '1 boleto' : `${nAtr} boletos`, num(R.totSemana), num(R.precisaVender)];
  const msg = { messaging_product: 'whatsapp', to: DESTINO, type: 'template', template: { name: 'previsao_pagamentos', language: { code: 'pt_BR' }, components: [
    { type: 'header', parameters: [{ type: 'document', document: { id: up.id, filename: `Pagamentos ${br(hoje).replace(/\//g, '-')}.pdf` } }] },
    { type: 'body', parameters: params.map(t => ({ type: 'text', text: t })) }] } };
  const r = curl(['-X', 'POST', `https://graph.facebook.com/v21.0/${PHONE_ID}/messages`, '-H', 'Content-Type: application/json', '--data-binary', JSON.stringify(msg)]);
  console.log(JSON.stringify({ enviado: !!(r.messages && r.messages[0]), resposta: r, pdf, resumo: { saldo: R.saldoTotal, atrasado: R.totAtras, semana: R.totSemana, vender: R.precisaVender } }));
  process.exit(r.messages ? 0 : 4);
})();
