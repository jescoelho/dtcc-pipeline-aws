# Guia de docstrings (Python)

Objetivo: quem abre o repo entende o fluxo lendo só as docstrings (módulo → classe → função), e entra no corpo do código apenas onde importa.

## Regras gerais

- Comentário explica o **porquê** (decisão, armadilha), não o **o quê**.
- Nomes claros valem mais que comentário.
- Docstring desatualizada é pior que nenhuma: mudou o código, atualize a docstring.
- Type hints documentam entrada e saída; não repita em prosa o que eles já dizem.
- Formato adotado: Google style (`Args`, `Returns`, `Raises`).

## O que documentar e o que deve conter

| # | Onde | O que deve conter |
|---|------|-------------------|
| 1 | **Módulo** (topo do arquivo) | O que o arquivo contém, quem deveria usá-lo, dependências importantes. Em scripts: como rodar e quais argumentos recebe. |
| 2 | **Pacote** (`__init__.py`) | O que o pacote oferece e o que é exportado. |
| 3 | **Classe** | Responsabilidade em 1 linha, atributos não óbvios, exemplo curto de uso. Se o `__init__` for simples, os parâmetros entram aqui. |
| 4 | **Função / método público** | O que faz, `Args`, `Returns`, `Raises`. Se houver regra de negócio, efeito colateral ou premissa relevante, registre em `Note`. |
| 5 | **Método privado** (`_nome`) | Só se a lógica não for óbvia: 1 linha do que faz e por quê. |
| 6 | **Método especial não trivial** (`__call__`, `__enter__`/`__exit__`, `__getitem__`, `__eq__`) | Qual comportamento customizado e o que retorna. |
| 7 | **Property** | 1 linha do que o valor representa (no getter). |
| 8 | **Constante / variável de módulo** | Comentário com significado, unidade e fonte, quando houver. |
| 9 | **Decorator / função que retorna função** | O que adiciona ao comportamento e se altera a assinatura. |
| 10 | **Exceção customizada** | Quando é levantada. |
| 11 | **Teste** | 1 linha do comportamento verificado. |

## Não precisam de docstring

- Funções internas triviais.
- Getters/setters óbvios.
- `__init__` simples (documente os parâmetros na classe).
- `__repr__` e similares triviais.

## Onde dar mais atenção

Erros silenciosos moram onde o código decide ou transforma algo. Priorize a docstring de:

- **Módulos**, porque são o mapa do projeto.
- **Funções com regra de negócio ou cálculo**: diga o que entra, o que sai e qual premissa assume.
- **Funções com efeito colateral** (escrevem em disco, banco, API, alteram estado global ou o argumento recebido): diga o que é alterado.
- **Fronteiras do sistema** (I/O, chamadas externas, parsing de entrada): diga o formato esperado e o que acontece quando falha.

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

## Para colar no prompt padrão

> Toda função, classe e módulo com docstring no formato Google style. Funções com regra de negócio, cálculo ou efeito colateral devem registrar a premissa ou o efeito em `Note`. Não adicione comentários que repetem o código.
