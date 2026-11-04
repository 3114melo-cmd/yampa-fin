#!/usr/bin/env python3
"""Conciliação diária de pagamentos do MotoRun (pedidos entregues x comprovantes x extrato Stone).

Uso:
  python3 scripts/conciliacao-pagamentos.py AAAA-MM-DD --stone /tmp/stone.json [--ignorar id1,id2] [--enviar]

AAAA-MM-DD = dia conferido (normalmente ONTEM — a Stone só libera o dia completo na madrugada seguinte).
--stone    = extrato da conta Stone (lista de transações do BANCO MCP, ou o objeto com "results"),
             cobrindo de 3 dias antes até o dia conferido.
--ignorar  = ids de pendências que o Claude já resolveu olhando a imagem do comprovante.
--enviar   = se houver pendência GRAVE, gera o PDF e manda no WhatsApp (modelo alerta_conciliacao).
             Precisa de WA_TOKEN no ambiente. Sem pendência grave, não envia nada.

Regras (Felipe, 03/11/2026):
 - Pedidos que exigem comprovante: entregues no dia por motoboy (rota ≠ loja/retirada), pagos por PIX, cartão,
   débito, link ou voucher. Não exigem: dinheiro, "Site Pago", entregas da Strongest.
 - O comprovante pode ter sido lançado até 3 dias antes da entrega (cliente que paga antes).
 - PIX do comprovante tem que estar na Stone: mesmo valor, de 2 dias antes até 1h depois do lançamento,
   preferindo o pagador com o mesmo nome.
 - Cartão/débito/link: a Stone mostra só o líquido antecipado, sem nome, e às vezes junta vendas. A conferência é
   pelo total do dia: (vendas brutas no cartão da loja + delivery) x (créditos de cartão na Stone no dia).
   Bate se o líquido ficar entre 93% e 99,5% do bruto.
 - Voucher cai depois (Alelo/Pluxee etc.): só confere se tem comprovante.
 - GRAVE (manda WhatsApp): PIX que não entrou, entrega sem comprovante, cartão do dia que não bate.
 - Ajustes (só vão no relatório se ele for enviado): lançamento duplicado, forma de pagamento diferente
   entre pedido e comprovante, PIX entrou mas o pedido continua "PIX Pendente".
"""
import json, sys, os, re, subprocess, unicodedata, argparse
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=-3))
FB = 'https://delivery-dhfit-4a399-default-rtdb.firebaseio.com'
PHONE_ID = '1413683815156427'
DESTINO = os.environ.get('DESTINO', '5584986327208')
CARTAO_RX = re.compile(r'cart|d[eé]bito|link|cr[eé]dito', re.I)


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


