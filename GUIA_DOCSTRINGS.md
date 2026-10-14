# Guia de docstrings e comentários (Python)

Objetivo: quem abre o repo entende o fluxo lendo só as docstrings (módulo → classe → função), e entra no corpo do código apenas onde importa.

## Regras gerais

- **Docstring descreve o contrato** (o que faz, o que recebe, o que devolve). **Comentário inline explica o porquê** (decisão, armadilha), nunca o que o código já diz.
- Nomes claros valem mais que comentário.
- Docstring desatualizada é pior que nenhuma: mudou o código, atualize a docstring.
- Type hints documentam tipos; não repita em prosa o que eles já dizem. Na docstring, descreva restrições e significado (ex: "maior ou igual a zero").
- **Formato:** Google style. Os nomes das seções ficam em inglês (`Args`, `Returns`, `Raises`, `Yields`, `Attributes`, `Example`, `Note`) e o texto em português.
- **Primeira linha:** resumo em 1 linha, na 3ª pessoa do presente ("Calcula...", "Remove..."), terminando com ponto. Se houver mais conteúdo, deixe uma linha em branco depois dela.

## Quando dispensar a docstring

Dispense quando o nome e os type hints já respondem **o que faz, o que recebe e o que devolve**. Esse é o único critério; vale onde esta tabela diz "dispensável".

- **Sempre documente:** módulo e classe/função/método **público**, com pelo menos o resumo de 1 linha. `Args`, `Returns` e `Raises` entram só quando acrescentam algo além do nome e dos type hints.
- **Dispensável:** método ou função privada curta (`_nome`), property/getter/setter óbvio, `__init__` simples, `__repr__` trivial e teste cujo nome já descreve o comportamento.
- **`dataclass` ou `Enum` com campos autoexplicativos:** 1 linha na classe basta.

## O que documentar e o que deve conter

| # | Onde | O que deve conter |
|---|------|-------------------|
| 1 | **Módulo** (topo do arquivo) | O que o arquivo contém e quando usá-lo; dependências importantes. Em scripts: como rodar e quais argumentos recebe. |
| 2 | **Pacote** (`__init__.py`) | O que o pacote oferece e o que é exportado. |
| 3 | **Classe** | Responsabilidade em 1 linha; `Attributes` quando não forem autoexplicativos; parâmetros do `__init__` (que não têm docstring própria); `Example` curto, opcional. |
| 4 | **Função / método público** | O que faz; `Args`; `Returns` (omita quando devolve `None`); `Raises` (só exceções que a função levanta ou deixa propagar de propósito); `Yields` em geradores; `Note` conforme a seção abaixo. |
| 5 | **Função / método privado** (`_nome`) e **função aninhada** | Dispensável (ver critério). Se a lógica não for óbvia: 1 linha do que faz e por quê. |
| 6 | **Método especial** (`__call__`, `__enter__`/`__exit__`, `__getitem__`, `__eq__`) | Dispensável nos triviais. Nos customizados: qual comportamento e o que retorna. |
| 7 | **Property** | Dispensável se óbvia. Caso contrário, 1 linha do que o valor representa (no getter). |
| 8 | **Constante / variável de módulo** | Comentário `#` com significado, unidade e fonte, quando houver. É comentário, não docstring. |
| 9 | **Decorator / função que retorna função** | O que adiciona ao comportamento e se altera a assinatura. |
| 10 | **Exceção customizada** | Quando é levantada. Esta linha prevalece sobre a da classe (3). |
| 11 | **Teste** | Dispensável se o nome descreve o comportamento. Caso contrário, 1 linha do comportamento verificado. |

Para funções assíncronas, use as mesmas regras das síncronas.

## Quando usar `Note`

Use `Note` para o que quem chama precisa saber e o código não deixa óbvio:

- **Regra de negócio ou premissa:** ex: "Descontos não são cumulativos com cupom; vale o maior."
- **Efeito colateral:** escreve em disco, banco ou API, altera estado global ou o argumento recebido.
- **Fronteira do sistema** (I/O, chamada externa, parsing): formato esperado e o que acontece quando falha, se não estiver em `Raises`.

## Templates

### Módulo

```python
"""Cálculo de preços e descontos de pedidos.

Contém as regras de desconto por categoria de cliente e o cálculo do
valor final. Depende apenas da biblioteca padrão.
"""
```

### Classe

```python
class CarrinhoDeCompras:
    """Agrupa itens de um pedido e calcula o total.

    Attributes:
        itens: lista de itens adicionados, na ordem de inserção.
        cupom: código de desconto aplicado, ou None.

    Example:
        >>> carrinho = CarrinhoDeCompras()
        >>> carrinho.adicionar(Item("caneta", 2.50), quantidade=3)
        >>> carrinho.total()
        7.5
    """
```

O `Example` é ilustrativo. Só vira teste se o projeto executar doctests.

### Função com regra de negócio

```python
def calcular_desconto(valor: float, categoria: str) -> float:
    """Calcula o valor do desconto para a categoria do cliente.

    Args:
        valor: valor bruto do pedido, maior ou igual a zero.
        categoria: "padrao", "premium" ou "atacado".

    Returns:
        Valor do desconto em reais (não o valor final).

    Raises:
        ValueError: se a categoria não for reconhecida.

    Note:
        Descontos não são cumulativos com cupom; vale o maior.
    """
```

### Função com efeito colateral

```python
def salvar_relatorio(dados: dict, destino: Path) -> None:
    """Grava o relatório em JSON no caminho informado.

    Args:
        dados: conteúdo serializável em JSON.
        destino: caminho do arquivo de saída.

    Raises:
        OSError: se não houver permissão de escrita.

    Note:
        Sobrescreve o arquivo se ele já existir.
    """
```

### Exceção customizada

```python
class ConfiguracaoInvalidaError(Exception):
    """Levantada quando o arquivo de configuração tem campos obrigatórios ausentes."""
```

### Constante

```python
TIMEOUT_REQUISICAO = 30  # segundos; limite acordado com o time de infraestrutura
```

## Trecho para colar no prompt ou no `AGENTS.md` / `CLAUDE.md`

> Documente com docstring Google style (seções em inglês, texto em português) todo módulo e toda classe, função e método público, com pelo menos um resumo de 1 linha na 3ª pessoa. Use `Args`/`Returns`/`Raises` só quando acrescentarem algo além do nome e dos type hints. Registre em `Note` regra de negócio, premissa ou efeito colateral. Dispense docstring em privados curtos, getters óbvios, `__init__` simples e testes com nome descritivo. Não escreva comentários que repetem o código.
