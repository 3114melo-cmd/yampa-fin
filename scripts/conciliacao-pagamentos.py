#!/usr/bin/env python3
"""Conciliação diária de pagamentos do MotoRun (pedidos entregues x FOTO do comprovante x extrato Stone).

A FOTO do comprovante é a fonte principal (Felipe, 03/11/2026): nem sempre quem paga o PIX é o cliente do pedido,
e a equipe sempre escreve à caneta o nome do cliente no comprovante. Por isso o fluxo tem 2 passos:

  1) Baixar as fotos e gerar a ficha de leitura:
       python3 scripts/conciliacao-pagamentos.py AAAA-MM-DD --stone /tmp/stone.json --extrair
     → grava <out>/leitura.json (uma entrada por comprovante do dia, com "imagem" e o bloco "leitura" vazio)
       e as imagens em <out>/. O Claude abre TODAS as imagens e preenche "leitura" de cada uma:
         tipo:        "pix" | "credito" | "debito" | "voucher" | "link" | "outro"
         valor:       número (valor da transação na foto)
         data:        "AAAA-MM-DD" da transação na foto
         hora:        "HH:MM" da transação na foto
         pagador:     nome de quem pagou (PIX) ou nome impresso no cupom ("" se não tiver)
         nome_caneta: nome escrito à mão ("" se não tiver)
         parcelas:    número (cartão) ou null
         id_transacao: E2E / autenticação / ID da transação / AUT do cupom ("" se não legível)
         legivel:     true/false
  2) Conciliar com a leitura:
       python3 scripts/conciliacao-pagamentos.py AAAA-MM-DD --stone /tmp/stone.json --leitura <out>/leitura.json [--enviar]

Sem --leitura o script ainda roda (usa só o que foi digitado no MotoRun), mas avisa "sem_leitura".

Regras:
 - Pedidos que exigem comprovante: entregues no dia por motoboy (rota ≠ loja/retirada), pagos por PIX, cartão,
   débito, link ou voucher. Não exigem: dinheiro, "Site Pago", entregas da Strongest.
 - Pedido ↔ comprovante: nome do pedido bate com o nome à caneta, o nome lançado ou o pagador da foto; valor igual
   (aceita comprovante menor em pagamento dividido). Comprovante pode ser de até 3 dias antes.
 - PIX ↔ Stone: com a hora da foto, mesmo valor e até 10 min de diferença (não precisa de nome). Sem hora: mesmo valor,
   2 dias antes até 1h depois do lançamento, preferindo o pagador com o mesmo nome.
 - Cartão de crédito/débito (Felipe, 19/11/2026): além do comprovante, confere se o dinheiro caiu na Stone — o cupom
   entra como "Antecipação | Recebimento vendas", líquido (90–99,5% do valor), de 2 min a 3 h depois da hora do cupom
   (sem hora na foto: até 12 h antes / 6 h depois do lançamento). Não achou → pendência "cartão que não caiu na Stone"
   (ex.: venda passada em outra maquininha), rechecada todo dia.
 - Link de pagamento Stone (Felipe, 19/11/2026): o link pago cai na Stone na MADRUGADA seguinte (~3h) como
   "Antecipação | Crédito | Recebimento vendas", líquido (90–99,5% do valor). Achou → conferido. Não achou → fica
   "aguardando" (guardado em links_aguardando no estado) e só vira pendência "link que não caiu na Stone" se não
   aparecer até as 06:00 de 2 dias depois do pagamento (sem hora na foto: do lançamento).
 - Voucher cai depois (Alelo/Pluxee etc.): só confere se tem comprovante.
 - Pendências (manda WhatsApp, Felipe 04/11/2026): valor do comprovante diferente do pedido, entrega sem comprovante,
   PIX que não entrou, comprovante de outra data, mesmo comprovante em 2 lançamentos. Tudo batendo → não envia nada.
 - Relatório: "Relatório geral" (conferidos ✅, valor que não bate, sem comprovante) + "Relatório de pendências" com
   cliente, valor e forma de pagamento, vendedor, entregador e rota (Norte/Sul) de cada pedido.
"""
import json, sys, os, re, subprocess, unicodedata, argparse, base64
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=-3))
FB = 'https://delivery-dhfit-4a399-default-rtdb.firebaseio.com'
PHONE_ID = '1413683815156427'
DESTINO = os.environ.get('DESTINO', '5584986327208')


def norm(s):
    return unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper()


def toks(s):
    return [w for w in re.split(r'[^A-Z]+', norm(s)) if len(w) >= 3]


def fb(path):
    out = subprocess.run(['curl', '-sS', '--max-time', '180', f'{FB}/{path}'], capture_output=True, check=True).stdout
    return json.loads(out) or {}


def brl(v):
    return f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def hm(ts):
    return datetime.fromtimestamp(ts, TZ).strftime('%d/%m %H:%M')


def valor(v):
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v or '0').replace('R$', '').strip()
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def nome_bate(a, *bs):
    ta = toks(a)
    alvo = norm(' '.join(str(b or '') for b in bs))
    return bool(ta) and any(w[:4] in alvo for w in ta)


def tipo_de(pag):
    p = norm(pag)
    if 'PIX' in p:
        return 'pix'
    if 'VOUCHER' in p:
        return 'voucher'
    if 'DEBITO' in p:
        return 'debito'
    if 'LINK' in p:
        return 'link'
    if 'CART' in p or 'CREDITO' in p:
        return 'credito'
    return 'outro'


def eh_cartao(tipo):
    return tipo in ('credito', 'debito', 'link')


ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dados', 'pendencias-conciliacao.json')
ESTADO = os.path.normpath(ESTADO)


def carregar_estado():
    try:
        e = json.load(open(ESTADO))
    except (FileNotFoundError, ValueError):
        e = {}
    e.setdefault('abertas', {})
    e.setdefault('resolvidas', [])
    e.setdefault('links_aguardando', {})
    return e


def salvar_estado(e):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    e['resolvidas'] = e['resolvidas'][-300:]
    json.dump(e, open(ESTADO, 'w'), ensure_ascii=False, indent=1)


def resolver(argv):
    """python3 scripts/conciliacao-pagamentos.py resolver <nº do pedido | id da pendência> --obs "o que foi"
    """
    ap = argparse.ArgumentParser()
    ap.add_argument('alvo')
    ap.add_argument('--obs', default='comprovante enviado pelo Felipe')
    ap.add_argument('--listar', action='store_true')
    a = ap.parse_args(argv)
    e = carregar_estado()
    achou = [k for k, v in e['abertas'].items() if k == a.alvo or str(v['ficha'].get('pedido')) == a.alvo.lstrip('#')]
    for k in achou:
        x = e['abertas'].pop(k)
        e['resolvidas'].append(dict(x, resolvido_em=datetime.now(TZ).strftime('%Y-%m-%d'), motivo=a.obs))
    salvar_estado(e)
    print(json.dumps(dict(resolvidas=achou, ainda_abertas=[f"{v['ficha'].get('cliente')} #{v['ficha'].get('pedido')} ({v['tipo']}, desde {v['dia']})"
                                                            for v in e['abertas'].values()]), ensure_ascii=False, indent=1))


