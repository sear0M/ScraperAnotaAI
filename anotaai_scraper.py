import argparse
import csv
import http.client
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

API = "https://api.anota.ai"
SITE = "https://pedido.anota.ai"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Origin": SITE,
    "Referer": SITE + "/",
}

DIAS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
DIAS_PT = {"sun": "dom", "mon": "seg", "tue": "ter", "wed": "qua", "thu": "qui", "fri": "sex", "sat": "sab"}

MODELOS_PRECO = {0: "soma", 1: "media", 2: "maior_valor"}

TIPOS_PIZZA = {"flavor": "sabor", "edge": "borda", "pasta": "massa", "additional": "adicional"}

PROFUNDIDADE_MAX = 8


class ErroScraper(Exception):
    pass


class LojaNaoEncontrada(ErroScraper):
    pass


class ErroHTTP(ErroScraper):
    def __init__(self, codigo, url):
        super().__init__(f"HTTP {codigo} em {url}")
        self.codigo = codigo


def requisicao(url, headers=None, tentativas=3, binario=False):
    h = dict(HEADERS)
    if headers:
        h.update(headers)

    erro = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=30) as resp:
                conteudo = resp.read()
            if binario:
                return conteudo
            return conteudo.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            erro = ErroHTTP(e.code, url)
            if e.code < 500 and e.code != 429:
                raise erro
        except (OSError, http.client.HTTPException) as e:
            erro = ErroScraper(f"Falha de conexão em {url}: {e}")

        if i < tentativas - 1:
            time.sleep(3)

    raise erro


def get_json(url, headers=None, tentativas=3):
    texto = requisicao(url, headers, tentativas)
    try:
        return json.loads(texto)
    except ValueError:
        raise ErroScraper(f"Resposta inválida de {url}")


class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def pegar_redirect(url):
    opener = urllib.request.build_opener(SemRedirect)
    try:
        opener.open(urllib.request.Request(url, headers=HEADERS), timeout=30).close()
        return None
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400 and e.headers.get("Location"):
            return urllib.parse.urljoin(url, e.headers["Location"])
        raise ErroHTTP(e.code, url)
    except (OSError, http.client.HTTPException) as e:
        raise ErroScraper(f"Falha de conexão em {url}: {e}")
    except ValueError:
        raise ErroScraper(f"Link inválido: {url}")


def slug_do_token(token):
    url = f"{API}/noauth/access/get-data-by-token/slug?access_token={urllib.parse.quote(token, safe='')}"
    try:
        slug = get_json(url, tentativas=1)["data"]["slug"]
    except (ErroHTTP, KeyError, TypeError):
        slug = None

    if not slug:
        raise ErroScraper("O link tem um access_token que não pertence a nenhuma loja.")
    return slug


def achar_loja(entrada, saltos=0):
    entrada = entrada.strip()
    if not entrada:
        raise ErroScraper("Informe o link ou o nome da loja.")

    tem_http = re.match(r"^\w+://", entrada)
    if not tem_http and "/" not in entrada.rstrip("/"):
        nome = entrada.rstrip("/")
        if "." in nome:
            return [("slug", nome), ("dominio", nome.lower())]
        return [("slug", nome)]
    if not tem_http:
        entrada = "https://" + entrada

    try:
        url = urllib.parse.urlparse(entrada)
        host = (url.hostname or "").lower()
    except ValueError:
        raise ErroScraper(f"Link inválido: {entrada}")

    if not host:
        raise ErroScraper(f"Link inválido: {entrada}")
    if host != "anota.ai" and not host.endswith(".anota.ai"):
        return [("dominio", host)]

    partes = [urllib.parse.unquote(p) for p in url.path.split("/") if p]

    if len(partes) >= 2 and partes[0] == "loja":
        return [("slug", partes[1])]
    if len(partes) >= 3 and partes[0] in ("product", "product-details"):
        return [("slug", partes[-1])]
    if host == "app.anota.ai" and len(partes) == 1:
        return [("slug", partes[0])]
    if host == "app.anota.ai" and len(partes) == 2 and partes[0] == "p":
        return [("slug", partes[1])]
    if host == "api.anota.ai" and "webapp" in partes and "p" in partes[:-1]:
        return [("slug", partes[partes.index("p") + 1])]

    token = urllib.parse.parse_qs(url.query).get("access_token")
    if token:
        return [("slug", slug_do_token(token[0]))]

    if partes and saltos < 5:
        try:
            destino = pegar_redirect(entrada)
        except ErroHTTP as e:
            if e.codigo == 404:
                raise ErroScraper(f"Link não encontrado no Anota AI: {entrada}")
            destino = None
        if destino and destino != entrada:
            return achar_loja(destino, saltos + 1)

    raise ErroScraper(f"Não consegui achar a loja no link {entrada}. "
                      f"Use o link do cardápio, tipo https://pedido.anota.ai/loja/nome-da-loja")


