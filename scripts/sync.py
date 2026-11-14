#!/usr/bin/env python3
"""Sincroniza Stone e XP (extratos Pluggy/Open Finance) no index.html do YampaFin.

Uso:
  python3 scripts/sync.py --stone stone.json --xp xp.json --saldo-stone 1714.54 --saldo-xp 97.34 \
      [--em "03/11/2026 07:20"] [--hoje 2026-11-03] [--dry]

- stone.json / xp.json: resultado de openfinance_list_transactions (objeto com "results" ou lista).
- Datas chegam em UTC; grava no horário de Natal (UTC-3).
- Ids: st_ + 8 primeiros hex (Stone), xp_ + 8 primeiros hex (XP). Já existentes são ignorados.
- Stone: cada linha "Tarifa | ..." é abatida da entrada (Pix Maquininha / Recebimento Boleto) com o
  MESMO horário (a venda entra líquida e a tarifa some). Tarifa sem par vira saída 4.4.3.
- XP: entrada = 7.1.7 (taxas de entrega STRONG); saída = 5.2.99 (pendente).
- Atualiza SALDO_BANCO (Stone e XP), HOJE_SISTEMA, const hoje, m-data e o badge "Stone · dd/mm · HH:MM".
- Imprime um resumo JSON com as saídas novas e os previstos candidatos a baixa (mesmo valor, ou valor + até 5% de juros).
"""
import argparse, datetime as dt, json, os, re, sys

ap = argparse.ArgumentParser()
ap.add_argument('--stone'); ap.add_argument('--xp')
ap.add_argument('--saldo-stone', type=float); ap.add_argument('--saldo-xp', type=float)
ap.add_argument('--em'); ap.add_argument('--hoje'); ap.add_argument('--dry', action='store_true')
a = ap.parse_args()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'index.html')
s = open(PATH, encoding='utf8').read()
agora = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3)
HOJE = a.hoje or agora.strftime('%Y-%m-%d')
EM = a.em or agora.strftime('%d/%m/%Y %H:%M')

existentes = set(re.findall(r"id:'((?:st|xp)_[0-9a-f]{8})'", s))

def carregar(p):
    if not p: return []
    j = json.load(open(p, encoding='utf8'))
    if isinstance(j, dict): j = j.get('results') or j.get('transactions') or j.get('data') or []
    return j

def local(iso):
    x = dt.datetime.strptime(iso[:19], '%Y-%m-%dT%H:%M:%S') - dt.timedelta(hours=3)
    return x.strftime('%Y-%m-%d'), x.strftime('%H:%M'), x.strftime('%Y-%m-%dT%H:%M:%S')

def esc(x): return str(x).replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ').strip()

def nome_pd(r, campo):
    pd = r.get('paymentData') or {}
    v = pd.get(campo)
    if isinstance(v, dict): v = v.get('name') or (v.get('documentNumber') or {}).get('value')
    return v

def tipo(r):
    t = r.get('type')
    if t in ('CREDIT', 'DEBIT'): return t
    return 'CREDIT' if float(r['amount']) > 0 else 'DEBIT'

CATS = [
    (['BRG ', 'BODY FOOD', 'DHFIT DISTRIBUIDORA', 'SUPLEY', 'RAINHA LABORATORIO', 'D T SUPLEMENTOS', 'CANIBAL', 'FITOWAY', 'METTA FIT', 'VELIK', 'NUTRATA', 'DUX COMPANY'], '4.2.1'),
    (['CONTABILIDADE'], '5.2.11'), (['COSERN'], '5.2.3'), (['CAERN'], '5.2.17'), (['BRISANET'], '5.2.1'),
    (['DHFIT NUTRICAO'], '7.2.99'), (['FACEBOOK'], '5.8.1'), (['UBER', '99 TECNOLOGIA'], '4.4.6'),
    (['COMBUSTIVEIS', 'POSTO', 'PETROLEO'], '4.6.1'), (['PAGGPIX'], '4.4.1'),
    (['SEFAZ', 'SECRETARIA DE ESTADO DA FAZENDA', 'SECRETARIA DE ESTADO DA TRIBUTACAO', 'RN TRIBUTACAO'], '4.1.4'),
    (['DETRAN', 'DEPARTAMENTO ESTADUAL DE TRANSITO', 'BOMBEIROS'], '5.5.3'),
]
def cat_stone(r, D):
    if tipo(r) == 'CREDIT': return '3.1.8'
    for keys, c in CATS:
        if any(k in D for k in keys): return c
    return '5.2.99'

linhas, novas = [], []

