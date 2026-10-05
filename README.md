# Scraper Anota AI

Script em Python pra copiar o cardápio de uma loja do Anota AI e salvar em JSON e CSV (dá pra abrir no Excel).

Ele pega as categorias, produtos, preços, fotos e os complementos (adicionais, sabores e bordas de pizza etc).

## Como usar

Só precisa do Python 3.8 ou mais novo, não usa nenhuma biblioteca externa.

```bash
python3 anotaai_scraper.py https://pedido.anota.ai/loja/nome-da-loja
```

Também funciona só com o nome da loja:

```bash
python3 anotaai_scraper.py nome-da-loja
```

Aceita também link de produto e os links curtos (`app.anota.ai/...` e `loja.anota.ai/...`). Se o link tiver `?`, coloca entre aspas. No Windows é `python` em vez de `python3`.

Pra testar dá pra usar essa loja: https://pedido.anota.ai/loja/mais-acai-10

### Opções

- `-o pasta`: escolhe a pasta onde salvar
- `--imagem`: baixa as fotos dos produtos
- `--sem-esgotados`: não salva o que tá esgotado
- `--bruto`: salva também o JSON original que vem da API

Exemplo com tudo:

```bash
python3 anotaai_scraper.py mais-acai-10 -o cardapios --imagem --sem-esgotados --bruto
```

## O que ele gera

Uma pasta com o nome da loja com:

- `nome-da-loja_cardapio.json`: o cardápio completo
- `nome-da-loja_produtos.csv`: um produto por linha
- `nome-da-loja_complementos.csv`: os complementos de cada produto
- `imagens/`: as fotos (se usar `--imagem`)

Os CSVs usam `;` e vírgula nos preços pra abrir certo no Excel.

Alguns produtos vêm com preço 0 porque o valor depende do tamanho ou sabor escolhido (acontece muito com açaí e pizza). Pra esses, a coluna `a_partir_de` mostra o menor valor que dá pra pedir.

## Como funciona

O site do Anota AI bloqueia scraping direto na página, mas o cardápio é carregado por uma API. O script pega um token da loja e depois baixa o cardápio dela:

1. `GET api.anota.ai/noauth/access//get-token/<loja>`
2. `GET api.anota.ai/clientauth/nm-category/menu-merchant` com o token no header `Authorization`

Se o Anota AI mudar essa API o script pode parar de funcionar.
