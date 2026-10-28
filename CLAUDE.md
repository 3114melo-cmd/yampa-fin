# YampaFin — notas para sincronização

- Datas da Stone (Pluggy/Open Finance) chegam em UTC (`...Z`). Ao gravar no RAW, converter para horário de Natal (UTC−3): subtrair 3h de data+hora.
- Sistema começa em 01/10/2026; saldo inicial Stone em `saldo_inicial_stone` (cat 13.99, conciliado).
- Novas transações: inserir no fim do array `RAW` (não no `REGRAS_AUTO`, que também termina em `\n];`).
- Atualizar `SALDO_BANCO`, `HOJE_SISTEMA`, `const hoje`, `m-data` e o badge `Stone · dd/mm · HH:MM` a cada sync.
- Regras automáticas ficam em `REGRAS_AUTO` (competência = mês do pagamento; `compMesAnterior:true` para mês anterior).
- XP (Pluggy item 31a19bd9-7f6b-4900-bb76-05966952787a, conta 53ab59d6-1a06-42af-a499-0bc9bc5578a3): começa em 27/10/2026, saldo inicial `saldo_inicial_xp` (166,11). Transações com id `xp_` + 8 hex, `conta:'XP'`. Toda entrada XP concilia automática como 7.1.7 STRONG (taxas de entrega da loja de roupas); regras genéricas não se aplicam à XP. Atualizar `SALDO_BANCO.XP` a cada sync.