def pegar_token(tipo, valor):
    valor_url = urllib.parse.quote(valor, safe="")
    if tipo == "slug":
        url = f"{API}/noauth/access//get-token/{valor_url}"
    else:
        url = f"{API}/noauth/access//get-token-by-url?url={valor_url}"

    try:
        resp = get_json(url)
    except ErroHTTP as e:
        if e.codigo in (400, 404):
            raise LojaNaoEncontrada(f"Loja não encontrada no Anota AI: {valor}")
        raise

    if not resp.get("success") or not resp.get("token"):
        raise LojaNaoEncontrada(f"Loja não encontrada no Anota AI: {valor}")
    return resp["token"]


def baixar_cardapio(token):
    url = f"{API}/clientauth/nm-category/menu-merchant?displaySources=DIGITAL_MENU"
    resp = get_json(url, headers={"Authorization": token})
    if not resp.get("success") or not resp.get("data"):
        raise ErroScraper("A API não retornou o cardápio da loja.")
    return resp["data"]


def to_float(valor):
    if valor is None or valor == "" or isinstance(valor, bool):
        return None
    try:
        return round(float(str(valor).replace(",", ".")), 2)
    except ValueError:
        return None


def to_int(valor, padrao=0):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def limpar(texto):
    if texto is None:
        return ""
    return unicodedata.normalize("NFC", str(texto)).strip()


def link_imagem(url):
    url = limpar(url)
    if not url.startswith("http"):
        return ""
    return urllib.parse.quote(url, safe=":/?&=%")


def preco_riscado(preco, preco_base):
    base = to_float(preco_base)
    if base is not None and preco is not None and base > preco:
        return base
    return None


def hora(segundos):
    segundos = to_int(segundos)
    return f"{segundos // 3600:02d}:{segundos % 3600 // 60:02d}"


def dias_indisponiveis(lista):
    dias = []
    for d in lista or []:
        if isinstance(d, str):
            dias.append(DIAS_PT.get(d, d))
            continue

        nome = DIAS_PT.get(d.get("short_name"), limpar(d.get("short_name")))
        inicio = to_int(d.get("start"))
        fim = to_int(d.get("end"), 86340)
        if inicio <= 0 and fim >= 86340:
            dias.append(nome)
        else:
            dias.append(f"{nome} {hora(inicio)}-{hora(fim)}")
    return dias


def minimo_grupo(opcoes, minimo, modelo):
    escolhidas = []
    for preco, maximo, extra in sorted(opcoes, key=lambda o: o[0] + o[2]):
        falta = minimo - len(escolhidas)
        if falta <= 0:
            break
        qtd = min(falta, maximo) if maximo > 0 else falta
        escolhidas += [(preco, extra)] * qtd

    if len(escolhidas) < minimo:
        return None

    precos = [p for p, _ in escolhidas]
    if modelo == "media":
        valor = sum(precos) / len(precos)
    elif modelo == "maior_valor":
        valor = max(precos)
    else:
        valor = sum(precos)
    return valor + sum(e for _, e in escolhidas)


def custo_minimo(grupos):
    total = 0.0
    for grupo in grupos:
        if grupo["minimo"] <= 0:
            continue

        opcoes = []
        for op in grupo["opcoes"]:
            extra = custo_minimo(op.get("complementos", []))
            if not op["esgotado"] and extra is not None:
                opcoes.append((op["preco"] or 0.0, op["maximo"], extra))

        valor = minimo_grupo(opcoes, grupo["minimo"], grupo["modelo_preco"])
        if valor is None:
            return None
        total += valor
    return total


