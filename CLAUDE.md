# YampaFin — notas para sincronização

- Datas da Stone (Pluggy/Open Finance) chegam em UTC (`...Z`). Ao gravar no RAW, converter para horário de Natal (UTC−3): subtrair 3h de data+hora.
- Sistema começa em 01/10/2026; saldo inicial Stone em `saldo_inicial_stone` (cat 13.99, conciliado).
- Novas transações: inserir no fim do array `RAW` (não no `REGRAS_AUTO`, que também termina em `\n];`).
- Atualizar `SALDO_BANCO`, `HOJE_SISTEMA`, `const hoje`, `m-data` e o badge `Stone · dd/mm · HH:MM` a cada sync.
- Regras automáticas ficam em `REGRAS_AUTO` (competência = mês do pagamento; `compMesAnterior:true` para mês anterior).
- XP (Pluggy item 31a19bd9-7f6b-4900-bb76-05966952787a, conta 53ab59d6-1a06-42af-a499-0bc9bc5578a3): começa em 27/10/2026, saldo inicial `saldo_inicial_xp` (166,11). Transações com id `xp_` + 8 hex, `conta:'XP'`. Toda entrada XP concilia automática como 7.1.7 STRONG (taxas de entrega da loja de roupas); regras genéricas não se aplicam à XP. Atualizar `SALDO_BANCO.XP` a cada sync.
- Taxas de entrega da Strongest (semana seg–sáb): todo domingo entra uma previsão a receber na segunda, no fim do array `RAW`: `{id:'pvr_strongtx_AAAAMMDD',date:'AAAA-MM-DD' (segunda),hora:'--:--',desc:'Taxas de entrega STRONGEST - semana DD/MM a DD/MM',amt:<valor>,type:'CREDIT',conta:'XP',cat:'7.1.7',cli:'STRONGEST BRAZIL LTDA',meio:'PIX',previsto:true,comp:'AAAA-MM' (mês do sábado),obs:'...'}`. Baixa automática quando cair Pix da Strongest na XP com o mesmo valor (-1 a +6 dias). Não duplicar: se o id já existir, atualizar o valor.
- Comissão vendedores (1% das vendas do mês no Bling, pedidos situação 9 = atendido): Soares = vendedor 15596533676; Renato = 15596286962; Soldado = 15596555010 + 15596357569 (dois cadastros, somar). Gravar com `python3 scripts/comissao.py AAAA-MM SOARES=<vendas> RENATO=<vendas> SOLDADO=<vendas>` → previsão dia 15 do mês seguinte, cat 4.4.2, ids `pvcom_<nome>_<AAAAMM>` (atualiza se já existir).