def link_ref(c):
    """(instante de referência, tem hora da transação?) de um comprovante de link."""
    if c.get('t_trans') and c.get('hora_lida'):
        return c['t_trans'], True
    return c['t'], False


def link_prazo(ref):
    """06:00 (Natal) de 2 dias depois do pagamento."""
    d = datetime.fromtimestamp(ref, TZ).replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=2)
    return d.timestamp()


def acha_link(linkst, v, ref_hora, folga=0):
    """Crédito "Antecipação | Crédito | Recebimento vendas" livre, 90–99,5% do valor, entre o pagamento e o prazo
    (+folga dias). Sem hora na foto, aceita a partir de 3 dias antes do lançamento."""
    ref, com_hora = ref_hora
    t0 = ref if com_hora else ref - 3 * 86400
    t1 = link_prazo(ref) + folga * 86400
    cand = [s for s in linkst if not s['usado'] and t0 <= s['t'] <= t1 and 0.90 * v <= s['v'] <= 0.995 * v]
    return min(cand, key=lambda s: abs(s['t'] - ref)) if cand else None


def acha_cartao(cartao, v, ref_hora, folga=0):
    """Recebimento de venda de cartão livre (90–99,5% do valor): 2 min a 3 h depois do cupom; sem hora, de 12 h antes
    a 6 h depois do lançamento. folga = dias extras (recheque de pendência antiga)."""
    ref, com_hora = ref_hora
    t0, t1 = (ref + 120, ref + 3 * 3600) if com_hora else (ref - 12 * 3600, ref + 6 * 3600)
    t1 += folga * 86400
    cand = [s for s in cartao if not s['usado'] and t0 <= s['t'] <= t1 and 0.90 * v <= s['v'] <= 0.995 * v]
    return min(cand, key=lambda s: abs(s['t'] - ref)) if cand else None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'resolver':
        return resolver(sys.argv[2:])
    ap = argparse.ArgumentParser()
    ap.add_argument('dia')
    ap.add_argument('--stone', required=True)
    ap.add_argument('--extrair', action='store_true')
    ap.add_argument('--leitura')
    ap.add_argument('--ignorar', default='')
    ap.add_argument('--enviar', action='store_true')
    ap.add_argument('--pdf', action='store_true', help='só gera o PDF, sem enviar')
    ap.add_argument('--forcar', action='store_true', help='envia de novo mesmo se o dia já foi enviado')
    ap.add_argument('--salvar', action='store_true', help='grava as pendências em dados/pendencias-conciliacao.json')
    ap.add_argument('--out', default='/tmp/conciliacao')
    a = ap.parse_args()
    ignorar = set(x for x in a.ignorar.split(',') if x)
    os.makedirs(a.out, exist_ok=True)

    dia = datetime.strptime(a.dia, '%Y-%m-%d').replace(tzinfo=TZ)
    ini, fim = dia.timestamp(), (dia + timedelta(days=1)).timestamp()

    # ---- comprovantes do MotoRun (dia e 3 dias antes)
    comps = []
    for d in range(3, -1, -1):
        dd = (dia - timedelta(days=d)).strftime('%Y-%m-%d')
        for k, r in (fb(f'comprovantes/{dd}.json') or {}).items():
            comps.append(dict(id=k, dia=dd, t=r['ts'] / 1000, v=valor(r.get('valor')), n=r.get('nome', ''),
                              pag=r.get('pag', ''), tipo=tipo_de(r.get('pag')), vend=r.get('vendedor', ''),
                              obs=r.get('obs', ''), pedido=None, stone=None, lido=False, caneta='', pagador='',
                              t_trans=None, idtx='', extras=[i for i in range(2, 6) if r.get(f'tipo{i}')]))
    comps.sort(key=lambda c: c['t'])
    comps_dia = [c for c in comps if ini <= c['t'] < fim]

    if a.extrair:
        ficha = []
        for c in comps_dia:
            arq = fb(f'comprovantes_arq/{c["dia"]}/{c["id"]}.json')
            achados = re.findall(r'data:([\w/+.-]+);base64,([A-Za-z0-9+/=]+)', json.dumps(arq))
            # fotos extras (2º, 3º… comprovante do mesmo pedido) ficam em <id>_2, <id>_3…
            for i in c.get('extras') or []:
                arq_i = fb(f'comprovantes_arq/{c["dia"]}/{c["id"]}_{i}.json')
                achados += re.findall(r'data:([\w/+.-]+);base64,([A-Za-z0-9+/=]+)', json.dumps(arq_i))
            imgs = []
            for j, (mt, b) in enumerate(achados):
                ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'application/pdf': 'pdf', 'image/webp': 'webp'}.get(mt, 'bin')
                fn = os.path.join(a.out, f'{c["id"]}_{j}.{ext}')
                open(fn, 'wb').write(base64.b64decode(b))
                imgs.append(fn)
            ficha.append(dict(id=c['id'], lancado=dict(nome=c['n'], valor=c['v'], pag=c['pag'], vendedor=c['vend'], hora=hm(c['t'])),
                              imagens=imgs, leitura=dict(tipo=None, valor=None, data=None, hora=None, pagador='', nome_caneta='',
                                                        parcelas=None, id_transacao='', legivel=None),
                              # 2ª, 3ª… foto com OUTRO pagamento do mesmo pedido: uma leitura (mesmos campos) por foto extra, na ordem de "imagens"
                              partes=[]))
        fn = os.path.join(a.out, 'leitura.json')
        json.dump(ficha, open(fn, 'w'), ensure_ascii=False, indent=1)
        print(json.dumps(dict(ficha=fn, comprovantes=len(ficha), imagens=sum(len(f['imagens']) for f in ficha),
                              sem_imagem=[f['id'] for f in ficha if not f['imagens']]), ensure_ascii=False))
        return

    # ---- aplica a leitura das fotos
    leitura_ok = False
    graves, ajustes = [], []

    def add(lista, tipo, txt, ref, valor_=0.0, extra=None):
        pid = f'{tipo}:{ref}'
        if pid in ignorar:
            return
        lista.append(dict(id=pid, tipo=tipo, texto=txt, valor=valor_, **(extra or {})))

    if a.leitura:
        leitura_ok = True
        lmap = {f['id']: f for f in json.load(open(a.leitura))}
        filhos = []
        for c in comps_dia:
            # pedido pago com mais de um comprovante (2ª, 3ª foto): cada foto extra vira uma "parte" conferida na Stone
            for i, P in enumerate((lmap.get(c['id']) or {}).get('partes') or [], 2):
                if P.get('valor') is None or P.get('tipo') == 'outro':
                    continue
                h = dict(c, id=f"{c['id']}#{i}", parte_de=c['id'], v=valor(P['valor']), tipo=P.get('tipo') or c['tipo'],
                         lido=True, caneta=P.get('nome_caneta') or '', pagador=P.get('pagador') or '', parcelas=P.get('parcelas'),
                         idtx=re.sub(r'\s', '', str(P.get('id_transacao') or '')).upper(), t_trans=None, stone=None, extras=[])
                if P.get('data'):
                    try:
                        h['t_trans'] = datetime.strptime(f"{P['data']} {P.get('hora') or '12:00'}", '%Y-%m-%d %H:%M').replace(tzinfo=TZ).timestamp()
                        h['hora_lida'] = bool(P.get('hora'))
                    except ValueError:
                        pass
                f0 = lmap[c['id']]
                h['imagem'] = (f0.get('imagens') or [None] * i)[i - 1] if len(f0.get('imagens') or []) >= i else None
                filhos.append(h)
        for c in comps_dia:
            f = lmap.get(c['id'])
            L = (f or {}).get('leitura') or {}
            if not f or L.get('legivel') is None:
                add(ajustes, 'sem_leitura', f"Foto não conferida: {c['n']} R$ {brl(c['v'])} ({c['vend']}, {hm(c['t'])})", c['id'])
                continue
            c['lido'] = True
            c['imagem'] = (f.get('imagens') or [None])[0]
            if not L.get('legivel'):
                add(ajustes, 'ilegivel', f"Foto ilegível: {c['n']} R$ {brl(c['v'])} ({c['vend']}, {hm(c['t'])}) — conferido só pelo lançado", c['id'],
                    extra={'imagem': c['imagem']})
                continue
            c['caneta'] = L.get('nome_caneta') or ''
            c['pagador'] = L.get('pagador') or ''
            c['parcelas'] = L.get('parcelas')
            c['idtx'] = re.sub(r'\s', '', str(L.get('id_transacao') or '')).upper()
            if L.get('tipo') and L['tipo'] != c['tipo']:
                add(ajustes, 'tipo_diferente', f"{c['n']} R$ {brl(c['v'])} ({c['vend']}): lançado como {c['pag']}, mas a foto é {L['tipo'].upper()}"
                    + (f" {L['parcelas']}x" if L.get('parcelas') else '') + ' — corrigir no MotoRun', c['id'], extra={'imagem': c['imagem']})
                c['tipo'] = L['tipo']
            partes = [h for h in filhos if h['parte_de'] == c['id']]
            if L.get('valor') is not None:
                tot = round(valor(L['valor']) + sum(h['v'] for h in partes), 2)
                if abs(tot - c['v']) >= 0.01:
                    add(ajustes, 'valor_diferente', f"{c['n']}: lançado R$ {brl(c['v'])}, mas " + (f"as {len(partes) + 1} fotos somam" if partes else "a foto mostra")
                        + f" R$ {brl(tot)} ({c['vend']}) — corrigir no MotoRun", c['id'], extra={'imagem': c['imagem']})
                c['v'] = valor(L['valor'])
                if partes:
                    c['v_total'] = tot
            if L.get('data'):
                try:
                    hh = L.get('hora') or '12:00'
                    c['t_trans'] = datetime.strptime(f"{L['data']} {hh}", '%Y-%m-%d %H:%M').replace(tzinfo=TZ).timestamp()
                    c['hora_lida'] = bool(L.get('hora'))
                except ValueError:
                    pass
                if c['t_trans'] and c['t_trans'] < c['t'] - 3 * 86400:
                    add(graves, 'comprovante_antigo', f"COMPROVANTE DE OUTRA DATA: {c['n']} R$ {brl(c['v'])} lançado em {hm(c['t'])} por {c['vend']}, "
                        f"mas a transação na foto é de {datetime.fromtimestamp(c['t_trans'], TZ).strftime('%d/%m/%Y')}", c['id'], c['v'],
                        {'imagem': c['imagem']})
        comps += filhos
        comps_dia += filhos
        # mesmo ID de transação em dois comprovantes → mesmo pagamento usado 2 vezes
        vistos = {}
        for c in comps_dia:
            if c['idtx'] and len(c['idtx']) >= 6:
                if c['idtx'] in vistos:
                    o = vistos[c['idtx']]
                    if o['n'] == c['n'] and abs(o['t'] - c['t']) < 600:
                        c['duplicado'] = o['id']
                    else:
                        c['repetido'] = o['id']
                        add(graves, 'comprovante_repetido', f"MESMO COMPROVANTE EM 2 LANÇAMENTOS: {o['n']} ({o['vend']}, {hm(o['t'])}) e {c['n']} ({c['vend']}, {hm(c['t'])}), R$ {brl(c['v'])}",
                            c['id'], c['v'], {'imagem': c.get('imagem')})
                else:
                    vistos[c['idtx']] = c
    else:
        add(ajustes, 'sem_leitura', 'As fotos dos comprovantes não foram lidas — conferência feita só pelo que foi digitado no MotoRun', 'todas')

    # duplicado de lançamento (mesmo nome e valor em até 2 min), se a foto não resolveu
    for i, c in enumerate(comps_dia):
        if c.get('duplicado') or c.get('repetido'):
            continue
        for c2 in comps_dia[:i]:
            if not c2.get('duplicado') and c2['n'] == c['n'] and c2['v'] == c['v'] and abs(c2['t'] - c['t']) < 120 \
                    and (not c['idtx'] or not c2['idtx'] or c['idtx'] == c2['idtx']):
                c['duplicado'] = c2['id']
                break
    for c in comps_dia:
        if c.get('duplicado'):
            add(ajustes, 'duplicado', f"Comprovante lançado 2 vezes: {c['n']} R$ {brl(c['v'])} ({c['vend']}, {hm(c['t'])}) — apagar um", c['id'])

    # ---- Stone
    st = json.load(open(a.stone))
    if isinstance(st, dict):
        st = st.get('results', [])
    pix, cartao, linkst = [], [], []
    for r in st:
        if r.get('type') != 'CREDIT':
            continue
        t = datetime.fromisoformat(r['date'].replace('Z', '+00:00')).timestamp()
        desc = r.get('description') or ''
        payer = (r.get('paymentData') or {}).get('payer')
        if isinstance(payer, dict):
            payer = payer.get('name')
        item = dict(t=t, v=round(float(r['amount']), 2), n=norm(f'{payer or ""} {desc}'), desc=desc, usado=False)
        if 'PIX' in norm(desc) or r.get('operationType') == 'PIX':
            pix.append(item)
        elif 'RECEBIMENTO' in norm(desc) and 'VENDA' in norm(desc):
            # link de pagamento: "Antecipação | Crédito | Recebimento vendas" (madrugada seguinte)
            (linkst if re.search(r'\|\s*CREDITO\s*\|', norm(desc)) else cartao).append(item)
    if not pix and not cartao and not linkst:
        print(json.dumps({'erro': 'extrato da Stone vazio'}))
        sys.exit(2)
    ultimo = max(x['t'] for x in pix + cartao + linkst)
    extrato_completo = ultimo >= fim - 3 * 3600

    # ---- pedidos entregues
    url = f'pedidos.json?orderBy=%22tsEntregue%22&startAt={int(ini*1000)}&endAt={int(fim*1000)}'
    try:
        ped = fb(url)
        if isinstance(ped, dict) and 'error' in ped:
            ped = {}
    except Exception:
        ped = {}
    if not ped:
        ped = {k: v for k, v in fb('pedidos.json').items() if isinstance(v, dict) and ini * 1000 <= (v.get('tsEntregue') or 0) < fim * 1000}
    pedidos = [dict(v, _k=k) for k, v in ped.items() if isinstance(v, dict) and v.get('status') == 'entregue']
    entregas = [p for p in pedidos if p.get('rota') not in ('loja', 'retirada')]

    def precisa_comp(p):
        pg = norm(p.get('pag'))
        if 'STRONGEST' in norm(p.get('nome')) or 'SITE' in pg:
            return False
        return bool(re.search(r'PIX|CART|DEBITO|LINK|VOUCHER|CREDITO', pg)) and valor(p.get('valor')) > 0

    alvo = sorted([p for p in entregas if precisa_comp(p)], key=lambda p: p.get('ts') or 0)

    # ---- pedido x comprovante (nome à caneta > nome lançado > pagador da foto)
    livres = [c for c in comps if not c.get('duplicado') and not c.get('repetido') and not c.get('parte_de')]
    vt = lambda c: c.get('v_total', c['v'])
    for rodada in ('caneta', 'qualquer', 'valor_difere'):
        for p in alvo:
            if p.get('_comp'):
                continue
            pv = valor(p.get('valor'))
            split = '+' in (p.get('pag') or '') or p.get('pagSplit')
            tsp = (p.get('ts') or 0) / 1000
            def bate(c):
                if rodada == 'caneta':
                    return c['caneta'] and nome_bate(p.get('nome'), c['caneta'])
                return nome_bate(p.get('nome'), c['n'], c['caneta'], c['pagador'])
            if rodada == 'valor_difere':  # mesmo cliente, valor diferente: só comprovante do dia, depois do pedido criado
                cand = [c for c in livres if c['pedido'] is None and bate(c) and tsp - 600 <= c['t'] < fim]
            else:
                cand = [c for c in livres if c['pedido'] is None and bate(c)
                        and (abs(vt(c) - pv) < 0.01 or (split and vt(c) <= pv + 0.01))]
                cand = [c for c in cand if c['t'] >= tsp - 600] or [c for c in cand if c['t'] >= tsp - 3 * 86400]
            if cand:
                c = min(cand, key=lambda c: abs(c['t'] - (p.get('tsEntregue') or 0) / 1000))
                c['pedido'] = p['_k']
                p['_comp'] = c
    for h in comps:
        if h.get('parte_de'):
            h['pedido'] = next((c['pedido'] for c in comps if c['id'] == h['parte_de']), None)

    # ---- PIX x Stone
    def acha_pix(c, modo):
        cand = [s for s in pix if not s['usado'] and abs(s['v'] - c['v']) < 0.01]
        if modo == 'hora':
            if not (c['t_trans'] and c.get('hora_lida')):
                return
            cand = [s for s in cand if abs(s['t'] - c['t_trans']) <= 600]
            ref = c['t_trans']
        else:
            cand = [s for s in cand if -2 * 86400 <= s['t'] - c['t'] <= 3600]
            if modo == 'nome':
                cand = [s for s in cand if nome_bate(c['n'], s['n']) or nome_bate(c['pagador'], s['n']) or nome_bate(c['caneta'], s['n'])]
            else:
                cand = [s for s in cand if abs(s['t'] - c['t']) <= 3600]
            ref = c['t']
        if cand:
            s = min(cand, key=lambda s: abs(s['t'] - ref))
            s['usado'] = True
            c['stone'] = s

    pix_comps = [c for c in comps if c['tipo'] == 'pix' and not c.get('duplicado') and not c.get('repetido')]
    for modo in ('hora', 'nome', 'valor'):
        for c in pix_comps:
            if not c['stone']:
                acha_pix(c, modo)

    # ---- cupom de cartão/débito x Stone (Felipe, 19/11/2026: tem que conferir se o dinheiro caiu)
    card_comps = [c for c in comps if c['tipo'] in ('credito', 'debito') and not c.get('duplicado') and not c.get('repetido')]

    # A Stone às vezes junta 2 vendas de cartão feitas no mesmo minuto num recebimento só
    # (ex.: 18/11 18:31 Helisson 129,90 3x + Roosevelt 65,00 3x → 184,39 às 18:51). Primeiro casa esses pares
    # pelo líquido esperado (tabela Mastercard de scripts/taxas_config.json), depois os cupons sozinhos.
    try:
        tab = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'taxas_config.json')))['tabela']['mastercard']
    except Exception:
        tab = {'debito': 0.0092, 'credito_avista': 0.0339, 'credito_2x': 0.046, 'credito_3x': 0.0539}

    def liquido(c):
        if c['tipo'] == 'debito':
            tx = tab['debito']
        else:
            n = int(c.get('parcelas') or 1)
            tx = tab['credito_avista'] if n <= 1 else tab['credito_2x'] if n == 2 else tab['credito_3x']
        return c['v'] * (1 - tx)

    com_hora_cc = [c for c in card_comps if link_ref(c)[1]]
    for i, c in enumerate(com_hora_cc):
        for c2 in com_hora_cc[i + 1:]:
            if c['stone'] or c2['stone'] or abs(link_ref(c)[0] - link_ref(c2)[0]) > 300:
                continue
            ref, soma = max(link_ref(c)[0], link_ref(c2)[0]), liquido(c) + liquido(c2)
            cand = [s for s in cartao if not s['usado'] and ref + 60 <= s['t'] <= ref + 3 * 3600 and abs(s['v'] - soma) <= 0.10]
            if cand:
                s_ = min(cand, key=lambda s: s['t'] - ref)
                s_['usado'] = True
                c['stone'] = c2['stone'] = s_

    for so_hora in (True, False):  # primeiro os cupons com hora (pareamento exato), depois os sem hora
        for c in sorted([c for c in card_comps if not c['stone'] and link_ref(c)[1] == so_hora], key=lambda c: link_ref(c)[0]):
            s_ = acha_cartao(cartao, c['v'], link_ref(c))
            if s_:
                s_['usado'] = True
                c['stone'] = s_

    # ---- link de pagamento x Stone (Felipe, 19/11/2026)
    link_comps = sorted([c for c in comps if c['tipo'] == 'link' and not c.get('duplicado') and not c.get('repetido')],
                        key=lambda c: c['t_trans'] if c.get('hora_lida') else c['t'])
    for c in link_comps:
        s_ = acha_link(linkst, c['v'], link_ref(c))
        if s_:
            s_['usado'] = True
            c['stone'] = s_
    links_conferidos = [f"{c['caneta'] or c['n']} R$ {brl(c['v'])} → Stone {hm(c['stone']['t'])} R$ {brl(c['stone']['v'])}"
                        for c in link_comps if ini <= c['t'] < fim and c['stone']]

    # ---- pendências no formato do relatório (Felipe, 04/11/2026). Sem conferência de "cartão do dia".
    MOTOS = {'1': 'Nathan', '2': 'Juninho', '3': 'Rodrigo', '4': 'Cooperativa'}
    ped_k = {p['_k']: p for p in alvo}
    comp_k = {c['id']: c for c in comps}

    def ficha(p, c=None):
        if p:
            return dict(cliente=p.get('nome'), pedido=p.get('id'), valor=valor(p.get('valor')), pag=p.get('pag'),
                        vendedor=p.get('vendedor'), entregador=MOTOS.get(str(p.get('motoId')), f"motoboy {p.get('motoId', '?')}"),
                        rota=(p.get('rota') or '-').capitalize())
        return dict(cliente=c['caneta'] or c['n'], pedido=None, valor=c['v'], pag=c['pag'], vendedor=c['vend'],
                    entregador='(pedido não encontrado no MotoRun)', rota='-')

    pend, com_pend = [], set()

    def addp(tipo, ref, f, detalhe='', v=0.0, imagem=None, pedido_k=None):
        pid = f'{tipo}:{ref}'
        if pid in ignorar:
            return
        pend.append(dict(id=pid, tipo=tipo, ficha=f, detalhe=detalhe, valor=v, imagem=imagem))
        if pedido_k:
            com_pend.add(pedido_k)

    for p in alvo:
        pv, c = valor(p.get('valor')), p.get('_comp')
        split = '+' in (p.get('pag') or '') or p.get('pagSplit')
        if not c:
            addp('sem_comprovante', p['_k'], ficha(p), v=pv, pedido_k=p['_k'])
        elif not split and abs(vt(c) - pv) >= 0.01:
            addp('valor_difere', p['_k'], ficha(p, c),
                 f"Comprovante mostra R$ {brl(vt(c))}\nMas o pedido foi lançado R$ {brl(pv)} (verificar)",
                 v=round(abs(pv - vt(c)), 2), imagem=c.get('imagem'), pedido_k=p['_k'])

    # Felipe (19/11/2026): comprovante SEM pedido entregue correspondente ("Comprovantes sem pedido
    # correspondente" no MotoRun) é desconsiderado — às vezes o comprovante é lançado e o pedido só é
    # entregue no dia seguinte (aí ele casa no relatório desse dia, que olha os comprovantes de até 3 dias antes).
    def sem_pedido(c):
        return c.get('pedido') is None

    def repetido_tem_pedido(c):  # comprovante repetido só vale se existe OUTRO pedido entregue que ele pagaria
        nomes = (c['n'], c['caneta'], c['pagador'])
        return any(nome_bate(p.get('nome'), *nomes) and abs(valor(p.get('valor')) - c['v']) < 0.01
                   for p in alvo if not p.get('_comp'))

    aguardando = []
    for c in pix_comps:
        if not (ini <= c['t'] < fim) or c['stone'] or sem_pedido(c):
            continue
        p = ped_k.get(c['pedido'])
        if (c['t_trans'] or c['t']) > ultimo - 1800:
            aguardando.append(f"{c['n']} R$ {brl(c['v'])} ({hm(c['t'])})")
            continue
        quem = f" — pagador: {c['pagador'].title()}" if c['pagador'] else ''
        addp('pix_nao_entrou', c['id'], ficha(p, c), f"PIX de R$ {brl(c['v'])} do comprovante ({hm(c['t_trans'] or c['t'])}) não entrou na Stone{quem}",
             v=c['v'], imagem=c.get('imagem'), pedido_k=c['pedido'])

    links_aguardando = {}
    for c in card_comps:
        if not (ini <= c['t'] < fim) or c['stone'] or sem_pedido(c):
            continue
        ref, com_hora = link_ref(c)
        if ultimo < ref + (3 if com_hora else 6) * 3600:
            aguardando.append(f"{c['caneta'] or c['n']} cartão R$ {brl(c['v'])} ({hm(c['t'])})")
            continue
        p = ped_k.get(c['pedido'])
        addp('cartao_nao_entrou', c['id'], ficha(p, c),
             f"{'Débito' if c['tipo'] == 'debito' else 'Crédito'} de R$ {brl(c['v'])}" + (f" {c.get('parcelas')}x" if c.get('parcelas') else '')
             + f" ({datetime.fromtimestamp(ref, TZ).strftime('%d/%m %H:%M')}) não caiu na Stone", v=c['v'], imagem=c.get('imagem'), pedido_k=c['pedido'])

    # Felipe (21/11/2026): link de pagamento com FOTO do comprovante batendo o VALOR e a DATA do pedido = conferido,
    # sem esperar o crédito na Stone (link em 3x sem antecipação só cai em parcelas de 30 dias).
    def link_foto_ok(c, p):
        if not p or not c.get('lido') or not c.get('t_trans') or abs(c['v'] - valor(p.get('valor'))) >= 0.01:
            return False
        d_foto = datetime.fromtimestamp(c['t_trans'], TZ).date()
        d_ped = {datetime.fromtimestamp(x / 1000, TZ).date() for x in (p.get('ts'), p.get('tsEntregue')) if x}
        return d_foto in d_ped

    for c in link_comps:
        if not (ini <= c['t'] < fim) or c['stone'] or sem_pedido(c):
            continue
        p = ped_k.get(c['pedido'])
        if link_foto_ok(c, p):
            links_conferidos.append(f"{c['caneta'] or c['n']} R$ {brl(c['v'])} → comprovante bate valor e data do pedido")
            continue
        ref, com_hora = link_ref(c)
        prazo = link_prazo(ref)
        if ultimo >= prazo:
            addp('link_nao_entrou', c['id'], ficha(p, c),
                 f"Link de R$ {brl(c['v'])} ({datetime.fromtimestamp(ref, TZ).strftime('%d/%m %H:%M')}) não caiu na Stone"
                 + (f" — pagador: {c['pagador'].title()}" if c['pagador'] else ''), v=c['v'], imagem=c.get('imagem'), pedido_k=c['pedido'])
        else:
            links_aguardando[c['id']] = dict(id=f"link_nao_entrou:{c['id']}", tipo='link_nao_entrou', ficha=ficha(p, c),
                                             detalhe=f"Link de R$ {brl(c['v'])} ({datetime.fromtimestamp(ref, TZ).strftime('%d/%m %H:%M')}) não caiu na Stone"
                                             + (f" — pagador: {c['pagador'].title()}" if c['pagador'] else ''),
                                             valor=c['v'], imagem=None, dia=a.dia, pedido_k=c['pedido'], comp_id=c['id'], comp_dia=c['dia'],
                                             t_comp=ref, com_hora=com_hora, prazo=prazo, foto_valor=c['v'],
                                             nomes=[n for n in (c['n'], c['caneta'], c['pagador']) if n])

    for g in graves:  # comprovante de outra data / mesmo comprovante em 2 lançamentos
        c = comp_k.get(g['id'].split(':', 1)[1])
        if c and g['tipo'] == 'comprovante_repetido' and not repetido_tem_pedido(c):
            continue
        if c and g['tipo'] == 'comprovante_antigo' and sem_pedido(c):
            continue
        p = ped_k.get(c['pedido']) if c else None
        addp(g['tipo'], g['id'].split(':', 1)[1], ficha(p, c) if c else {}, g['texto'], g['valor'], g.get('imagem'), c and c['pedido'])

    conferidos = len([p for p in alvo if p['_k'] not in com_pend])

    # ---- pendências de dias anteriores (Felipe, 10/11/2026): repetem no relatório até achar o pagamento
    #      ou até o Felipe mandar o comprovante (aí: python3 scripts/conciliacao-pagamentos.py resolver <pedido|id> --obs "...")
    estado = carregar_estado()
    antigas, resolvidas_hoje = [], []
    cache_comps = {}

    def comps_do_dia(dd):
        if dd not in cache_comps:
            cache_comps[dd] = fb(f'comprovantes/{dd}.json') or {}
        return cache_comps[dd]

    links_promovidos, links_ok_antes = [], []
    for cid, x in list(estado['links_aguardando'].items()):
        if x['dia'] >= a.dia:
            continue
        cc = comp_k.get(cid)
        s_ = cc['stone'] if cc and cc.get('stone') else acha_link(linkst, x['valor'], (x['t_comp'], x.get('com_hora')))
        if s_:
            s_['usado'] = True
            links_ok_antes.append(cid)
        elif ultimo >= x['prazo']:
            links_promovidos.append(cid)
            estado['abertas'][x['id']] = {k: v for k, v in x.items() if k not in ('prazo', 'com_hora')}

    for pid, x in list(estado['abertas'].items()):
        if x['dia'] >= a.dia:
            continue  # reprocessamento do mesmo dia: a pendência é recalculada abaixo
        motivo = None
        pk = x.get('pedido_k')
        ped_atual = fb(f'pedidos/{pk}.json') if pk else None
        if pk and (not ped_atual or ped_atual.get('status') != 'entregue'):
            motivo = 'pedido removido ou não está mais como entregue no MotoRun'
        elif x['tipo'] == 'sem_comprovante':
            pv, tsp = valor(ped_atual.get('valor')), (ped_atual.get('ts') or 0) / 1000
            d0 = datetime.strptime(x['dia'], '%Y-%m-%d').replace(tzinfo=TZ) - timedelta(days=3)
            dd = d0
            while dd <= dia and not motivo:
                ds = dd.strftime('%Y-%m-%d')
                for ck, cr in comps_do_dia(ds).items():
                    cc = comp_k.get(ck)
                    if cc and cc['pedido'] not in (None, pk):
                        continue  # comprovante já é de outro pedido
                    nomes = (cr.get('nome'), cc['caneta'] if cc else '', cc['pagador'] if cc else '')
                    vv = cc['v'] if cc else valor(cr.get('valor'))
                    if nome_bate(ped_atual.get('nome'), *nomes) and abs(vv - pv) < 0.01 and cr['ts'] / 1000 >= tsp - 600:
                        motivo = f"comprovante localizado (lançado em {hm(cr['ts'] / 1000)} por {cr.get('vendedor', '?')})"
                        break
                dd += timedelta(days=1)
        elif x['tipo'] == 'pix_nao_entrou':
            tc = x.get('t_comp') or 0
            for s_ in pix:
                if not s_['usado'] and abs(s_['v'] - x['valor']) < 0.01 and tc - 2 * 86400 <= s_['t'] and \
                        any(nome_bate(nm, s_['n']) for nm in x.get('nomes', []) if nm):
                    s_['usado'] = True
                    motivo = f"PIX localizado na Stone ({hm(s_['t'])})"
                    break
        elif x['tipo'] == 'cartao_nao_entrou':
            cc = comp_k.get(x.get('comp_id'))
            s_ = cc['stone'] if cc and cc.get('stone') else acha_cartao(cartao, x['valor'], (x.get('t_comp') or 0, False), folga=7)
            if s_:
                s_['usado'] = True
                motivo = f"cartão caiu na Stone ({hm(s_['t'])}, R$ {brl(s_['v'])})"
        elif x['tipo'] == 'link_nao_entrou':
            cc = comp_k.get(x.get('comp_id'))
            s_ = cc['stone'] if cc and cc.get('stone') else acha_link(linkst, x['valor'], (x.get('t_comp') or 0, True), folga=7)
            if s_:
                s_['usado'] = True
                motivo = f"link caiu na Stone ({hm(s_['t'])}, R$ {brl(s_['v'])})"
        elif x['tipo'] == 'valor_difere':
            if ped_atual and abs(valor(ped_atual.get('valor')) - x.get('foto_valor', -1)) < 0.01:
                motivo = 'valor do pedido corrigido no MotoRun'
        elif x.get('comp_id') and not fb(f"comprovantes/{x.get('comp_dia')}/{x['comp_id']}.json"):
            motivo = 'comprovante removido do MotoRun'
        if motivo:
            resolvidas_hoje.append(dict(x, resolvido_em=a.dia, motivo=motivo))
        else:
            if ped_atual:  # atualiza a ficha com o que está no MotoRun hoje
                x['ficha'].update(valor=valor(ped_atual.get('valor')), pag=ped_atual.get('pag'), vendedor=ped_atual.get('vendedor'))
            antigas.append(x)

    texto = relatorio_texto(a.dia, conferidos, pend, antigas, resolvidas_hoje)
    res = dict(dia=a.dia, fotos_lidas=leitura_ok, extrato_stone_ate=hm(ultimo), extrato_completo=extrato_completo,
               pedidos_entregues=len(entregas), exigem_comprovante=len(alvo), conferidos=conferidos,
               comprovantes_do_dia=len(comps_dia), fotos_conferidas=len([c for c in comps_dia if c['lido']]),
               pix_aguardando_stone=aguardando, links_conferidos=links_conferidos,
               links_aguardando_stone=[f"{x['ficha'].get('cliente')} R$ {brl(x['valor'])}" for x in links_aguardando.values()],
               pendencias=pend, tem_pendencia=bool(pend or antigas),
               pendencias_anteriores=antigas, resolvidas_hoje=resolvidas_hoje,
               relatorio_texto=texto, ajustes_internos=[g['texto'] for g in ajustes])

    if a.salvar:
        for x in resolvidas_hoje:
            estado['abertas'].pop(x['id'], None)
            estado['resolvidas'].append(x)
        for pid in [k for k, v in estado['abertas'].items() if v['dia'] == a.dia]:
            estado['abertas'].pop(pid)
        for x in pend:
            ref = x['id'].split(':', 1)[1]
            c = comp_k.get(ref) or (ped_k.get(ref) or {}).get('_comp')
            pk = ref if ref in ped_k else (c or {}).get('pedido')
            estado['abertas'][x['id']] = dict(
                x, dia=a.dia, pedido_k=pk, comp_id=(c or {}).get('id'), comp_dia=(c or {}).get('dia'),
                t_comp=(c['t_trans'] or c['t']) if c else None, foto_valor=c['v'] if c else None,
                nomes=[n for n in ((c or {}).get('n'), (c or {}).get('caneta'), (c or {}).get('pagador'), x['ficha'].get('cliente')) if n],
                imagem=None)
        for cid in links_ok_antes + links_promovidos:
            estado['links_aguardando'].pop(cid, None)
        for cid in [k for k, v in estado['links_aguardando'].items() if v['dia'] == a.dia]:
            estado['links_aguardando'].pop(cid)
        estado['links_aguardando'].update(links_aguardando)
        salvar_estado(estado)
        res['estado_salvo'] = ESTADO

    if a.pdf:
        res['pdf'] = gerar_pdf(res, a.out)
    if a.enviar and entregas:  # Felipe 08/11/2026: envia todo dia que teve venda (com ou sem pendência)
        est = carregar_estado()
        if a.dia in est.get('enviados', []) and not a.forcar:
            res['envio'] = dict(enviado=False, motivo=f'relatório de {a.dia} já tinha sido enviado (use --forcar para mandar de novo)')
        else:
            res['envio'] = enviar(res, a.out)
            if res['envio'].get('enviado'):
                est['enviados'] = sorted(set(est.get('enviados', []) + [a.dia]))[-60:]
                salvar_estado(est)
    print(json.dumps(res, ensure_ascii=False, indent=1))