# ---------- STONE ----------
st = [r for r in carregar(a.stone) if 'st_' + r['id'][:8] not in existentes]
tarifas = [r for r in st if str(r.get('description', '')).strip().upper().startswith('TARIFA')]
outras = [r for r in st if r not in tarifas]
abat = {}
for t in tarifas:
    par = [r for r in outras if tipo(r) == 'CREDIT' and r['date'][:19] == t['date'][:19]]
    if par:
        k = par[0]['id']; abat[k] = abat.get(k, 0) + abs(float(t['amount']))
        continue
    # venda já importada antes (entrou líquida): a tarifa já está abatida — não lança de novo
    d_, h_, _ = local(t['date'])
    if re.search(r"\{id:'st_[0-9a-f]{8}',date:'" + d_ + r"',hora:'" + h_ + r"',[^\n]*?type:'CREDIT',conta:'Stone'", s):
        continue
    outras.append(dict(t, _tarifa_sem_par=True))
for r in sorted(outras, key=lambda r: r['date']):
    desc = str(r.get('description', '')); D = desc.upper(); parts = [p.strip() for p in desc.split('|')]
    amt = round(abs(float(r['amount'])) - abat.get(r['id'], 0), 2)
    ty = tipo(r)
    if 'Recebimento vendas' in desc or 'liquidação' in desc: cli = 'STONE'
    else: cli = nome_pd(r, 'payer' if ty == 'CREDIT' else 'receiver') or parts[-1]
    if not cli or 'STONE INSTITUICAO' in str(cli).upper(): cli = parts[-1]
    p0 = parts[0]
    meio = 'Antecipação' if p0 == 'Antecipação' else 'Boleto' if p0 == 'Pagamento' else 'Cartão' if (p0 == 'Débito' or 'cartão' in desc) else 'PIX' if (r.get('operationType') == 'PIX' or 'Pix' in desc) else 'TED'
    d, h, _ = local(r['date'])
    c = '4.4.3' if r.get('_tarifa_sem_par') else cat_stone(r, D)
    extra = (",obs:'Líquido: tarifa de R$ %s abatida'" % f"{abat[r['id']]:.2f}".replace('.', ',')) if r['id'] in abat else ''
    i = 'st_' + r['id'][:8]
    linhas.append(f"  {{id:'{i}',date:'{d}',hora:'{h}',desc:'{esc(desc)}',amt:{amt},type:'{ty}',conta:'Stone',cat:'{c}',cli:'{esc(cli)}',meio:'{meio}'{extra}}}")
    novas.append({'id': i, 'conta': 'Stone', 'date': d, 'hora': h, 'type': ty, 'amt': amt, 'cat': c, 'desc': desc, 'cli': str(cli)})

# ---------- trocos em Pix registrados no MotoRun (aba Espécie) → transferência para o caixa ----------
# Cada troco lançado no MotoRun (especie/<data>/<pedido>/troco) é um Pix que saiu da Stone para alguém
# que deu o dinheiro em espécie. Pix de saída do mesmo dia e mesmo valor, sem categoria definida, vira
# transferência Stone → Caixa (dinheiro) automaticamente (um troco para cada Pix).
import subprocess
_troco_cache = {}
def trocos_do_dia(d):
    if d not in _troco_cache:
        try:
            out = subprocess.check_output(['curl', '-sS', '-m', '20', f'https://delivery-dhfit-4a399-default-rtdb.firebaseio.com/especie/{d}.json']).decode()
            j = json.loads(out or 'null') or {}
            _troco_cache[d] = [round(float(v['troco']), 2) for v in j.values() if isinstance(v, dict) and v.get('troco')]
        except Exception:
            _troco_cache[d] = []
    return _troco_cache[d]
# trocos já usados por transferências existentes no mesmo dia
def _usados(d):
    return [round(float(x), 2) for x in re.findall(r"\{id:'st_[0-9a-f]{8}',date:'" + d + r"'[^\n]*?amt:([\d.]+),type:'DEBIT'[^\n]*?transf:'Caixa \(Dinheiro\)'", s)]
trocos_marcados = []
_pend = {}
for k, ln in enumerate(linhas):
    m = re.match(r"  \{id:'(st_[0-9a-f]{8})',date:'([\d-]+)'[^\n]*?amt:([\d.]+),type:'DEBIT',conta:'Stone',cat:'5\.2\.99'[^\n]*?meio:'PIX'", ln)
    if not m: continue
    d, v = m.group(2), round(float(m.group(3)), 2)
    if d not in _pend:
        livres = list(trocos_do_dia(d))
        for u in _usados(d):
            h = next((x for x in livres if abs(x - u) <= 0.2), None)
            if h is not None: livres.remove(h)
        _pend[d] = livres
    hit = next((x for x in _pend[d] if abs(x - v) <= 0.2), None)
    if hit is not None:
        _pend[d].remove(hit)
        linhas[k] = ln[:-1] + ",transf:'Caixa (Dinheiro)',obs:'Troco em Pix (lançado no MotoRun) — transferência para o caixa'}"
        trocos_marcados.append({'id': m.group(1), 'date': d, 'amt': v})
        for n in novas:
            if n['id'] == m.group(1): n['cat'] = 'transf'; n['troco_motorun'] = True

