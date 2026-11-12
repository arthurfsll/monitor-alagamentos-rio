# calibrar.py: calcula o limiar de risco de cada estação a partir do histórico
import json
import math
from google.cloud import bigquery

PROJETO_GCP = "datas-rio"
TAB_CHUVA = "datario.clima_pluviometro.taxa_precipitacao_alertario"
TAB_ESTACOES = "datario.clima_pluviometro.estacoes_alertario"
MIN_AMOSTRAS = 1000          # estações com pouco histórico ficam com o limiar padrão
PISO, TETO = 25.0, 50.0      # faixa de "chuva forte" da escala do Alerta Rio (mm/h)

SQL = f"""
WITH chuva AS (
  SELECT CAST(id_estacao AS STRING) AS id_estacao,
         SAFE_CAST(acumulado_chuva_1_h AS FLOAT64) AS mm_1h
  FROM `{TAB_CHUVA}`
  WHERE data_particao BETWEEN '2000-01-01' AND CURRENT_DATE()
)
SELECT c.id_estacao, ANY_VALUE(e.estacao) AS estacao, COUNT(*) AS amostras,
       APPROX_QUANTILES(c.mm_1h, 1000)[OFFSET(990)] AS p99,
       APPROX_QUANTILES(c.mm_1h, 1000)[OFFSET(999)] AS p999,
       MAX(c.mm_1h) AS maximo
FROM chuva c
LEFT JOIN `{TAB_ESTACOES}` e ON CAST(e.id_estacao AS STRING) = c.id_estacao
WHERE c.mm_1h > 0
GROUP BY c.id_estacao
ORDER BY p999 DESC
"""

def fmt(v):
    return "  -  " if v is None or (isinstance(v, float) and math.isnan(v)) else f"{float(v):.1f}"

print("Consultando o histórico no BigQuery (pode levar alguns segundos)...")
df = bigquery.Client(project=PROJETO_GCP).query(SQL).to_dataframe()

limiares = {}
print(f"\n{'Estação':<28}{'amostras':>10}{'p99':>8}{'p99.9':>8}{'máx':>8}{'limiar':>8}")
print("-" * 70)
for r in df.itertuples():
    limiar = None
    if r.amostras >= MIN_AMOSTRAS and r.p999 is not None and not math.isnan(r.p999):
        limiar = round(min(TETO, max(PISO, float(r.p999))), 1)
        limiares[str(int(r.id_estacao))] = limiar
    nome = str(r.estacao if isinstance(r.estacao, str) else f"Estação {r.id_estacao}")[:27]
    print(f"{nome:<28}{r.amostras:>10}{fmt(r.p99):>8}{fmt(r.p999):>8}{fmt(r.maximo):>8}{fmt(limiar):>8}")

with open("limiares.json", "w", encoding="utf-8") as f:
    json.dump(limiares, f, indent=2, ensure_ascii=False)
print(f"\n{len(limiares)} limiares salvos em limiares.json")
