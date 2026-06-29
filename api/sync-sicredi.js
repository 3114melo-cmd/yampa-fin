// api/sync-sicredi.js
// Vercel Serverless Function - Sincronizacao Sicredi via BANCO MCP
// Yampa Fin - Felipe Elias - CNPJ 38.364.354/0001-98

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', 'https://yampa-fin.vercel.app');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });

  const MCP_URL = 'https://api.mcp.ai/banco';
  const MCP_KEY = process.env.BANCO_MCP_KEY;

  if (!MCP_KEY) {
    return res.status(500).json({ error: 'BANCO_MCP_KEY nao configurada nas variaveis de ambiente do Vercel.' });
  }

  try {
    const hoje = new Date().toISOString().split('T')[0];
    const trintaDiasAtras = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];

    const conexoesRes = await fetch(MCP_URL + '/openfinance/connections', {
      headers: { 'Authorization': 'Bearer ' + MCP_KEY, 'Content-Type': 'application/json' }
    });
    const conexoes = await conexoesRes.json();

    const sicredi = conexoes?.items?.find(c =>
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
    const contaCorrente = contasData?.accounts?.find(c => c.type === 'BANK') || contasData?.accounts?.[0];

    let saldoAtual = 0;
    if (contaCorrente?.id) {
      const saldoRes = await fetch(MCP_URL + '/openfinance/accounts/' + contaCorrente.id + '/balance', {
        headers: { 'Authorization': 'Bearer ' + MCP_KEY }
      });
      const saldoData = await saldoRes.json();
      saldoAtual = saldoData?.balance ?? saldoData?.availableAmount ?? 0;
    }

    const txRes = await fetch(
      MCP_URL + '/openfinance/transactions?itemId=' + itemId + '&from=' + trintaDiasAtras + '&to=' + hoje + '&pageSize=300',
      { headers: { 'Authorization': 'Bearer ' + MCP_KEY } }
    );
    const txData = await txRes.json();
    const rawTxs = txData?.transactions ?? txData?.results ?? [];

    function detectarCategoria(desc, isCredito) {
      const d = (desc || '').toUpperCase();
      if (d.includes('DHFIT')) return { plano: '4.2.1 - Fornecedores', grupo: 'Custos com Fornecedores' };
      if (d.includes('DEB.CTA.FATURA') || d.includes('FATURA')) return { plano: '5.1.1 - Mensalidade Banco', grupo: 'Desp. Financeiras' };
      if (d.includes('COSERN') || d.includes('ENERGIA')) return { plano: '5.2.3 - Energia eletrica', grupo: 'Desp. Administrativas' };
      if (d.includes('POSTO') || d.includes('GASOLINA') || d.includes('COMBUSTIVEL')) return { plano: '4.6.1 - Gasolina', grupo: 'Custos com Motos' };
      if (d.includes('CAERN') || d.includes('AGUA')) return { plano: '5.2.17 - CAERN', grupo: 'Desp. Administrativas' };
      if (d.includes('META') || d.includes('GOOGLE') || d.includes('TRAFEGO')) return { plano: '5.8.1 - Trafego Pago', grupo: 'Trafego Pago' };
      if (d.includes('FOLHA') || d.includes('SALARIO')) return { plano: '5.3.1 - Salario', grupo: 'Desp. com Pessoal' };
      if (d.includes('ALUGUEL')) return { plano: '5.2.4 - Aluguel', grupo: 'Desp. Administrativas' };
      if (d.includes('CONTADOR') || d.includes('CONTABIL')) return { plano: '5.2.11 - Contador', grupo: 'Desp. Administrativas' };
      if (d.includes('FGTS')) return { plano: '5.3.5 - FGTS', grupo: 'Desp. com Pessoal' };
      if (d.includes('INSS') || d.includes('DARF')) return { plano: '5.3.6 - DARF', grupo: 'Desp. com Pessoal' };
      if (d.includes('EMPRESTIMO') || d.includes('PARCELA')) return { plano: '7.2.13 - Emprestimos', grupo: 'Nao Operacional' };
      return isCredito
        ? { plano: '3.1.8 - Receita de Vendas', grupo: 'Receitas' }
        : { plano: '5.2.99 - Outras desp. adm.', grupo: 'Desp. Administrativas' };
    }

    const transacoes = rawTxs.map((tx, idx) => {
      const isCredito = tx.type === 'CREDIT';
      const valor = parseFloat(tx.amount) || 0;
      const desc = tx.description || tx.name || '';
      const cat = detectarCategoria(desc, isCredito);
      const dataStr = (tx.date || tx.transactionDate || hoje).substring(0, 10);
      const [y, m, d] = dataStr.split('-');
      const matchNome = desc.match(/(?:PIX|PAGAMENTO|RECEBIMENTO)\s*[-]?\s*(.+)/i);
      return {
        id: String(idx + 1).padStart(3, '0'),
        data: d + '/' + m + '/' + y.substring(2),
        data_iso: dataStr,
        plano: cat.plano,
        grupo: cat.grupo,
        hist: desc,
        conta: 'Sicredi',
        entrada: isCredito ? Math.abs(valor) : null,
        saida: !isCredito ? Math.abs(valor) : null,
        status: 'realizado',
        conc: true,
        cli: matchNome ? matchNome[1].trim().substring(0, 40) : desc.substring(0, 40) || '-',
      };
    });

    const mesAtual = hoje.substring(0, 7);
    const txMes = transacoes.filter(t => t.data_iso.startsWith(mesAtual));
    const entradasMes = txMes.filter(t => t.entrada).reduce((s, t) => s + t.entrada, 0);
    const saidasMes = txMes.filter(t => t.saida).reduce((s, t) => s + t.saida, 0);
    const txHoje = transacoes.filter(t => t.data_iso === hoje);

    return res.status(200).json({
      ok: true,
      atualizado: new Date().toLocaleString('pt-BR', { timeZone: 'America/Recife' }),
      conta: {
        nome: 'Sicredi Empresas',
        cnpj: '38.364.354/0001-98',
        agencia: '2207',
        numero: contaCorrente?.number || '00047678-1',
        saldo: saldoAtual,
        entradas_mes: entradasMes,
        saidas_mes: saidasMes,
      },
      hoje: {
        data: hoje,
        total_lancamentos: txHoje.length,
        entradas: txHoje.filter(t => t.entrada).reduce((s, t) => s + t.entrada, 0),
        saidas: txHoje.filter(t => t.saida).reduce((s, t) => s + t.saida, 0),
        transacoes: txHoje,
      },
      transacoes,
      total: transacoes.length,
    });

  } catch (err) {
    console.error('Erro sync Sicredi:', err);
    return res.status(500).json({
      ok: false,
      error: err.message,
      detalhes: 'Verifique a variavel BANCO_MCP_KEY no Vercel.',
    });
  }
}
