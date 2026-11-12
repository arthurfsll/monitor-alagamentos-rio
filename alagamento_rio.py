import random, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ---------- Configuração ----------
PROJETO_GCP = "datas-rio"
USAR_DADOS_REAIS = False   # mude para True depois de conferir as tabelas
USAR_OPENMETEO = True   # chuva ao vivo estimada, sem chave
DATASET = "datario.clima_pluviometro"
TAB_CHUVA = f"{DATASET}.taxa_precipitacao_alertario"
TAB_ESTACOES = f"{DATASET}.estacoes_alertario"
DB = "alagamento.db"
INTERVALO_COLETA = 15 * 60
FUSO = ZoneInfo("America/Sao_Paulo")
LIMIAR_PADRAO = 30.0   # mm em 1h; calibrar depois com o histórico

ESTACOES_SIMULADAS = [
    (1, "Praça da Bandeira", -22.9128, -43.2130, 25),
    (2, "Tijuca", -22.9250, -43.2330, 30),
    (3, "Jardim Botânico", -22.9670, -43.2240, 35),
    (4, "Madureira", -22.8730, -43.3390, 30),
    (5, "Campo Grande", -22.9030, -43.5610, 40),
    (6, "Barra da Tijuca", -23.0000, -43.3650, 35),
]

def agora():
    return datetime.now(FUSO).replace(tzinfo=None, microsecond=0)

# ---------- Banco ----------
@contextmanager
def conexao():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def criar_banco():
    with conexao() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS estacoes(
            id INTEGER PRIMARY KEY, nome TEXT, lat REAL, lon REAL, limiar REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS leituras(
            estacao_id INTEGER, horario TEXT, mm REAL,
            PRIMARY KEY (estacao_id, horario))""")

def carregar_estacoes():
    import json, os
    limiares = {}
    if os.path.exists("limiares.json"):
        limiares = {int(k): v for k, v in json.load(open("limiares.json")).items()}
    try:
        df = consultar(f"SELECT id_estacao, estacao, latitude, longitude FROM `{TAB_ESTACOES}` WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
        estacoes = [(int(r.id_estacao), r.estacao, float(r.latitude), float(r.longitude),
                     limiares.get(int(r.id_estacao), LIMIAR_PADRAO)) for r in df.itertuples()]
    except Exception as erro:
        print("Não consegui carregar as estações reais, usando simuladas:", erro)
        estacoes = ESTACOES_SIMULADAS
    with conexao() as c:
        c.execute("DELETE FROM estacoes")
        c.execute("DELETE FROM leituras")  # limpa leituras antigas da simulação
        c.executemany("INSERT INTO estacoes VALUES (?,?,?,?,?)", estacoes)
    print(f"{len(estacoes)} estações carregadas, {len(limiares)} com limiar calibrado")

# ---------- Coleta ----------
def consultar(sql):
    from google.cloud import bigquery
    return bigquery.Client(project=PROJETO_GCP).query(sql).to_dataframe()

def coletar_simulado():
    h = agora().isoformat()
    with conexao() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM estacoes")]
    return [(i, h, round(max(0, random.gauss(3, 4)), 1)) for i in ids]

def coletar_real():
    df = consultar(f"""
        SELECT id_estacao,
               CAST(DATETIME(data_particao, CAST(horario AS TIME)) AS STRING) AS horario,
               acumulado_chuva_15_min AS mm
        FROM `{TAB_CHUVA}`
        WHERE data_particao BETWEEN DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 1 DAY) AND CURRENT_DATE('America/Sao_Paulo')
          AND DATETIME(data_particao, CAST(horario AS TIME)) >= DATETIME_SUB(CURRENT_DATETIME('America/Sao_Paulo'), INTERVAL 2 HOUR)""")
    return [(int(r.id_estacao), str(r.horario).replace(" ", "T"), float(r.mm or 0))
            for r in df.itertuples()]

def coletar_openmeteo():
    """Chuva estimada a cada 15 min pelo Open-Meteo (modelo, não pluviômetro)."""
    import requests
    with conexao() as c:
        est = c.execute("SELECT id, lat, lon FROM estacoes WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchall()
    limite = agora().isoformat()
    leituras = []
    for i in range(0, len(est), 50):
        lote = est[i:i + 50]
        resp = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": ",".join(f"{e['lat']:.4f}" for e in lote),
            "longitude": ",".join(f"{e['lon']:.4f}" for e in lote),
            "minutely_15": "precipitation",
            "past_minutely_15": 8,
            "forecast_minutely_15": 1,
            "timezone": "America/Sao_Paulo",
        }, timeout=30)
        resp.raise_for_status()
        dados = resp.json()
        if isinstance(dados, dict):
            dados = [dados]
        for e, d in zip(lote, dados):
            m = d["minutely_15"]
            for h, mm in zip(m["time"], m["precipitation"]):
                h = h if len(h) > 16 else h + ":00"
                if mm is not None and h <= limite:
                    leituras.append((e["id"], h, float(mm)))
    return leituras

def ciclo_coleta():
    while True:
        try:
            dados = coletar_openmeteo() if USAR_OPENMETEO else (coletar_real() if USAR_DADOS_REAIS else coletar_simulado())
            with conexao() as c:
                c.executemany("INSERT OR REPLACE INTO leituras VALUES (?,?,?)", dados)
            print(f"[{agora():%H:%M}] {len(dados)} leituras salvas")
        except Exception as erro:
            print("Falha na coleta:", erro)
        time.sleep(INTERVALO_COLETA)

# ---------- Interpretação ----------
def classificar(acumulado, limiar):
    if acumulado >= limiar:
        return "alto"
    if acumulado >= 0.6 * limiar:
        return "moderado"
    return "baixo"

def risco_atual():
    desde = (agora() - timedelta(hours=1)).isoformat()
    with conexao() as c:
        linhas = c.execute("""
            SELECT e.id, e.nome, e.lat, e.lon, e.limiar,
                   COALESCE(SUM(l.mm), 0) AS acumulado
            FROM estacoes e
            LEFT JOIN leituras l ON l.estacao_id = e.id AND l.horario >= ?
            GROUP BY e.id""", (desde,)).fetchall()
    return [{**dict(r), "acumulado": round(r["acumulado"], 1),
             "risco": classificar(r["acumulado"], r["limiar"])} for r in linhas]

# ---------- API e mapa ----------
app = FastAPI(title="Monitor de Alagamentos Rio")

@app.get("/api/risco")
def api_risco():
    return {"atualizado": f"{agora():%H:%M}",
            "modo": "Open-Meteo (estimativa)" if USAR_OPENMETEO else ("dados reais" if USAR_DADOS_REAIS else "simulação"),
            "estacoes": risco_atual()}

MAPA_HTML = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor de Alagamentos Rio</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body,#mapa{height:100%;margin:0}
#info{position:absolute;top:10px;right:10px;z-index:1000;background:#fff;
padding:8px 12px;border-radius:8px;font:14px sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)}
</style></head><body>
<div id="mapa"></div><div id="info">Carregando…</div>
<script>
const mapa = L.map('mapa').setView([-22.93, -43.40], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {attribution: '© OpenStreetMap'}).addTo(mapa);
const camada = L.layerGroup().addTo(mapa);
const cores = {baixo: '#2e7d32', moderado: '#f9a825', alto: '#c62828'};
async function atualizar() {
  try {
    const d = await (await fetch('api/risco')).json();
    camada.clearLayers();
    d.estacoes.forEach(e => {
      L.circleMarker([e.lat, e.lon], {radius: 10, color: cores[e.risco], fillOpacity: .7})
        .bindPopup(`<b>${e.nome}</b><br>Chuva na última hora: ${e.acumulado} mm` +
                   `<br>Limiar: ${e.limiar} mm<br>Risco: ${e.risco}`)
        .addTo(camada);
    });
    document.getElementById('info').textContent =
      `Atualizado às ${d.atualizado} · ${d.modo}`;
  } catch (err) {
    document.getElementById('info').textContent = 'Erro ao carregar dados';
  }
}
atualizar();
setInterval(atualizar, 60000);
</script></body></html>"""

