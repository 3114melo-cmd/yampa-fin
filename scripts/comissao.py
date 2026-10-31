#!/usr/bin/env python3
"""Grava/atualiza no index.html as previsões de comissão dos vendedores (1% das vendas do mês).
Uso: python3 scripts/comissao.py AAAA-MM SOARES=55507.31 RENATO=51302.02 SOLDADO=56039.48
  AAAA-MM = mês das vendas (competência). A previsão é criada para o dia 15 do mês seguinte, cat 4.4.2.
  Ids: pvcom_<nome>_<AAAAMM> — se já existir, o valor é atualizado (não duplica)."""
import sys,re,os
mes=sys.argv[1]; y,m=map(int,mes.split('-')); ny,nm=(y+1,1) if m==12 else (y,m+1)
venc=f"{ny:04d}-{nm:02d}-15"
p=os.path.join(os.path.dirname(__file__),'..','index.html'); s=open(p).read()
br=lambda v:f"{v:,.2f}".replace(',','X').replace('.',',').replace('X','.')
MESES=['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez']
novos=[]
for arg in sys.argv[2:]:
    nome,vendas=arg.split('='); vendas=float(vendas); com=round(vendas*0.01,2)
    pid=f"pvcom_{nome.lower()}_{y:04d}{m:02d}"
    linha=(f"  {{id:'{pid}',date:'{venc}',hora:'--:--',desc:'Comissão {nome.upper()} - vendas {MESES[m-1]}/{str(y)[2:]} (1%)',"
           f"amt:{com},type:'DEBIT',conta:'Stone',cat:'4.4.2',cli:'{nome.title()}',meio:'PIX',previsto:true,comp:'{mes}',"
           f"obs:'1% sobre R$ {br(vendas)} vendidos no Bling em {MESES[m-1]}/{y} (pedidos atendidos)'}}")
    r=re.search(r"\{id:'"+pid+r"'[^\n]*?\}(?=,?\n)",s)
    if r: s=s[:r.start()]+linha.strip()+s[r.end():]
    else: novos.append(linha)
    print(nome,br(vendas),'->',br(com),'em',venc,'(atualizado)' if r else '(novo)')
if novos:
    i=s.index('\n];', s.index('const RAW = ['))
    pre=s[:i].rstrip()
    if not pre.endswith(','): pre+=','
    s=pre+'\n'+",\n".join(novos)+s[i:]
open(p,'w').write(s)