TITULOS = [('valor_difere', 'com valor que não bate', 'com valor diferente'),
           ('sem_comprovante', 'sem comprovante', 'sem comprovante'),
           ('pix_nao_entrou', 'com PIX que não entrou na Stone', 'com PIX que não entrou na Stone'),
           ('cartao_nao_entrou', 'com cartão que não caiu na Stone', 'com cartão/débito que não caiu na Stone'),
           ('link_nao_entrou', 'com link que não caiu na Stone', 'com link que não caiu na Stone'),
           ('comprovante_antigo', 'com comprovante de outra data', 'com comprovante de outra data'),
           ('comprovante_repetido', 'com comprovante repetido', 'com comprovante repetido')]


def qtd(n):
    return f"{n} {'pedido' if n == 1 else 'pedidos'}"


def contagem(pend):
    return {t: len([x for x in pend if x['tipo'] == t]) for t, _, _ in TITULOS}


ROTULO = {t: tit for t, _, tit in TITULOS}


def ddmm(d):
    return datetime.strptime(d, '%Y-%m-%d').strftime('%d/%m')


def secoes(pend, antigas):
    """[(título, [itens])] na ordem do relatório; itens antigos ganham a linha "Em aberto desde DD/MM"."""
    out = []
    for t, _, tit in TITULOS:
        itens = [x for x in pend if x['tipo'] == t]
        if itens:
            out.append((f'{qtd(len(itens))} {tit}', itens))
    if antigas:
        itens = [dict(x, detalhe=f"Em aberto desde {ddmm(x['dia'])} – {ROTULO.get(x['tipo'], x['tipo'])}" + (('\n' + x['detalhe']) if x.get('detalhe') else ''))
                 for x in sorted(antigas, key=lambda x: x['dia'])]
        out.append((f'{qtd(len(antigas))} de dias anteriores ainda em aberto', itens))
    return out


