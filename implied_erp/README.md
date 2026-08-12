# Extrator de ERP por País — Damodaran

## Descrição

Este programa extrai dados de **Risco de Mercado Implícito por País (ERP)** da planilha **ctryprem\*.xlsx** do Damodaran. Ele converte a planilha da NYU Stern School of Business com todos os dados de ERP por país para um formato estruturado em JSON, pronto para análise quantitativa.

## Como funciona

O script lê a planilha `ctryprem*.xlsx` e processa a aba **"ERPs by country"**, extraindo três tipos de informações:

### 1. Metadados (linhas 1-7)
- **Data de atualização**: Quando os dados foram compilados
- **ERP de mercados maduros**: Risco de mercado para economias desenvolvidas
- **ERP dos EUA**: Risco de mercado específico para os Estados Unidos

### 2. Países regulares (com classificação de risco)
- Mais de 100 países com classificação de risco soberano Moody's
- Inclui colunas como: Moody's Rating, Rating Default Spread, Total Equity Risk Premium, Country Risk Premium, CDS soberanos, entre outras
- Estruturado como um dicionário com tipos de dados apropriados (strings para ratings, floats para valores numéricos)

### 3. Mercados fronteiriços (sem classificação de risco)
- Países sem classificação de risco soberano padrão
- Contém dados mais básicos: PRS Score, ERP, CRP, Default Spread
- Marcados com flag `is_frontier: true`

## Uso

```bash
# Salvar em arquivo JSON
python extract_damodaran_erp.py --xlsx "caminho/ctrypremJuly26.xlsx" --out "dados.json"

# Exibir no terminal (JSON formatado)
python extract_damodaran_erp.py --xlsx "caminho/ctrypremJuly26.xlsx"
```

## Formato de saída

O JSON contém um dicionário com as seguintes chaves de alto nível:

- **`source`**: Nome do arquivo de origem processado
- **`updated`**: String com a data de atualização
- **`mature_market_erp`**: Float com o ERP de mercados maduros (ou `null`)
- **`us_erp`**: Float com o ERP dos EUA (ou `null`)
- **`countries`**: Dicionário onde cada chave é o nome do país e o valor é um dicionário com os campos abaixo

### Países Regulares
| Campo | Tipo | Descrição |
|-------|------|-----------|
| `is_frontier` | bool | `false` |
| `region` | string | Região geográfica (ex: "Africa") |
| `moody_rating` | string | Classificação Moody's (ex: "B3") |
| `rating_default_spread` | float | Default spread baseado no rating |
| `total_equity_risk_premium` | float | ERP total |
| `country_risk_premium` | float | CRP (Country Risk Premium) |
| `sovereign_cds_net` | float | CDS soberano líquido (ou null) |
| `total_equity_risk_premium2` | float | ERP alternativo (calculado via CDS) |
| `country_risk_premium3` | float | CRP alternativo (calculado via CDS) |

### Mercados Fronteiriços
| Campo | Tipo | Descrição |
|-------|------|-----------|
| `is_frontier` | bool | `true` |
| `prs_score` | float | Pontuação PRS (Political Risk Score) |
| `erp` | float | ERP |
| `crp` | float | CRP |
| `default_spread` | float | Default spread |

### Exemplo: Angola (Regular)

```json
{
  "Angola": {
    "is_frontier": false,
    "region": "Africa",
    "moody_rating": "B3",
    "rating_default_spread": 0.0596,
    "total_equity_risk_premium": 0.1344,
    "country_risk_premium": 0.0927,
    "sovereign_cds_net": 0.049,
    "total_equity_risk_premium2": 0.1179,
    "country_risk_premium3": 0.0762
  }
}
```

### Exemplo: Argélia (Fronteiriço)

```json
{
  "Algeria": {
    "is_frontier": true,
    "prs_score": 66.25,
    "erp": 0.1059,
    "crp": 0.0642,
    "default_spread": 0.0413
  }
}
```

## Tratamento de erros

- **Arquivo não encontrado**: Se o caminho para `--xlsx` não existir, gera `FileNotFoundError` com a mensagem "Arquivo não encontrado: [caminho]"
- **Aba não encontrada**: Se a aba "ERPs by country" não estiver presente, informa os nomes das abas disponíveis para facilitar a identificação
- **Dados ausentes**: Se nenhum país é encontrado após o parsing, gera `ValueError` indicando que nenhum país foi encontrado
- **Valores inválidos**: "NA", "N/A", "#N/A" ou células vazias são convertidos para `null` no JSON

## Constantes editáveis

Se a estrutura da planilha mudar no futuro, as constantes no topo do script podem ser ajustadas:

| Constante | Valor default | Descrição |
|-----------|--------------|-----------|
| `SHEET_NAME` | `"ERPs by country"` | Nome da aba a ser processada |
| `COUNTRY_COL` | `0` | Índice da coluna com nomes dos países |
| `DATA_START_ROW` | `9` | Primeira linha de dados (Excel row) |
| `METADATA_MAX_ROW` | `7` | Última linha de metadados (Excel row) |
| `REGULAR_FIELDS` | `(8 campos)` | Mapeamento coluna → field name para países regulares |
| `FRONTIER_FIELDS` | `(4 campos)` | Mapeamento coluna → field name para fronteiriços |

## Dependências

- **openpyxl**: Leitura de arquivos Excel .xlsx

Instalado no ambiente virtual do projeto.

## Fonte de dados

Os dados são extraídos da planilha do **Damodaran** (NYU Stern):

https://pages.stern.nyu.edu/~adamodar/