def nome_bate(a, b):
    ta, tb = toks(a), norm(b)
    return any(w[:4] in tb for w in ta)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dia')
    ap.add_argument('--stone', required=True)
    ap.add_argument('--ignorar', default='')
    ap.add_argument('--enviar', action='store_true')
    ap.add_argument('--out', default='/tmp/conciliacao')
    a = ap.parse_args()
    ignorar = set(x for x in a.ignorar.split(',') if x)
    os.makedirs(a.out, exist_ok=True)

    dia = datetime.strptime(a.dia, '%Y-%m-%d').replace(tzinfo=TZ)
    ini, fim = dia.timestamp(), (dia + timedelta(days=1)).timestamp()

    # ---- Stone
    st = json.load(open(a.stone))
    if isinstance(st, dict):
        st = st.get('results', [])
    pix, cartao = [], []
    for r in st:
        if r.get('type') != 'CREDIT':
            continue
        t = datetime.fromisoformat(r['date'].replace('Z', '+00:00')).timestamp()
        desc = r.get('description') or ''
        pd = r.get('paymentData') or {}
        payer = pd.get('payer')
        if isinstance(payer, dict):
            payer = payer.get('name')
        item = dict(t=t, v=round(float(r['amount']), 2), n=norm(f'{payer or ""} {desc}'), desc=desc, usado=False)
        if 'PIX' in norm(desc) or r.get('operationType') == 'PIX':
            pix.append(item)
        elif 'RECEBIMENTO' in norm(desc) and ('VENDA' in norm(desc)):
            cartao.append(item)
    if not pix and not cartao:
        print(json.dumps({'erro': 'extrato da Stone vazio'}))
        sys.exit(2)
    ultimo = max(x['t'] for x in pix + cartao)
    extrato_completo = ultimo >= fim - 3 * 3600  # tem movimento depois das 21h do dia

    # ---- MotoRun
    comps = []
    for d in range(3, -1, -1):
        dd = (dia - timedelta(days=d)).strftime('%Y-%m-%d')
        for k, r in (fb(f'comprovantes/{dd}.json') or {}).items():
            comps.append(dict(id=k, dia=dd, t=r['ts'] / 1000, v=valor(r.get('valor')), n=r.get('nome', ''),
                              pag=r.get('pag', ''), vend=r.get('vendedor', ''), obs=r.get('obs', ''), pedido=None, stone=None))
    comps.sort(key=lambda c: c['t'])
    comps_dia = [c for c in comps if ini <= c['t'] < fim]

    url = f'pedidos.json?orderBy=%22tsEntregue%22&startAt={int(ini*1000)}&endAt={int(fim*1000)}'
    try:
        ped = fb(url)
        if isinstance(ped, dict) and 'error' in ped:
            ped = {}
    except Exception:
        ped = {}
    if not ped:  # índice orderBy pode não existir no Firebase → baixa tudo e filtra
        ped = {k: v for k, v in fb('pedidos.json').items() if isinstance(v, dict) and ini * 1000 <= (v.get('tsEntregue') or 0) < fim * 1000}
    pedidos = [dict(v, _k=k) for k, v in ped.items() if isinstance(v, dict) and v.get('status') == 'entregue']

    loja_cartao = [p for p in pedidos if p.get('rota') == 'loja' and CARTAO_RX.search(p.get('pag', ''))]
    entregas = [p for p in pedidos if p.get('rota') not in ('loja', 'retirada')]

    def precisa_comp(p):
        pg = norm(p.get('pag'))
        if 'STRONGEST' in norm(p.get('nome')) or 'SITE' in pg:
            return False
        return bool(re.search(r'PIX|CART|DEBITO|LINK|VOUCHER|CREDITO', pg))

    alvo = sorted([p for p in entregas if precisa_comp(p) and valor(p.get('valor')) > 0], key=lambda p: p.get('ts') or 0)

    # ---- duplicados (mesmo nome, valor e vendedor em até 2 min)
    dup = []
    for i, c in enumerate(comps_dia):
        for c2 in comps_dia[:i]:
            if c2['n'] == c['n'] and c2['v'] == c['v'] and abs(c2['t'] - c['t']) < 120 and c2 not in dup:
                dup.append(c)
                c['duplicado'] = c2['id']
                break

    # ---- pedido x comprovante
    livres = [c for c in comps if not c.get('duplicado')]
    for p in alvo:
        pv = valor(p.get('valor'))
        split = '+' in (p.get('pag') or '') or p.get('pagSplit')
        cand = [c for c in livres if c['pedido'] is None and nome_bate(p.get('nome'), c['n'])
                and (abs(c['v'] - pv) < 0.5 or (split and c['v'] <= pv + 0.01))]
        # comprovante lançado antes do pedido existir (mais de 10 min) não serve, exceto se for do mesmo dia da criação
        tsp = (p.get('ts') or 0) / 1000
        cand = [c for c in cand if c['t'] >= tsp - 600] or [c for c in cand if c['t'] >= tsp - 3 * 86400 and abs(c['v'] - pv) < 0.01 and c['t'] < tsp]
        if cand:
            c = min(cand, key=lambda c: abs(c['t'] - (p.get('tsEntregue') or 0) / 1000))
            c['pedido'] = p['_k']
            p['_comp'] = c

    # ---- comprovante PIX x Stone
    def acha_pix(c, exigir_nome):
        cand = [s for s in pix if not s['usado'] and abs(s['v'] - c['v']) < 0.01 and -2 * 86400 <= s['t'] - c['t'] <= 3600]
        if exigir_nome:
            cand = [s for s in cand if nome_bate(c['n'], s['n'])]
        else:
            cand = [s for s in cand if abs(s['t'] - c['t']) <= 3600]
        if cand:
            s = min(cand, key=lambda s: abs(s['t'] - c['t']))
            s['usado'] = True
            c['stone'] = s
            c['stone_nome'] = not exigir_nome or nome_bate(c['n'], s['n'])

    ped_por_k = {p['_k']: p for p in alvo}
    def pedido_cartao(c):  # comprovante lançado como PIX mas o pedido é cartão (ex.: cupom da maquininha)
        p = ped_por_k.get(c['pedido'])
        return bool(p) and 'PIX' not in norm(p.get('pag'))
    pix_comps = [c for c in comps if 'PIX' in norm(c['pag']) and not c.get('duplicado') and not pedido_cartao(c)]
    for exigir in (True, False):
        for c in pix_comps:
            if not c['stone']:
                acha_pix(c, exigir)

    graves, ajustes = [], []

    def add(lista, tipo, txt, ref, valor_=0.0, extra=None):
        pid = f'{tipo}:{ref}'
        if pid in ignorar:
            return
        lista.append(dict(id=pid, tipo=tipo, texto=txt, valor=valor_, **(extra or {})))

    for c in pix_comps:
        if ini <= c['t'] < fim and not c['stone']:
            if c['t'] > ultimo - 1800:
                add(ajustes, 'pix_aguardando', f"PIX {c['n']} R$ {brl(c['v'])} ({c['vend']}, {hm(c['t'])}) — extrato da Stone ainda não chegou nesse horário", c['id'], c['v'])
            else:
                add(graves, 'pix_nao_entrou', f"PIX NÃO ENTROU na Stone: {c['n']} R$ {brl(c['v'])} — lançado por {c['vend']} em {hm(c['t'])}", c['id'], c['v'],
                    {'comprovante': c['id'], 'dia_comprovante': c['dia']})
        elif ini <= c['t'] < fim and c['stone'] and not c.get('stone_nome'):
            add(ajustes, 'pix_outro_nome', f"PIX {c['n']} R$ {brl(c['v'])} entrou, mas o pagador na Stone é {c['stone']['desc'].split('|')[-1].strip().title()} ({hm(c['stone']['t'])})", c['id'], 0,
                {'comprovante': c['id'], 'dia_comprovante': c['dia']})

    for p in alvo:
        nome, pv = p.get('nome'), valor(p.get('valor'))
        tag = f"#{p.get('id')} {nome} R$ {brl(pv)} ({p.get('pag')}, {p.get('vendedor')}, motoboy {p.get('motoId', '?')})"
        c = p.get('_comp')
        if not c:
            add(graves, 'sem_comprovante', f"ENTREGA SEM COMPROVANTE: {tag} — {p.get('end', '')}", p['_k'], pv,
                {'pedido': p.get('id'), 'tel': p.get('tel', ''), 'pag': p.get('pag'), 'tsEntregue': (p.get('tsEntregue') or 0) / 1000})
            continue
        pp, cp = norm(p.get('pag')), norm(c['pag'])
        tipo_p = 'PIX' if 'PIX' in pp else 'CARTAO'
        tipo_c = 'PIX' if 'PIX' in cp else 'CARTAO'
        if tipo_p != tipo_c and '+' not in (p.get('pag') or ''):
            add(ajustes, 'forma_diferente', f"{tag}: pedido está como {p.get('pag')}, comprovante lançado como {c['pag']} — conferir a imagem", c['id'], 0,
                {'comprovante': c['id'], 'dia_comprovante': c['dia']})
        if 'PENDENTE' in pp and c.get('stone'):
            add(ajustes, 'pix_pendente', f"{tag}: PIX já entrou na Stone — marcar como pago no MotoRun", p['_k'])

    for c in dup:
        add(ajustes, 'duplicado', f"Comprovante lançado 2 vezes: {c['n']} R$ {brl(c['v'])} ({c['vend']}, {hm(c['t'])}) — apagar um", c['id'])

    # ---- cartão: total do dia
    def eh_cartao(pag):
        return bool(CARTAO_RX.search(pag or '')) and 'VOUCHER' not in norm(pag)
    bruto_loja = sum(valor(p.get('valor')) for p in loja_cartao if eh_cartao(p.get('pag')))
    bruto_entr = sum(valor(p.get('valor')) for p in alvo if eh_cartao(p.get('pag')))
    bruto = round(bruto_loja + bruto_entr, 2)
    liquido = round(sum(s['v'] for s in cartao if ini <= s['t'] < fim + 2 * 3600), 2)
    cartao_res = dict(bruto_loja=bruto_loja, bruto_delivery=bruto_entr, bruto=bruto, liquido_stone=liquido,
                      percentual=round(liquido / bruto * 100, 2) if bruto else None)
    if bruto and extrato_completo and not (0.93 * bruto <= liquido <= 0.995 * bruto):
        falta = round(bruto * 0.96 - liquido, 2)
        if falta > 0:
            add(graves, 'cartao_dia', f"CARTÃO DO DIA NÃO BATE: vendas brutas R$ {brl(bruto)} (loja {brl(bruto_loja)} + delivery {brl(bruto_entr)}), "
                f"caiu na Stone R$ {brl(liquido)} ({cartao_res['percentual']}%) — falta ~R$ {brl(falta)}", 'dia', falta)
        else:
            add(ajustes, 'cartao_sobra', f"Cartão: caiu na Stone R$ {brl(liquido)}, mais que as vendas registradas (R$ {brl(bruto)}) — alguma venda no cartão sem pedido/NFC-e", 'dia')

    if cartao_res['percentual'] and not any(g['tipo'] == 'cartao_dia' for g in graves):
        for g in graves:
            if g['tipo'] == 'sem_comprovante' and 'PIX' not in norm(g.get('pag')):
                g['texto'] += ' (o total de cartão do dia bateu com a Stone: o dinheiro provavelmente entrou, falta lançar o cupom)'

    # imagens das pendências, para o Claude abrir e conferir
    imgs = {}
    for g in graves + ajustes:
        cid, dd = g.get('comprovante'), g.get('dia_comprovante')
        if cid and cid not in imgs:
            arq = fb(f'comprovantes_arq/{dd}/{cid}.json')
            m = re.search(r'data:([\w/+.-]+);base64,([A-Za-z0-9+/=]+)', json.dumps(arq))
            if m:
                import base64
                ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'application/pdf': 'pdf'}.get(m.group(1), 'bin')
                fn = os.path.join(a.out, f'{cid}.{ext}')
                open(fn, 'wb').write(base64.b64decode(m.group(2)))
                imgs[cid] = fn
        if cid in imgs:
            g['imagem'] = imgs[cid]

    res = dict(dia=a.dia, extrato_stone_ate=hm(ultimo), extrato_completo=extrato_completo,
               pedidos_entregues=len(entregas), exigem_comprovante=len(alvo), comprovantes_do_dia=len(comps_dia),
               pix_do_dia=len([c for c in pix_comps if ini <= c['t'] < fim]), cartao=cartao_res,
               graves=graves, ajustes=ajustes, valor_grave=round(sum(g['valor'] for g in graves), 2))

    if a.enviar and graves:
        res['envio'] = enviar(res, a.out)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def enviar(res, out):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    d = datetime.strptime(res['dia'], '%Y-%m-%d').strftime('%d/%m/%Y')
    pdf = os.path.join(out, f'conciliacao-{res["dia"]}.pdf')
    ss = getSampleStyleSheet()
    el = [Paragraph(f'Conciliação de pagamentos — {d}', ss['Title']),
          Paragraph(f"Entregas que exigem comprovante: {res['exigem_comprovante']} · comprovantes do dia: {res['comprovantes_do_dia']} · "
                    f"extrato Stone até {res['extrato_stone_ate']}", ss['Normal']), Spacer(1, 10),
          Paragraph('Pendências (dinheiro não entrou / comprovante faltando)', ss['Heading2'])]
    el += [Paragraph('• ' + g['texto'], ss['Normal']) for g in res['graves']]
    c = res['cartao']
    el += [Spacer(1, 8), Paragraph('Cartão do dia', ss['Heading2']),
           Paragraph(f"Vendas brutas R$ {brl(c['bruto'])} (loja {brl(c['bruto_loja'])} + delivery {brl(c['bruto_delivery'])}) · "
                     f"líquido na Stone R$ {brl(c['liquido_stone'])} ({c['percentual']}%)", ss['Normal'])]
    if res['ajustes']:
        el += [Spacer(1, 8), Paragraph('Ajustes de lançamento no MotoRun', ss['Heading2'])]
        el += [Paragraph('• ' + g['texto'], ss['Normal']) for g in res['ajustes']]
    SimpleDocTemplate(pdf, pagesize=A4).build(el)

    tok = os.environ['WA_TOKEN']
    def curl(args):
        o = subprocess.run(['curl', '-sS', '--max-time', '60', '-H', f'Authorization: Bearer {tok}'] + args, capture_output=True).stdout
        return json.loads(o or b'{}')
    up = curl(['-X', 'POST', f'https://graph.facebook.com/v21.0/{PHONE_ID}/media', '-F', 'messaging_product=whatsapp',
               '-F', 'type=application/pdf', '-F', f'file=@{pdf};type=application/pdf;filename=conciliacao.pdf'])
    tipos = {}
    for g in res['graves']:
        tipos[g['tipo']] = tipos.get(g['tipo'], 0) + 1
    nomes = {'pix_nao_entrou': 'PIX não entrou', 'sem_comprovante': 'entrega sem comprovante', 'cartao_dia': 'cartão do dia não bate'}
    resumo = '; '.join(f'{n} {nomes[t]}' for t, n in tipos.items())
    body = {'messaging_product': 'whatsapp', 'to': DESTINO, 'type': 'template', 'template': {
        'name': 'alerta_conciliacao', 'language': {'code': 'pt_BR'}, 'components': [
            {'type': 'header', 'parameters': [{'type': 'document', 'document': {'id': up.get('id'), 'filename': f'Conciliacao {d.replace("/", "-")}.pdf'}}]},
            {'type': 'body', 'parameters': [{'type': 'text', 'text': x} for x in
                                            (d, str(len(res['graves'])), brl(res['valor_grave']), resumo)]}]}}
    r = curl(['-X', 'POST', f'https://graph.facebook.com/v21.0/{PHONE_ID}/messages', '-H', 'Content-Type: application/json',
              '--data-binary', json.dumps(body, ensure_ascii=False)])
    return dict(enviado=bool(r.get('messages')), resposta=r, pdf=pdf)


if __name__ == '__main__':
    main()
