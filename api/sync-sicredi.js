// api/sync-sicredi.js
// Vercel Serverless Function - Sincronizacao Sicredi via BANCO MCP
// Yampa Fin - Felipe Elias - CNPJ 38.364.354/0001-98

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', 'https://yampa-fin.vercel.app');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });

  const MCP_URL = 'https://api.mcp.ai/banco';
  const MCP_KEY = process.env.BANCO_MCP_KEY;

  if (!MCP_KEY) {
    return res.status(500).json({ error: 'BANCO_MCP_KEY nao configurado nas variaveis de ambiente do Vercel.' });
  }

  try {
    const hoje = new Date().toISOString().split('T')[0];
    const trintaDiasAtras = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];

    const conexoesRes = await fetch(MCP_URL + '/openfinance/connections', {
      headers: { 'Authorization': 'Bearer ' + MCP_KEY, 'Content-Type': 'application/json' }
    });
    const conexoes = await conexoesRes.json();

    const sicredi = conexoes?.data?.find(c =>
      c.connector?.name?.toLowerCase().includes('sicredi') ||
      c.item_id === process.env.SICREDI_ITEM_ID
    );

    if (!sicredi) {
      return res.status(404).json({ error: 'Conexao Sicredi nao encontrada no BANCO MCP.' });
    }

    const itemId = sicredi.item_id;

    const contasRes = await fetch(MCP_URL + '/openfinance/accounts?itemId=' + itemId, {
      headers: { 'Authorization': 'Bearer ' + MCP_KEY }
    });
    const contasData = await contasRes.json();
    const contaCorrente = contasData?.accounts?.find(c => c.type === 'CHECKING') || contasData?.accounts?.[0];

    let saldoReal = 0;
    if (contaCorrente?.id) {
      const saldoRes = await fetch(MCP_URL + '/openfinance/accounts/' + contaCorrente.id + '/balance', {
        headers: { 'Authorization': 'Bearer ' + MCP_KEY }
      });
      const saldoData = await saldoRes.json();
      saldoReal = saldoData?.balance ?? saldoData?.available_amount ?? 0;
    }

    const txRes = await fetch(
      MCP_URL + '/openfinance/transactions?itemId=' + itemId + '&from=' + trintaDiasAtras + '&to=' + hoje + '&pageSize=300',
      { headers: { 'Authorization': 'Bearer ' + MCP_KEY } }
    );
    const txData = await txRes.json();

    const transacoes = (txData?.transactions || txData?.data || []).map(tx => ({
      id: tx.id,
      data: tx.date,
      descricao: tx.description || tx.name || 'Transacao',
      valor: tx.amount,
      tipo: tx.amount >= 0 ? 'entrada' : 'saida',
      categoria: tx.category || 'Outros',
      saldo: tx.balance
    }));

    const totalEntradas = transacoes
      .filter(t => t.tipo === 'entrada')
      .reduce((sum, t) => sum + Math.abs(t.valor), 0);

    const totalSaidas = transacoes
      .filter(t => t.tipo === 'saida')
      .reduce((sum, t) => sum + Math.abs(t.valor), 0);

    return res.status(200).json({
      sucesso: true,
      ultimaAtualizacao: new Date().toISOString(),
      saldo: saldoReal,
      totalEntradas,
      totalSaidas,
      transacoes
    });

  } catch (err) {
    console.error('Erro sync-sicredi:', err);
    return res.status(500).json({
      error: 'Erro ao sincronizar com BANCO MCP',
      detalhes: err.message
    });
  }
};