def relatorio_texto(dia, conferidos, pend, antigas=(), resolvidas=()):
    d = datetime.strptime(dia, '%Y-%m-%d').strftime('%d/%m/%Y')
    n = contagem(pend)
    L = [f'*Relatório geral – {d}*', f'{qtd(conferidos)} conferidos ✅']
    for t, txt, _ in TITULOS:
        if n[t] or t in ('valor_difere', 'sem_comprovante'):
            L.append(f'{qtd(n[t])} {txt}')
    if antigas:
        L.append(f'{qtd(len(antigas))} de dias anteriores ainda em aberto')
    if resolvidas:
        L.append(f"{len(resolvidas)} {'pendência anterior resolvida' if len(resolvidas) == 1 else 'pendências anteriores resolvidas'} ✅")
    sec = secoes(pend, antigas)
    if sec:
        L += ['', '*Relatório de pendências*']
        for tit, itens in sec:
            L += ['', f'*{tit}*']
            for x in itens:
                f = x['ficha']
                L.append('')
                L.append(f"{f.get('cliente')}" + (f" – pedido #{f['pedido']}" if f.get('pedido') else ''))
                if x.get('detalhe'):
                    L += x['detalhe'].split('\n')
                L += [f"Valor: R$ {brl(f.get('valor') or 0)} – {limpa(f.get('pag'))}", f"Vendedor: {f.get('vendedor')}",
                      f"Entregador: {f.get('entregador')}", f"Rota: {f.get('rota')}"]
    if resolvidas:
        L += ['', '*Pendências anteriores resolvidas ✅*']
        for x in resolvidas:
            f = x['ficha']
            L.append(f"{f.get('cliente')}" + (f" – pedido #{f['pedido']}" if f.get('pedido') else '') + f" (desde {ddmm(x['dia'])}): {x['motivo']}")
    return '\n'.join(L)


