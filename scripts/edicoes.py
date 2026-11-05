#!/usr/bin/env python3
"""Incorpora no index.html as conciliações feitas pelo Felipe no app (salvas no Firebase em /yampafin/ed).

Uso: python3 scripts/edicoes.py [--dry]
- Para cada edição de um lançamento existente no RAW: grava cat, status (status_fixo), obs, comp, desc, cli,
  meio, amt, date e conta direto na linha; contaTransf → transf:'<conta>'.
- Lançamentos manuais (manual:true) viram linhas novas no fim do RAW (id mantido).
- Espelhos de transferência (esp_*) são ignorados (o app recria sozinho).
- Depois de gravar, apaga da nuvem só as chaves incorporadas (o app recarrega a nuvem e continua igual).
Imprime JSON com o que foi aplicado.
"""
import json, os, re, subprocess, sys
URL = 'https://delivery-dhfit-4a399-default-rtdb.firebaseio.com/yampafin/ed'
DRY = '--dry' in sys.argv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, 'index.html')
ed = json.loads(subprocess.check_output(['curl', '-sS', '-m', '30', URL + '.json']).decode() or 'null') or {}
s = open(P, encoding='utf8').read()
esc = lambda x: str(x).replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ')
def setf(l, k, v):
    val = repr(float(v)) if k == 'amt' else "'" + esc(v) + "'"
    if re.search(r"[{,]" + k + r":", l):
        return re.sub(r"([{,])" + k + r":(?:'(?:[^'\\]|\\.)*'|[^,}]+)", lambda m: m.group(1) + k + ":" + val, l, count=1)
    return l[:-1] + f",{k}:{val}}}"
aplicados, novos, ignorados = [], [], []
linhas_novas = []
for i, e in ed.items():
    if not isinstance(e, dict) or i.startswith('esp_'):
        ignorados.append(i); continue
    m = re.search(r"\{id:'" + re.escape(i) + r"'[^\n]*?\}(?=,?\n)", s)
    if m:
        l = m.group(0); n = l
        for k in ['cat', 'obs', 'comp', 'desc', 'cli', 'meio', 'amt', 'date', 'conta']:
            if e.get(k) not in (None, ''): n = setf(n, k, e[k])
        if e.get('status'): n = setf(n, 'status_fixo', e['status'])
        if e.get('contaTransf'): n = setf(n, 'transf', e['contaTransf'])
        s = s.replace(l, n); aplicados.append(i)
    elif e.get('manual'):
        r = {'id': i, 'date': e.get('date', ''), 'hora': e.get('hora', '--:--'), 'desc': e.get('desc', ''), 'amt': abs(float(e.get('amt') or 0)),
             'type': e.get('type', 'CREDIT'), 'conta': e.get('conta', 'Stone'), 'cat': e.get('cat', ''), 'cli': e.get('cli', ''),
             'meio': e.get('meio', 'PIX'), 'status_fixo': e.get('status', 'conciliado'), 'comp': e.get('comp', ''), 'obs': e.get('obs', '')}
        if e.get('contaTransf'): r['transf'] = e['contaTransf']
        parts = []
        for k, v in r.items():
            if v in ('', None): continue
            parts.append(f"{k}:{repr(float(v)) if k == 'amt' else chr(39) + esc(v) + chr(39)}")
        linhas_novas.append('  {' + ','.join(parts) + '}'); novos.append(i)
    else:
        ignorados.append(i)
if linhas_novas:
    k = s.index('\n];', s.index('const RAW = ['))
    pre = s[:k].rstrip()
    if not pre.endswith(','): pre += ','
    s = pre + '\n  // CONCILIAÇÕES FEITAS NO APP (manuais)\n' + ',\n'.join(linhas_novas) + s[k:]
if not DRY and (aplicados or novos):
    open(P, 'w', encoding='utf8').write(s)
    feitos = aplicados + novos + [x for x in ignorados if x.startswith('esp_')]
    resto = {k: v for k, v in ed.items() if k not in feitos}
    subprocess.check_output(['curl', '-sS', '-m', '30', '-X', 'PUT', '-H', 'Content-Type: application/json',
                             '--data-binary', json.dumps(resto if resto else None), URL + '.json'])
print(json.dumps({'nuvem': len(ed), 'aplicados': aplicados, 'manuais_novos': novos, 'ignorados': ignorados}, ensure_ascii=False))