class Cardapio:
    def __init__(self, dados, incluir_esgotados=True):
        self.dados = dados
        self.incluir_esgotados = incluir_esgotados

        menu = dados.get("menu") or {}
        self.categorias = menu.get("menu") or []
        self.aux = {}
        for cat in menu.get("menu_aux") or []:
            if cat.get("category_id") and cat["category_id"] not in self.aux:
                self.aux[cat["category_id"]] = cat

        self.dia = DIAS[(date.today().weekday() + 1) % 7]

    def itens(self, categoria):
        itens = categoria.get("itens") or []
        if not self.incluir_esgotados:
            itens = [i for i in itens if not i.get("out")]
        return itens

    def preco_pizza(self, item):
        for d in item.get("minimal_price_per_day") or []:
            if d.get("short_name") == self.dia and to_float(d.get("minimal_price")) is not None:
                return to_float(d.get("minimal_price"))
        return to_float(item.get("minimal_price"))

    def loja(self):
        est = self.dados.get("establishment") or {}
        pagina = est.get("page") or {}
        unidade = (est.get("units") or [{}])[0]
        end = unidade.get("address") or {}

        endereco = limpar(end.get("addressFormated"))
        if not endereco:
            rua = ", ".join(limpar(end.get(k)) for k in ("name", "num", "complement") if limpar(end.get(k)))
            cidade = "/".join(limpar(end.get(k)) for k in ("city", "state") if limpar(end.get(k)))
            partes = [rua, limpar(end.get("neighborhood")), cidade, limpar(end.get("postal_code"))]
            endereco = " - ".join(p for p in partes if p)

        horarios = {}
        for dia in unidade.get("week") or []:
            if dia.get("short_name") in DIAS_PT:
                horarios[DIAS_PT[dia["short_name"]]] = [
                    f"{limpar(h.get('start'))[:5]}-{limpar(h.get('end'))[:5]}" for h in dia.get("schedules") or []
                ]

        return {
            "nome": limpar(pagina.get("name")),
            "slug": limpar(est.get("page_name_id")),
            "imagem": link_imagem(pagina.get("image")),
            "whatsapp": limpar(est.get("whatsapp")),
            "pedido_minimo": to_float(est.get("minimum_order_amount")),
            "endereco": endereco,
            "horarios": horarios,
        }

    def grupos(self, next_steps, nivel=0, visitados=()):
        grupos = []
        for passo in next_steps or []:
            cid = passo.get("category")
            cat = self.aux.get(cid)
            if not cat:
                continue

            tipo = None
            if cat.get("category_type") == "pizza":
                tipo = TIPOS_PIZZA.get(limpar(cat.get("internal_title")).split(" ")[0].lower())

            modelo = to_int(passo.get("price_model"), None)
            grupo = {
                "id": cid,
                "nome": limpar(cat.get("title")),
                "tipo": tipo,
                "minimo": to_int(passo.get("min")),
                "maximo": to_int(passo.get("max")),
                "modelo_preco": MODELOS_PRECO.get(modelo, modelo),
                "opcoes": [],
            }

            if cid in visitados or nivel >= PROFUNDIDADE_MAX:
                grupo["aviso"] = "referência circular ou muito profunda; não expandido"
                grupos.append(grupo)
                continue

            for item in self.itens(cat):
                preco = to_float(item.get("price"))
                opcao = {
                    "id": limpar(item.get("category_item_id") or item.get("_id")),
                    "nome": limpar(item.get("title")),
                    "descricao": limpar(item.get("description")),
                    "preco": preco,
                    "preco_original": preco_riscado(preco, item.get("price_base")),
                    "maximo": 1 if tipo == "sabor" else to_int(item.get("max"), -1),
                    "esgotado": bool(item.get("out")),
                    "imagem": link_imagem(item.get("image")),
                }
                if item.get("next_steps"):
                    opcao["complementos"] = self.grupos(item["next_steps"], nivel + 1, visitados + (cid,))
                grupo["opcoes"].append(opcao)

            if grupo["opcoes"] or self.incluir_esgotados:
                grupos.append(grupo)
        return grupos

    def produto(self, item, pizza):
        preco = to_float(item.get("price"))
        grupos = self.grupos(item.get("next_steps"))

        a_partir_de = None
        extra = custo_minimo(grupos)
        if extra is not None:
            a_partir_de = round((preco or 0.0) + extra, 2)
        if pizza and a_partir_de == 0:
            a_partir_de = self.preco_pizza(item) or 0.0

        return {
            "id": limpar(item.get("category_item_id") or item.get("_id")),
            "nome": limpar(item.get("title")),
            "descricao": limpar(item.get("description")),
            "preco": preco,
            "preco_original": preco_riscado(preco, item.get("price_base")),
            "a_partir_de": a_partir_de,
            "esgotado": bool(item.get("out")),
            "indisponivel_em": dias_indisponiveis(item.get("non_active_weekdays")),
            "imagem": link_imagem(item.get("image")),
            "complementos": grupos,
        }

    def montar(self):
        categorias = []
        for cat in self.categorias:
            itens = self.itens(cat)
            if not itens:
                continue

            pizza = cat.get("category_type") == "pizza"
            categorias.append({
                "id": limpar(cat.get("category_id") or cat.get("_id")),
                "nome": limpar(cat.get("title")),
                "tipo": limpar(cat.get("category_type")),
                "imagem": link_imagem(cat.get("image")),
                "somente_agendamento": bool(cat.get("only_scheduled_orders")),
                "indisponivel_em": dias_indisponiveis(cat.get("non_active_weekdays")),
                "produtos": [self.produto(item, pizza) for item in itens],
            })
        return categorias