def limpa(t):
    return re.sub(r'[\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\U0001F300-\U0001FAFF\uFE0F]', '', str(t or '')).strip()


def gerar_pdf(res, out):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from xml.sax.saxutils import escape as esc
    F, FB = 'Helvetica', 'Helvetica-Bold'
    try:
        pdfmetrics.registerFont(TTFont('DJ', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DJB', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        F, FB = 'DJ', 'DJB'
    except Exception:
        pass
    VERDE, VERM, AMAR, CINZA = colors.HexColor('#1a7f37'), colors.HexColor('#c62828'), colors.HexColor('#b26a00'), colors.HexColor('#555555')
    st = lambda **k: ParagraphStyle('x', fontName=k.pop('f', F), fontSize=k.pop('s', 10), leading=k.pop('l', 14), **k)
    d = datetime.strptime(res['dia'], '%Y-%m-%d').strftime('%d/%m/%Y')
    pdf = os.path.join(out, f'conciliacao-{res["dia"]}.pdf')
    n = contagem(res['pendencias'])
    el = [Paragraph('Conciliação de pagamentos', st(f=FB, s=18, l=22)),
          Paragraph(f'Dia {d} · MotoRun × comprovantes × Stone', st(s=10, textColor=CINZA)), Spacer(1, 8 * mm),
          Paragraph('Relatório geral', st(f=FB, s=14, l=18)), Spacer(1, 2 * mm)]
    linhas = [[Paragraph('✓', st(f=FB, s=12, textColor=VERDE)), Paragraph(f'<b>{qtd(res["conferidos"])}</b> conferidos', st(s=11))]]
    for t, txt, _ in TITULOS:
        if n[t] or t in ('valor_difere', 'sem_comprovante'):
            cor = VERM if n[t] else CINZA
            linhas.append([Paragraph('!' if n[t] else '–', st(f=FB, s=12, textColor=cor)), Paragraph(f'<b>{qtd(n[t])}</b> {txt}', st(s=11))])
    tg = Table(linhas, colWidths=[8 * mm, 150 * mm])
    tg.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, -2), 0.3, colors.HexColor('#e0e0e0')),
                            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    if res.get('pendencias_anteriores'):
        tg._cellvalues.append([Paragraph('!', st(f=FB, s=12, textColor=VERM)),
                               Paragraph(f"<b>{qtd(len(res['pendencias_anteriores']))}</b> de dias anteriores ainda em aberto", st(s=11))])
    if res.get('resolvidas_hoje'):
        k = len(res['resolvidas_hoje'])
        tg._cellvalues.append([Paragraph('✓', st(f=FB, s=12, textColor=VERDE)),
                               Paragraph(f"<b>{k}</b> {'pendência anterior resolvida' if k == 1 else 'pendências anteriores resolvidas'}", st(s=11))])
    tg = Table(tg._cellvalues, colWidths=[8 * mm, 150 * mm])
    tg.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, -2), 0.3, colors.HexColor('#e0e0e0')),
                            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    el += [tg, Spacer(1, 8 * mm)]
    sec = secoes(res['pendencias'], res.get('pendencias_anteriores', []))
    if sec:
        el.append(Paragraph('Relatório de pendências', st(f=FB, s=14, l=18)))
        for tit, itens in sec:
            el += [Spacer(1, 4 * mm), Paragraph(esc(tit), st(f=FB, s=11, textColor=VERM))]
            for x in itens:
                f = x['ficha']
                nome = esc(str(f.get('cliente'))) + (f"  <font color='#555555'>· pedido #{f['pedido']}</font>" if f.get('pedido') else '')
                rows = [[Paragraph(nome, st(f=FB, s=11)), '']]
                for dl in (x.get('detalhe') or '').split('\n'):
                    if dl:
                        rows.append([Paragraph(esc(dl), st(s=10, textColor=AMAR, f=FB)), ''])
                for k, v in (('Valor', f"R$ {brl(f.get('valor') or 0)} – {limpa(f.get('pag'))}"), ('Vendedor', f.get('vendedor')),
                             ('Entregador', f.get('entregador')), ('Rota', f.get('rota'))):
                    rows.append([Paragraph(k, st(s=10, textColor=CINZA)), Paragraph(esc(str(v)), st(s=10))])
                tb = Table(rows, colWidths=[28 * mm, 132 * mm])
                sty = [('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f6f6f6')), ('BOX', (0, 0), (-1, -1), 0.4, colors.HexColor('#d0d0d0')),
                       ('LINEBEFORE', (0, 0), (0, -1), 3, VERM), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                       ('LEFTPADDING', (0, 0), (-1, -1), 8)]
                for i in range(len(rows)):
                    if rows[i][1] == '':
                        sty.append(('SPAN', (0, i), (1, i)))
                tb.setStyle(TableStyle(sty))
                el += [Spacer(1, 3 * mm), KeepTogether(tb)]
    else:
        el.append(Paragraph('Nenhuma pendência.', st(s=11, textColor=VERDE)))
    if res.get('resolvidas_hoje'):
        el += [Spacer(1, 8 * mm), Paragraph('Pendências anteriores resolvidas', st(f=FB, s=12, l=16, textColor=VERDE))]
        for x in res['resolvidas_hoje']:
            f = x['ficha']
            el.append(Paragraph(esc(f"✓ {f.get('cliente')}" + (f" – pedido #{f['pedido']}" if f.get('pedido') else '') +
                                    f" (desde {ddmm(x['dia'])}): {x['motivo']}"), st(s=10)))
    SimpleDocTemplate(pdf, pagesize=A4, title=f'Conciliação {d}', leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=18 * mm, bottomMargin=18 * mm).build(el)
    return pdf


