# YampaFin — notas para sincronização

- Datas da Stone (Pluggy/Open Finance) chegam em UTC (`...Z`). Ao gravar no RAW, converter para horário de Natal (UTC−3): subtrair 3h de data+hora.
- Sistema começa em 01/10/2026; saldo inicial Stone em `saldo_inicial_stone` (cat 13.99, conciliado).
- Novas transações: inserir no fim do array `RAW` (não no `REGRAS_AUTO`, que também termina em `\n];`).
- Atualizar `SALDO_BANCO`, `HOJE_SISTEMA`, `const hoje`, `m-data` e o badge `Stone · dd/mm · HH:MM` a cada sync.
- Regras automáticas ficam em `REGRAS_AUTO` (competência = mês do pagamento; `compMesAnterior:true` para mês anterior).
