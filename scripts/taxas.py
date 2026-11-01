#!/usr/bin/env python3
"""Lança as taxas do mês (Stone) no último dia do mês: uma SAÍDA por tipo de taxa e uma ENTRADA
de mesmo valor (receita de vendas bruta), para o saldo de caixa continuar batendo com o banco
(as vendas entram líquidas no extrato).
Uso: python3 scripts/taxas.py AAAA-MM PIX=<valor> BOLETO=<valor> CARTAO=<valor>   (omitir/0 = não lança)
Ids: txm_<tipo>_<AAAAMM> (saída) e txm_<tipo>_<AAAAMM>_e (entrada) — atualiza se já existir."""
import sys,re,os,calendar
mes=sys.argv[1]; y,m=map(int,mes.split('-')); last=f"{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}"
p=os.path.join(os.path.dirname(__file__),'..','index.html'); s=open(p).read()
br=lambda v:f"{v:,.2f}".replace(',','X').replace('.',',').replace('X','.')
MES=['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'][m-1]+'/'+str(y)[2:]
TIPOS={'PIX':('4.4.3','Taxa de Pix (maquininha)'),'BOLETO':('4.4.5','Taxa de boleto (recebimentos)'),'CARTAO':('4.4.1','Taxa de cartão crédito/débito + antecipação')}
novos=[]
total=0.0; partes=[]
def put(pid,linha):
    global s
    r=re.search(r"\{id:'"+pid+r"'[^\n]*?\}(?=,?\n)",s)
    if r: s=s[:r.start()]+linha.strip()+s[r.end():]; return 'atualizado'
    novos.append(linha); return 'novo'
for arg in sys.argv[2:]:
    k,v=arg.split('='); v=round(float(v),2); k=k.upper()
    if v<=0 or k not in TIPOS: continue
    cat,nome=TIPOS[k]; base=f"txm_{k.lower()}_{y:04d}{m:02d}"
    obs=f"Taxas Stone {MES}: lançadas no último dia do mês (saída + entrada de mesmo valor) — o extrato traz as vendas líquidas"
    sd=f"  {{id:'{base}',date:'{last}',hora:'23:58',desc:'{nome} - {MES}',amt:{v},type:'DEBIT',conta:'Stone',cat:'{cat}',cli:'STONE',meio:'Ajuste',status_fixo:'conciliado',comp:'{mes}',obs:'{obs}'}}"
    st=put(base,sd); total+=v; partes.append(f"{k.lower()} R$ {br(v)}")
    # remove entradas antigas por tipo (versão anterior)
    s=re.sub(r"\n  \{id:'"+base+r"_e'[^\n]*",'',s)
    print(k,cat,'R$',br(v),'em',last,st)
if total>0:
    eid=f"txm_ajuste_{y:04d}{m:02d}_e"
    en=f"  {{id:'{eid}',date:'{last}',hora:'23:59',desc:'Receita bruta - ajuste das taxas Stone - {MES}',amt:{round(total,2)},type:'CREDIT',conta:'Stone',cat:'3.1.8',cli:'STONE',meio:'Ajuste',status_fixo:'conciliado',comp:'{mes}',obs:'Entrada única que compensa as saídas de taxas ({', '.join(partes)}) — as vendas entram líquidas no extrato; com ela o saldo bate com o banco'}}"
    print('ENTRADA 3.1.8 R$',br(round(total,2)),put(eid,en))
if novos:
    i=s.index('\n];', s.index('const RAW = ['))
    pre=s[:i].rstrip()
    if not pre.endswith(','): pre+=','
    s=pre+'\n'+",\n".join(novos)+s[i:]
open(p,'w').write(s)
