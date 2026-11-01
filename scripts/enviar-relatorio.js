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

// Diárias dos entregadores (Cooperativa e Juninho) do dia anterior — scripts/entregadores.js
execFileSync('node', [path.join(__dirname, 'entregadores.js'), hoje, path.join(out, 'entregadores.json')], { stdio: ['ignore', 'ignore', 'inherit'], env: process.env });
const E = JSON.parse(fs.readFileSync(path.join(out, 'entregadores.json'), 'utf8'));
if (E.pendentes.length && !process.env.ENVIAR_SEM_PENDENTES) {
  // Felipe pediu: bairro fora da tabela → NÃO enviar; perguntar o valor antes
  console.log(JSON.stringify({ enviado: false, motivo: 'entregas sem taxa definida', diaEntregadores: E.dia, pendentes: E.pendentes, parcial: { cooperativa: E.cooperativa.total, juninho: E.juninho.total } }));
  process.exit(5);
}
{
  const f = v => 'R$ ' + v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const dE = s => ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'][new Date(s + 'T12:00:00').getDay()] + ' ' + s.split('-').reverse().join('/');
  const linha = r => `<tr><td>${r.nome}</td><td class="n">${r.idas.length}</td><td class="n">${f(r.taxas)}</td><td class="n">${r.encosta ? f(r.encosta) : '—'}</td><td class="n"><b>${f(r.total)}</b></td></tr>`;
  const sec = `<h2>🛵 Entregadores — ${dE(E.dia)}</h2><table><thead><tr><th>Entregador</th><th class="n">Idas</th><th class="n">Taxas</th><th class="n">Encosta</th><th class="n">Total</th></tr></thead><tbody>${linha(E.cooperativa)}${linha(E.juninho)}</tbody><tfoot><tr><td colspan=4>Total a pagar</td><td class="n">${f(E.total)}</td></tr></tfoot></table>`;
  const hp = path.join(out, 'relatorio.html');
  fs.writeFileSync(hp, fs.readFileSync(hp, 'utf8').replace('<div class="sub" style="margin-top:20px">', sec + '<div class="sub" style="margin-top:20px">'));
}
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
    nAtr === 0 ? 'nenhum boleto' : nAtr === 1 ? '1 boleto' : `${nAtr} boletos`, num(R.totSemana), `${num(R.precisaVender)} (média R$ ${num(R.mediaDia)}/dia em ${R.diasVenda} ${R.diasVenda === 1 ? 'dia' : 'dias'})`];
  const diaE = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'][new Date(E.dia + 'T12:00:00').getDay()] + ' ' + br(E.dia).slice(0, 5);
  const enviar = (nome, ps) => curl(['-X', 'POST', `https://graph.facebook.com/v21.0/${PHONE_ID}/messages`, '-H', 'Content-Type: application/json', '--data-binary', JSON.stringify(
    { messaging_product: 'whatsapp', to: DESTINO, type: 'template', template: { name: nome, language: { code: 'pt_BR' }, components: [
      { type: 'header', parameters: [{ type: 'document', document: { id: up.id, filename: `Pagamentos ${br(hoje).replace(/\//g, '-')}.pdf` } }] },
      { type: 'body', parameters: ps.map(t => ({ type: 'text', text: t })) }] } })]);
  // modelo com os entregadores; enquanto a Meta não aprovar, usa o antigo (os totais ficam no PDF)
  let modelo = 'previsao_pagamentos_entregas';
  let r = enviar(modelo, [...params, diaE, num(E.cooperativa.total), num(E.juninho.total)]);
  if (!r.messages) { modelo = 'previsao_pagamentos'; r = enviar(modelo, params); }
  console.log(JSON.stringify({ enviado: !!(r.messages && r.messages[0]), resposta: r, pdf, modelo, entregadores: { dia: E.dia, cooperativa: E.cooperativa.total, juninho: E.juninho.total }, resumo: { saldo: R.saldoTotal, atrasado: R.totAtras, semana: R.totSemana, vender: R.precisaVender } }));
  process.exit(r.messages ? 0 : 4);
})();
