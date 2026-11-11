// Diárias dos entregadores terceirizados (MotoRun → relatório de pagamentos).
// Uso: node scripts/entregadores.js <AAAA-MM-DD do relatório> [saida.json] [--dia AAAA-MM-DD]
// Dia de referência = dia anterior ao relatório (na segunda, o sábado). --dia força um dia específico.
// Regras (Felipe, 31/10/2026):
//  - Cooperativa (motoId '4'): taxa por bairro (TABELA_COOP) + encosta R$ 30,00 nos dias com pelo menos 1 ida.
//  - Juninho, bike (motoId '2'): R$ 5,00 fixo por entrega.
//  - Produção (nome com PRODUÇÃO/PRODUCAO/PRDUCAO): R$ 5,00 por ida, para os dois.
//  - Só pedidos status 'entregue', fora das rotas loja/retirada. Pedido que diz "FAZ A ENTREGA" (outra pessoa entregou) não conta.
//  - Cada pedido entregue conta como uma entrega, mesmo com o mesmo endereço na mesma saída (Felipe, 31/10/2026).
//  - Bairro fora da tabela ou não identificado → vai em "pendentes" (não inventar valor: perguntar ao Felipe).
const fs = require('fs');
const { execFileSync } = require('child_process');

const ENCOSTA_COOP = 30, TAXA_BIKE = 5, TAXA_PRODUCAO = 5;
const ENTREGADORES = { '4': 'Cooperativa', '2': 'Juninho' };

// Tabela da Cooperativa (valor por entrega). Chaves sem acento, minúsculas.
const TABELA_COOP = {
  'regomoleiro': 4, 'santo antonio do potengi': 7, 'novo amarante': 4, 'amarante': 4, 'sao goncalo do amarante': 4,
  'golandim': 4, 'jardins': 7, 'jardim petropolis': 7, 'jardim lola': 4, 'jardim progresso': 7, 'plaza garden': 7,
  'igapo': 3, 'nossa senhora da apresentacao': 5, 'lagoa azul': 6, 'potengi': 5, 'pajucara': 7, 'sarney': 6,
  'cidade praia': 6, 'gramore': 7, 'extremoz': 10, 'redinha': 10, 'nova natal': 7, 'centro de sao goncalo': 10,
  'macaiba': 10, 'alecrim': 10, 'tirol': 10, 'mae luiza': 10, 'candelaria': 10, 'planalto': 10, 'lagoa nova': 10,
  'cidade nova': 10, 'neopolis': 10, 'nova descoberta': 10, 'cidade satelite': 10, 'capim macio': 12, 'ponta negra': 12,
  'nova parnamirim': 12, 'parnamirim': 12, 'parnamirim centro': 15, 'parque das nacoes': 15, 'vale do sol': 15,
  'dix-sept rosado': 10, 'flores do campo': 7, 'cidade das rosas': 7, 'nordelandia': 7, 'salinas': 5, 'vale dourado': 5,
  'parque dos coqueiros': 5, 'mae de damasio': 5, "olho d'agua": 7, 'quintas': 10, 'felipe camarao': 10, 'ribeira': 6,
  'conjunto brasil novo': 6, 'parque dos ipes': 6, 'cidade jardim': 8, 'santa catarina': 10, 'local fixo': 6,
  'academia vieira raca': 7,
  // confirmados pelo Felipe em 31/10/2026
  'petropolis': 10, 'rocas': 10,
  // confirmado pelo Felipe em 10/11/2026
  'canaa': 4,
};
// Grafias alternativas → bairro da tabela
const ALIAS = {
  'sao antonio do potengi': 'santo antonio do potengi', 'nossa sra. da apresentacao': 'nossa senhora da apresentacao',
  'nossa sra da apresentacao': 'nossa senhora da apresentacao', 'n. sra. da apresentacao': 'nossa senhora da apresentacao',
  'jardins de extremoz': 'extremoz', 'centro de parnamirim': 'parnamirim centro', 'dix sept rosado': 'dix-sept rosado',
  'olho dagua': "olho d'agua", 'conj. brasil novo': 'conjunto brasil novo', 'cidade das flores': 'flores do campo',
};
const BUSCA_LIVRE = ['canaa'];
const CIDADES = ['sao goncalo do amarante', 'natal', 'parnamirim', 'extremoz', 'macaiba'];

const norm = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
const CHAVES = [...Object.keys(TABELA_COOP), ...Object.keys(ALIAS)].sort((a, b) => b.length - a.length);
const resolve = k => ALIAS[k] || k;