def enviar(res, out):
    d = datetime.strptime(res['dia'], '%Y-%m-%d').strftime('%d/%m/%Y')
    pdf = gerar_pdf(res, out)

    tok = os.environ['WA_TOKEN']
    def curl(args):
        o = subprocess.run(['curl', '-sS', '--max-time', '60', '-H', f'Authorization: Bearer {tok}'] + args, capture_output=True).stdout
        return json.loads(o or b'{}')
    up = curl(['-X', 'POST', f'https://graph.facebook.com/v21.0/{PHONE_ID}/media', '-F', 'messaging_product=whatsapp',
               '-F', 'type=application/pdf', '-F', f'file=@{pdf};type=application/pdf;filename=conciliacao.pdf'])
    n = contagem(res['pendencias'])
    outros = n['comprovante_antigo'] + n['comprovante_repetido']
    ant = {t: len([x for x in res.get('pendencias_anteriores', []) if x['tipo'] == t]) for t, _, _ in TITULOS}
    def par(hoje, antes):
        return qtd(hoje) + (f' (+{antes} de dias anteriores)' if antes else '')
    params = [d, qtd(res['conferidos']), par(n['valor_difere'], ant['valor_difere']), par(n['sem_comprovante'], ant['sem_comprovante']),
              par(n['pix_nao_entrou'] + outros, ant['pix_nao_entrou'] + ant['comprovante_antigo'] + ant['comprovante_repetido'])]
    body = {'messaging_product': 'whatsapp', 'to': DESTINO, 'type': 'template', 'template': {
        'name': 'alerta_conciliacao', 'language': {'code': 'pt_BR'}, 'components': [
            {'type': 'header', 'parameters': [{'type': 'document', 'document': {'id': up.get('id'), 'filename': f'Conciliacao {d.replace("/", "-")}.pdf'}}]},
            {'type': 'body', 'parameters': [{'type': 'text', 'text': x} for x in params]}]}}
    r = curl(['-X', 'POST', f'https://graph.facebook.com/v21.0/{PHONE_ID}/messages', '-H', 'Content-Type: application/json',
              '--data-binary', json.dumps(body, ensure_ascii=False)])
    return dict(enviado=bool(r.get('messages')), resposta=r, pdf=pdf)


if __name__ == '__main__':
    main()
