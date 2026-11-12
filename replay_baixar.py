# replay_baixar.py: baixa a chuva real de um temporal histórico para testar o sistema
import sqlite3
import pandas as pd
from google.cloud import bigquery

PROJETO_GCP = "datas-rio"
TAB_CHUVA = "datario.clima_pluviometro.taxa_precipitacao_alertario"
INICIO = "2024-01-13 12:00:00"
FIM = "2024-01-14 12:00:00"

sql = f"""
SELECT CAST(id_estacao AS STRING) AS id_estacao,
       CAST(DATETIME(data_particao, CAST(horario AS TIME)) AS STRING) AS horario,
       SAFE_CAST(acumulado_chuva_15_min AS FLOAT64) AS mm
FROM `{TAB_CHUVA}`
WHERE data_particao BETWEEN DATE('{INICIO[:10]}') AND DATE('{FIM[:10]}')
  AND DATETIME(data_particao, CAST(horario AS TIME))
      BETWEEN DATETIME('{INICIO}') AND DATETIME('{FIM}')
"""

print("Baixando o temporal de 13-14/jan/2024 do BigQuery...")
df = bigquery.Client(project=PROJETO_GCP).query(sql).to_dataframe()
linhas = [(int(r.id_estacao), str(r.horario).replace(" ", "T"), float(r.mm))
          for r in df.itertuples() if pd.notna(r.mm)]

con = sqlite3.connect("alagamento.db")
con.execute("DROP TABLE IF EXISTS leituras_replay")
con.execute("""CREATE TABLE leituras_replay(
    estacao_id INTEGER, horario TEXT, mm REAL, PRIMARY KEY (estacao_id, horario))""")
con.executemany("INSERT OR REPLACE INTO leituras_replay VALUES (?,?,?)", linhas)
con.commit()

print(f"{len(linhas)} leituras salvas.\n")
print("Estações com mais chuva no período:")
for nome, total in con.execute("""
    SELECT COALESCE(e.nome, 'Estação ' || l.estacao_id), ROUND(SUM(l.mm), 1)
    FROM leituras_replay l LEFT JOIN estacoes e ON e.id = l.estacao_id
    GROUP BY l.estacao_id ORDER BY 2 DESC LIMIT 8"""):
    print(f"  {nome:<28} {total:>7} mm")
con.close()