def nome_arquivo(nome):
    nome = re.sub(r"[^\w.-]+", "_", nome).strip("._")
    return nome or "loja"


def formatar_preco(valor):
    if valor is None:
        return ""
    return f"{valor:.2f}".replace(".", ",")


def salvar_json(dados, caminho):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def salvar_csv_produtos(cardapio, caminho):
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["categoria", "produto", "descricao", "preco", "preco_original", "a_partir_de",
                         "esgotado", "indisponivel_em", "grupos_complemento", "imagem", "id"])
        for cat in cardapio["categorias"]:
            for prod in cat["produtos"]:
                writer.writerow([
                    cat["nome"],
                    prod["nome"],
                    prod["descricao"],
                    formatar_preco(prod["preco"]),
                    formatar_preco(prod["preco_original"]),
                    formatar_preco(prod["a_partir_de"]),
                    "sim" if prod["esgotado"] else "",
                    ", ".join(cat["indisponivel_em"] + prod["indisponivel_em"]),
                    " | ".join(g["nome"] for g in prod["complementos"]),
                    prod["imagem"],
                    prod["id"],
                ])


def escrever_grupos(writer, categoria, produto, grupos, caminho):
    for g in grupos:
        for op in g["opcoes"]:
            writer.writerow([
                categoria,
                produto,
                " > ".join(caminho),
                g["nome"],
                g["tipo"] or "",
                g["minimo"],
                g["maximo"],
                g["modelo_preco"],
                op["nome"],
                op["descricao"],
                formatar_preco(op["preco"]),
                op["maximo"] if op["maximo"] > 0 else "",
                "sim" if op["esgotado"] else "",
                op["imagem"],
            ])
            if op.get("complementos"):
                escrever_grupos(writer, categoria, produto, op["complementos"], caminho + [op["nome"]])


def salvar_csv_complementos(cardapio, caminho):
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["categoria", "produto", "caminho", "grupo", "tipo_grupo", "minimo", "maximo",
                         "modelo_preco", "opcao", "descricao_opcao", "preco_opcao", "maximo_opcao",
                         "esgotado", "imagem_opcao"])
        for cat in cardapio["categorias"]:
            for prod in cat["produtos"]:
                escrever_grupos(writer, cat["nome"], prod["nome"], prod["complementos"], [])


