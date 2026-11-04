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
 - Cartão/débito/link: cupom ↔ crédito "Antecipação/Recebimento vendas" de 5 a 60 min depois da hora do cupom, líquido
   entre 90% e 99,5% do valor. A Stone às vezes junta vendas, então também confere o total do dia
   (loja + delivery x Stone, entre 93% e 99,5%).
 - Voucher cai depois (Alelo/Pluxee etc.): só confere se tem comprovante.
 - GRAVE (manda WhatsApp): PIX que não entrou, entrega sem comprovante, comprovante de outra data,
   comprovante repetido de outro pedido, cartão do dia que não bate.
 - Ajustes (só vão no relatório se ele for enviado): lançamento duplicado, forma de pagamento ou valor diferente
   da foto, PIX entrou mas o pedido continua "PIX Pendente", foto ilegível.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dia')
    ap.add_argument('--stone', required=True)
    ap.add_argument('--extrair', action='store_true')
    ap.add_argument('--leitura')
    ap.add_argument('--ignorar', default='')
    ap.add_argument('--enviar', action='store_true')
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
                              t_trans=None, idtx=''))
    comps.sort(key=lambda c: c['t'])
    comps_dia = [c for c in comps if ini <= c['t'] < fim]

    if a.extrair:
        ficha = []
        for c in comps_dia:
            arq = fb(f'comprovantes_arq/{c["dia"]}/{c["id"]}.json')
            achados = re.findall(r'data:([\w/+.-]+);base64,([A-Za-z0-9+/=]+)', json.dumps(arq))
            imgs = []
            for j, (mt, b) in enumerate(achados):
                ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'application/pdf': 'pdf', 'image/webp': 'webp'}.get(mt, 'bin')
                fn = os.path.join(a.out, f'{c["id"]}_{j}.{ext}')
                open(fn, 'wb').write(base64.b64decode(b))
                imgs.append(fn)
            ficha.append(dict(id=c['id'], lancado=dict(nome=c['n'], valor=c['v'], pag=c['pag'], vendedor=c['vend'], hora=hm(c['t'])),
                              imagens=imgs, leitura=dict(tipo=None, valor=None, data=None, hora=None, pagador='', nome_caneta='',
                                                        parcelas=None, id_transacao='', legivel=None)))
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
            c['idtx'] = re.sub(r'\s', '', str(L.get('id_transacao') or '')).upper()
            if L.get('tipo') and L['tipo'] != c['tipo']:
                add(ajustes, 'tipo_diferente', f"{c['n']} R$ {brl(c['v'])} ({c['vend']}): lançado como {c['pag']}, mas a foto é {L['tipo'].upper()}"
                    + (f" {L['parcelas']}x" if L.get('parcelas') else '') + ' — corrigir no MotoRun', c['id'], extra={'imagem': c['imagem']})
                c['tipo'] = L['tipo']
            if L.get('valor') is not None and abs(valor(L['valor']) - c['v']) >= 0.01:
                add(ajustes, 'valor_diferente', f"{c['n']}: lançado R$ {brl(c['v'])}, mas a foto mostra R$ {brl(valor(L['valor']))} ({c['vend']}) — corrigir no MotoRun",
                    c['id'], extra={'imagem': c['imagem']})
                c['v'] = valor(L['valor'])
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
    pix, cartao = [], []
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
            cartao.append(item)
    if not pix and not cartao:
        print(json.dumps({'erro': 'extrato da Stone vazio'}))
        sys.exit(2)
    ultimo = max(x['t'] for x in pix + cartao)
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
    loja_cartao = [p for p in pedidos if p.get('rota') == 'loja' and eh_cartao(tipo_de(p.get('pag')))]
    entregas = [p for p in pedidos if p.get('rota') not in ('loja', 'retirada')]

    def precisa_comp(p):
        pg = norm(p.get('pag'))
        if 'STRONGEST' in norm(p.get('nome')) or 'SITE' in pg:
            return False
        return bool(re.search(r'PIX|CART|DEBITO|LINK|VOUCHER|CREDITO', pg)) and valor(p.get('valor')) > 0

    alvo = sorted([p for p in entregas if precisa_comp(p)], key=lambda p: p.get('ts') or 0)

    # ---- pedido x comprovante (nome à caneta > nome lançado > pagador da foto)
    livres = [c for c in comps if not c.get('duplicado') and not c.get('repetido')]
    for rodada in ('caneta', 'qualquer'):
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
            cand = [c for c in livres if c['pedido'] is None and bate(c)
                    and (abs(c['v'] - pv) < 0.5 or (split and c['v'] <= pv + 0.01))]
            cand = [c for c in cand if c['t'] >= tsp - 600] or [c for c in cand if c['t'] >= tsp - 3 * 86400 and abs(c['v'] - pv) < 0.01]
            if cand:
                c = min(cand, key=lambda c: abs(c['t'] - (p.get('tsEntregue') or 0) / 1000))
                c['pedido'] = p['_k']
                p['_comp'] = c

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

    # ---- cupom de cartão x Stone (quando há hora na foto)
    for c in sorted([c for c in comps_dia if eh_cartao(c['tipo']) and c['t_trans'] and c.get('hora_lida') and not c.get('duplicado')],
                    key=lambda c: c['t_trans']):
        cand = [s for s in cartao if not s['usado'] and 300 <= s['t'] - c['t_trans'] <= 3600 and 0.90 * c['v'] <= s['v'] <= 0.995 * c['v']]
        if cand:
            s = min(cand, key=lambda s: s['t'] - c['t_trans'])
            s['usado'] = True
            c['stone'] = s

    for c in pix_comps:
        if not (ini <= c['t'] < fim) or c['stone']:
            continue
        ref = c['t_trans'] or c['t']
        if ref > ultimo - 1800:
            add(ajustes, 'pix_aguardando', f"PIX {c['n']} R$ {brl(c['v'])} ({c['vend']}, {hm(c['t'])}) — extrato da Stone ainda não chegou nesse horário", c['id'], c['v'])
        else:
            quem = f" (pagador na foto: {c['pagador'].title()})" if c['pagador'] else ''
            add(graves, 'pix_nao_entrou', f"PIX NÃO ENTROU na Stone: {c['n']} R$ {brl(c['v'])}{quem} — lançado por {c['vend']} em {hm(c['t'])}", c['id'], c['v'],
                {'imagem': c.get('imagem')})

    for p in alvo:
        nome, pv = p.get('nome'), valor(p.get('valor'))
        tag = f"#{p.get('id')} {nome} R$ {brl(pv)} ({p.get('pag')}, {p.get('vendedor')}, motoboy {p.get('motoId', '?')})"
        c = p.get('_comp')
        if not c:
            add(graves, 'sem_comprovante', f"ENTREGA SEM COMPROVANTE: {tag} — {p.get('end', '')}", p['_k'], pv,
                {'pedido': p.get('id'), 'pag': p.get('pag')})
            continue
        tp = tipo_de(p.get('pag'))
        if tp != c['tipo'] and '+' not in (p.get('pag') or '') and not any(g['id'] == f"tipo_diferente:{c['id']}" for g in ajustes):
            add(ajustes, 'forma_diferente', f"{tag}: no pedido está {p.get('pag')}, mas o comprovante é {c['tipo'].upper()} — corrigir no MotoRun", c['id'])
        if 'PENDENTE' in norm(p.get('pag')) and c.get('stone'):
            add(ajustes, 'pix_pendente', f"{tag}: PIX já entrou na Stone — marcar como pago no MotoRun", p['_k'])

    # ---- cartão: total do dia
    bruto_loja = sum(valor(p.get('valor')) for p in loja_cartao)
    bruto_entr = sum(valor(p.get('valor')) for p in alvo if eh_cartao(tipo_de(p.get('pag'))))
    bruto = round(bruto_loja + bruto_entr, 2)
    liquido = round(sum(s['v'] for s in cartao if ini <= s['t'] < fim + 2 * 3600), 2)
    cartao_res = dict(bruto_loja=bruto_loja, bruto_delivery=bruto_entr, bruto=bruto, liquido_stone=liquido,
                      percentual=round(liquido / bruto * 100, 2) if bruto else None,
                      cupons_conferidos=len([c for c in comps_dia if eh_cartao(c['tipo']) and c['stone']]),
                      cupons_do_dia=len([c for c in comps_dia if eh_cartao(c['tipo']) and not c.get('duplicado')]))
    if bruto and extrato_completo and not (0.93 * bruto <= liquido <= 0.995 * bruto):
        falta = round(bruto * 0.96 - liquido, 2)
        if falta > 0:
            add(graves, 'cartao_dia', f"CARTÃO DO DIA NÃO BATE: vendas brutas R$ {brl(bruto)} (loja {brl(bruto_loja)} + delivery {brl(bruto_entr)}), "
                f"caiu na Stone R$ {brl(liquido)} ({cartao_res['percentual']}%) — falta ~R$ {brl(falta)}", 'dia', falta)
        else:
            add(ajustes, 'cartao_sobra', f"Cartão: caiu na Stone R$ {brl(liquido)}, mais que as vendas registradas (R$ {brl(bruto)}) — alguma venda no cartão sem pedido/NFC-e", 'dia')
    if cartao_res['percentual'] and not any(g['tipo'] == 'cartao_dia' for g in graves):
        for g in graves:
            if g['tipo'] == 'sem_comprovante' and tipo_de(g.get('pag')) != 'pix':
                g['texto'] += ' (o total de cartão do dia bateu com a Stone: o dinheiro provavelmente entrou, falta lançar o cupom)'

    res = dict(dia=a.dia, fotos_lidas=leitura_ok, extrato_stone_ate=hm(ultimo), extrato_completo=extrato_completo,
               pedidos_entregues=len(entregas), exigem_comprovante=len(alvo), comprovantes_do_dia=len(comps_dia),
               fotos_conferidas=len([c for c in comps_dia if c['lido']]),
               pix_do_dia=len([c for c in pix_comps if ini <= c['t'] < fim]),
               pix_na_stone=len([c for c in pix_comps if ini <= c['t'] < fim and c['stone']]),
               cartao=cartao_res, graves=graves, ajustes=ajustes, valor_grave=round(sum(g['valor'] for g in graves), 2))
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
    c = res['cartao']
    el = [Paragraph(f'Conciliação de pagamentos — {d}', ss['Title']),
          Paragraph(f"Entregas que exigem comprovante: {res['exigem_comprovante']} · comprovantes do dia: {res['comprovantes_do_dia']} "
                    f"(fotos conferidas: {res['fotos_conferidas']}) · PIX na Stone: {res['pix_na_stone']}/{res['pix_do_dia']} · "
                    f"extrato Stone até {res['extrato_stone_ate']}", ss['Normal']), Spacer(1, 10),
          Paragraph('Pendências (dinheiro não entrou / comprovante faltando)', ss['Heading2'])]
    el += [Paragraph('• ' + g['texto'], ss['Normal']) for g in res['graves']]
    el += [Spacer(1, 8), Paragraph('Cartão do dia', ss['Heading2']),
           Paragraph(f"Vendas brutas R$ {brl(c['bruto'])} (loja {brl(c['bruto_loja'])} + delivery {brl(c['bruto_delivery'])}) · "
                     f"líquido na Stone R$ {brl(c['liquido_stone'])} ({c['percentual']}%) · cupons conferidos {c['cupons_conferidos']}/{c['cupons_do_dia']}", ss['Normal'])]
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
    nomes = {'pix_nao_entrou': 'PIX não entrou', 'sem_comprovante': 'entrega sem comprovante', 'cartao_dia': 'cartão do dia não bate',
             'comprovante_antigo': 'comprovante de outra data', 'comprovante_repetido': 'comprovante repetido'}
    tipos = {}
    for g in res['graves']:
        tipos[g['tipo']] = tipos.get(g['tipo'], 0) + 1
    resumo = '; '.join(f'{n} {nomes.get(t, t)}' for t, n in tipos.items())
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