# ---------- XP ----------
for r in sorted([r for r in carregar(a.xp) if 'xp_' + r['id'][:8] not in existentes], key=lambda r: r['date']):
    desc = str(r.get('description', '')); ty = tipo(r)
    amt = round(abs(float(r['amount'])), 2)
    cli = nome_pd(r, 'payer' if ty == 'CREDIT' else 'receiver') or re.sub(r'^Pix (recebido de|enviado para)\s*', '', desc, flags=re.I)
    d, h, _ = local(r['date'])
    c = '7.1.7' if ty == 'CREDIT' else '5.2.99'
    i = 'xp_' + r['id'][:8]
    linhas.append(f"  {{id:'{i}',date:'{d}',hora:'{h}',desc:'{esc(desc)}',amt:{amt},type:'{ty}',conta:'XP',cat:'{c}',cli:'{esc(cli)}',meio:'PIX'}}")
    novas.append({'id': i, 'conta': 'XP', 'date': d, 'hora': h, 'type': ty, 'amt': amt, 'cat': c, 'desc': desc, 'cli': str(cli)})

# ---------- grava ----------
if linhas:
    i = s.index('\n];', s.index('const RAW = ['))
    pre = s[:i].rstrip()
    if not pre.endswith(','): pre += ','
    s = pre + '\n  // SYNC ' + EM + '\n' + ',\n'.join(linhas) + s[i:]
    ult = max((n for n in novas if n['conta'] == 'Stone'), key=lambda n: (n['date'], n['hora']), default=None)
    if ult: s = re.sub(r"Stone · \d\d/\d\d · \d\d:\d\d", f"Stone · {ult['date'][8:10]}/{ult['date'][5:7]} · {ult['hora']}", s)
s = re.sub(r'id="m-data" value="[\d-]+"', f'id="m-data" value="{HOJE}"', s)
s = re.sub(r"const hoje = '20\d\d-[\d-]+';", f"const hoje = '{HOJE}';", s)
s = re.sub(r"const HOJE_SISTEMA = '[\d-]+';", f"const HOJE_SISTEMA = '{HOJE}';", s)
if a.saldo_stone is not None:
    s = re.sub(r"Stone:\{valor:[\d.\-]+, em:'[^']*'\}", f"Stone:{{valor:{a.saldo_stone}, em:'{EM}'}}", s)
if a.saldo_xp is not None:
    s = re.sub(r"XP:\{valor:[\d.\-]+, em:'[^']*'\}", f"XP:{{valor:{a.saldo_xp}, em:'{EM}'}}", s)

# ---------- candidatos a baixa (previstos em aberto) ----------
baixados = set(re.findall(r"^\s*'([\w]+)':\s*\[", s[s.index('const BAIXAS_PREV'):s.index('};', s.index('const BAIXAS_PREV'))], re.M))
prev = []
for m in re.finditer(r"\{id:'([^']+)',date:'([\d-]+)'[^\n]*?desc:'((?:[^'\\]|\\.)*)',amt:([\d.]+),type:'DEBIT'[^\n]*?previsto:true", s):
    if m.group(1) not in baixados: prev.append({'id': m.group(1), 'date': m.group(2), 'desc': m.group(3), 'amt': float(m.group(4))})
for n in novas:
    if n['type'] != 'DEBIT': continue
    c = [p for p in prev if p['amt'] <= n['amt'] + 0.01 and n['amt'] <= p['amt'] * 1.05 + 0.01]
    n['candidatos'] = sorted(c, key=lambda p: (abs(p['amt'] - n['amt']), p['date']))[:4]

# ---------- DH HOLDING (CNPJ 57.875.466/0001-58): meta de R$ 20.000 por mês ----------
META_DH = 20000.0
mes = HOJE[:7]
dh = []
for m in re.finditer(r"\{id:'(st_[0-9a-f]{8})',date:'" + mes + r"-\d\d'[^\n]*?amt:([\d.]+),type:'DEBIT',conta:'Stone'[^\n]*", s):
    if 'DH HOLDING' in m.group(0).upper(): dh.append((m.group(1), float(m.group(2))))
dh_tot = round(sum(v for _, v in dh), 2)
dh_info = {'mes': mes, 'total': dh_tot, 'meta': META_DH, 'falta': round(max(0, META_DH - dh_tot), 2),
           'novos': [n['amt'] for n in novas if n['type'] == 'DEBIT' and 'DH HOLDING' in n['desc'].upper()]}

if not a.dry: open(PATH, 'w', encoding='utf8').write(s)
res = {'hoje': HOJE, 'em': EM, 'novas': len(novas),
       'entradas': round(sum(n['amt'] for n in novas if n['type'] == 'CREDIT'), 2),
       'saidas': round(sum(n['amt'] for n in novas if n['type'] == 'DEBIT'), 2),
       'tarifas_abatidas': round(sum(abat.values()), 2),
       'dh_holding': dh_info,
       'trocos_motorun': trocos_marcados,
       'saidas_novas': [n for n in novas if n['type'] == 'DEBIT' and not n.get('troco_motorun')]}
print(json.dumps(res, ensure_ascii=False, indent=1))