function bairroDe(end) {
  const e = norm(end);
  // Formato Google: "Rua, nº - Bairro, Cidade - RN, CEP": olhar o trecho logo após o primeiro " - "
  const partes = e.split(' - ');
  if (partes.length >= 2) {
    let seg = partes[1].split(',')[0].trim();
    for (const c of CIDADES) if (seg.length > c.length && seg.endsWith(c)) seg = seg.slice(0, -c.length).trim(); // "pajucaranatal"
    let k = CHAVES.find(k => seg === k) || CHAVES.find(k => seg.startsWith(k + ' ') && !TABELA_COOP[seg]);
    if (!k && seg && !/^\d|rn$|^cep/.test(seg)) {
      // trecho não é bairro da tabela (ex.: "parque das dunas") → pendente, a não ser que seja só a cidade
      if (!CIDADES.includes(seg)) return { k: null, seg };
    }
    if (k) return { k: resolve(k) };
  }
  // Formato "Rua, nº, Bairro, Cidade - RN": algum trecho entre vírgulas (exceto o 1º, que é a rua) igual a um bairro da tabela;
  // bairro tem preferência sobre cidade
  const trechos = e.split(/,| - /).slice(1).map(s => s.trim()).filter(Boolean);
  const achados = trechos.map(s => CHAVES.find(k => s === k)).filter(Boolean).map(resolve);
  const bairro = achados.find(k => !CIDADES.includes(k)) || achados[0];
  if (bairro) return { k: bairro };
  // Endereço digitado à mão sem vírgulas (ex.: "Rua Antúrio 04 Canaã São Gonçalo"): só bairros de nome único, que não
  // aparecem como nome de rua, podem ser achados soltos no texto
  const livre = BUSCA_LIVRE.find(k => new RegExp('(^|[^a-z])' + k + '([^a-z]|$)').test(e));
  if (livre) return { k: livre };
  // Sem formato Google: só aceita se o texto for uma cidade da tabela sem nada mais (ex.: "EXTREMOZ")
  const so = e.replace(/[^a-z' -]/g, '').replace(/ - rn$/, '').trim();
  if (TABELA_COOP[resolve(so)] !== undefined) return { k: resolve(so) };
  return { k: null, seg: '' };
}

function calcular(pedidos, dia, extra = {}) {  // extra = {idPedido: taxa} para pendentes já respondidos pelo Felipe
  const inicio = new Date(dia + 'T00:00:00-03:00').getTime(), fim = inicio + 864e5;
  const res = {}, pendentes = [], fora = [];
  for (const id of Object.keys(ENTREGADORES)) res[id] = { nome: ENTREGADORES[id], idas: [], taxas: 0, encosta: 0, total: 0 };
  const lista = Object.values(pedidos || {}).filter(p => p && p.status === 'entregue' && ENTREGADORES[p.motoId] &&
    p.tsEntregue >= inicio && p.tsEntregue < fim && !['loja', 'retirada'].includes(String(p.rota || '').toLowerCase()))
    .sort((a, b) => (a.tsSaiu || a.tsEntregue) - (b.tsSaiu || b.tsEntregue));
  for (const p of lista) {
    const txt = norm(p.nome + ' ' + p.end + ' ' + (p.obs || ''));
    if (/faz a entrega/.test(norm(p.end))) { fora.push({ entregador: ENTREGADORES[p.motoId], cliente: p.nome, end: p.end, motivo: 'outra pessoa fez a entrega' }); continue; }
    const r = res[p.motoId];
    const producao = /pr[o]?ducao/.test(norm(p.nome));
    let taxa, bairro;
    if (producao) { taxa = TAXA_PRODUCAO; bairro = 'Produção'; }
    else if (p.motoId === '2') { taxa = TAXA_BIKE; bairro = ''; }
    else {
      const b = bairroDe(p.end);
      if (!b.k && extra[p.id] !== undefined) { r.idas.push({ id: p.id, cliente: p.nome, bairro: 'informado', taxa: +extra[p.id], hora: new Date(p.tsEntregue - 3 * 3600e3).toISOString().slice(11, 16) }); continue; }
      if (!b.k) { pendentes.push({ entregador: r.nome, id: p.id, cliente: p.nome, end: String(p.end || '').replace(/\s+/g, ' '), obs: String(p.obs || '').replace(/\s+/g, ' ').trim(), trecho: b.seg || '' }); continue; }
      taxa = TABELA_COOP[b.k]; bairro = b.k;
    }
    r.idas.push({ id: p.id, cliente: p.nome, bairro, taxa, hora: new Date(p.tsEntregue - 3 * 3600e3).toISOString().slice(11, 16) });
  }
  for (const id of Object.keys(res)) {
    const r = res[id];
    r.taxas = r.idas.reduce((s, x) => s + x.taxa, 0);
    const temIda = r.idas.length > 0 || pendentes.some(p => p.entregador === r.nome);
    r.encosta = id === '4' && temIda ? ENCOSTA_COOP : 0;
    r.total = +(r.taxas + r.encosta).toFixed(2);
  }
  return { dia, cooperativa: res['4'], juninho: res['2'], pendentes, fora, total: +(res['4'].total + res['2'].total).toFixed(2) };
}

function diaAnterior(hoje) {
  const d = new Date(hoje + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() - (d.getUTCDay() === 1 ? 2 : 1)); // segunda → sábado
  return d.toISOString().slice(0, 10);
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const iDia = args.indexOf('--dia');
  const forcado = iDia >= 0 ? args.splice(iDia, 2)[1] : null;
  const hoje = args[0] || new Date(Date.now() - 3 * 3600e3).toISOString().slice(0, 10);
  const dia = forcado || diaAnterior(hoje);
  const ini = new Date(dia + 'T00:00:00-03:00').getTime();
  const url = `https://delivery-dhfit-4a399-default-rtdb.firebaseio.com/pedidos.json?orderBy=%22tsEntregue%22&startAt=${ini}&endAt=${ini + 864e5}`;
  let pedidos = JSON.parse(execFileSync('curl', ['-sS', '--max-time', '60', url], { maxBuffer: 1 << 28 }).toString());
  if (pedidos && pedidos.error) // sem índice em tsEntregue: baixa tudo
    pedidos = JSON.parse(execFileSync('curl', ['-sS', '--max-time', '120', 'https://delivery-dhfit-4a399-default-rtdb.firebaseio.com/pedidos.json'], { maxBuffer: 1 << 28 }).toString());
  const R = calcular(pedidos, dia, process.env.ENTREG_EXTRA ? JSON.parse(process.env.ENTREG_EXTRA) : {});
  if (args[1]) fs.writeFileSync(args[1], JSON.stringify(R, null, 1));
  console.log(JSON.stringify(R));
}
module.exports = { calcular, diaAnterior, bairroDe };