# ---------- Replay de eventos históricos ----------
def risco_em(momento):
    fim = datetime.fromisoformat(momento)
    desde = (fim - timedelta(hours=1)).isoformat()
    with conexao() as c:
        linhas = c.execute("""
            SELECT e.id, e.nome, e.lat, e.lon, e.limiar,
                   COALESCE(SUM(l.mm), 0) AS acumulado
            FROM estacoes e
            LEFT JOIN leituras_replay l ON l.estacao_id = e.id
                 AND l.horario > ? AND l.horario <= ?
            WHERE e.lat IS NOT NULL AND e.lon IS NOT NULL
            GROUP BY e.id""", (desde, fim.isoformat())).fetchall()
    return [{**dict(r), "acumulado": round(r["acumulado"], 1),
             "risco": classificar(r["acumulado"], r["limiar"])} for r in linhas]

@app.get("/api/replay/momentos")
def api_replay_momentos():
    try:
        with conexao() as c:
            rows = c.execute("SELECT DISTINCT horario FROM leituras_replay ORDER BY horario").fetchall()
        return {"momentos": [r[0] for r in rows]}
    except sqlite3.OperationalError:
        return {"momentos": []}

@app.get("/api/replay")
def api_replay(momento: str):
    return {"atualizado": f"{momento[8:10]}/{momento[5:7]} {momento[11:16]}",
            "modo": "Replay · temporal de 13–14/jan/2024",
            "estacoes": risco_em(momento)}

@app.get("/replay", response_class=HTMLResponse)
def pagina_replay():
    return open("mapa2d.html", encoding="utf-8").read()

@app.get("/", response_class=HTMLResponse)
def mapa():
    return open("mapa2d.html", encoding="utf-8").read()

@app.get("/3d", response_class=HTMLResponse)
def mapa3d():
    return open("mapa3d.html", encoding="utf-8").read()

if __name__ == "__main__":
    criar_banco()
    carregar_estacoes()
    threading.Thread(target=ciclo_coleta, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8080)