def extensao(conteudo):
    if conteudo[:4] == b"\x89PNG":
        return ".png"
    if conteudo[:4] == b"GIF8":
        return ".gif"
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def baixar_fotos(cardapio, pasta):
    pasta_fotos = os.path.join(pasta, "imagens")
    os.makedirs(pasta_fotos, exist_ok=True)
    ja_baixadas = {os.path.splitext(f)[0]: f for f in os.listdir(pasta_fotos)}

    total = 0
    for cat in cardapio["categorias"]:
        for prod in cat["produtos"]:
            if not prod["imagem"]:
                continue

            nome = nome_arquivo(prod["nome"][:80] + "_" + prod["id"])
            if nome not in ja_baixadas:
                try:
                    conteudo = requisicao(prod["imagem"], binario=True)
                except ErroScraper as e:
                    print(f"Erro ao baixar a foto de {prod['nome']}: {e}", file=sys.stderr)
                    continue

                ja_baixadas[nome] = nome + extensao(conteudo)
                with open(os.path.join(pasta_fotos, ja_baixadas[nome]), "wb") as f:
                    f.write(conteudo)
                total += 1
                time.sleep(0.2)

            prod["imagem_local"] = "imagens/" + ja_baixadas[nome]
    return total


def raspar(link, incluir_esgotados=True):
    opcoes = achar_loja(link)
    for i, (tipo, valor) in enumerate(opcoes):
        try:
            token = pegar_token(tipo, valor)
            break
        except LojaNaoEncontrada:
            if i == len(opcoes) - 1:
                raise

    dados = baixar_cardapio(token)
    cardapio = Cardapio(dados, incluir_esgotados)

    loja = cardapio.loja()
    if not loja["slug"] and tipo == "slug":
        loja["slug"] = valor
    if loja["slug"]:
        loja["url"] = f"{SITE}/loja/{loja['slug']}"
    else:
        loja["url"] = "https://" + valor

    resultado = {
        "loja": loja,
        "extraido_em": datetime.now().isoformat(timespec="seconds"),
        "categorias": cardapio.montar(),
    }
    return resultado, dados


def main():
    parser = argparse.ArgumentParser(description="Copia o cardápio de uma loja do Anota AI para JSON e CSV.")
    parser.add_argument("loja", help="link da loja ou só o nome dela (ex.: personal)")
    parser.add_argument("-o", "--saida", default=".", help="pasta onde salvar os arquivos")
    parser.add_argument("--imagens", "--imagem", action="store_true", help="baixa também as fotos dos produtos")
    parser.add_argument("--sem-esgotados", action="store_true", help="não inclui produtos e opções esgotados")
    parser.add_argument("--bruto", action="store_true", help="salva também o JSON original da API")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    try:
        print(f"Buscando cardápio de {args.loja}...", flush=True)
        cardapio, dados = raspar(args.loja, not args.sem_esgotados)

        slug = nome_arquivo(cardapio["loja"]["slug"] or cardapio["loja"]["nome"])
        pasta = os.path.join(args.saida, slug)
        os.makedirs(pasta, exist_ok=True)

        arquivos = [
            os.path.join(pasta, f"{slug}_cardapio.json"),
            os.path.join(pasta, f"{slug}_produtos.csv"),
            os.path.join(pasta, f"{slug}_complementos.csv"),
        ]
        salvar_json(cardapio, arquivos[0])
        salvar_csv_produtos(cardapio, arquivos[1])
        salvar_csv_complementos(cardapio, arquivos[2])
        if args.bruto:
            arquivos.append(os.path.join(pasta, f"{slug}_bruto.json"))
            salvar_json(dados, arquivos[3])

        total_produtos = sum(len(c["produtos"]) for c in cardapio["categorias"])
        print(f"Loja: {cardapio['loja']['nome']}")
        print(f"Categorias: {len(cardapio['categorias'])} | Produtos: {total_produtos}")

        if args.imagens:
            print("Baixando fotos (pode demorar um pouco)...", flush=True)
            print(f"Fotos baixadas: {baixar_fotos(cardapio, pasta)}")
            salvar_json(cardapio, arquivos[0])

        print(f"Arquivos salvos em {os.path.abspath(pasta)}:")
        for arquivo in arquivos:
            print("  - " + os.path.basename(arquivo))
        print("Pronto!")

    except ErroScraper as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Erro ao salvar os arquivos: {e}", file=sys.stderr)
        print("Se algum CSV estiver aberto no Excel, feche e tente de novo.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
