# Monitor de Alagamentos Rio

Sistema que monitora o risco de alagamento na cidade do Rio de Janeiro em tempo quase real.

## Como funciona

1. **Estações:** usa as estações pluviométricas do Sistema Alerta Rio, a partir do datalake da Prefeitura (DATA.RIO / BigQuery).
2. **Calibração:** `calibrar.py` analisa o histórico de chuva de cada estação e calcula um limiar de risco próprio (mm em 1 hora).
3. **Chuva ao vivo:** a cada 15 minutos, coleta a chuva estimada pelo Open-Meteo para cada estação.
4. **Risco:** compara o acumulado da última hora com o limiar e classifica em baixo, moderado ou alto.
5. **Mapa:** uma API em FastAPI serve um mapa interativo (Leaflet) com as estações coloridas por risco.

## Como rodar

    pip install -r requirements.txt
    python calibrar.py        # gera limiares.json (precisa de um projeto no Google Cloud)
    python alagamento_rio.py  # abre o mapa em http://localhost:8080

## Próximos passos

- Pluviômetros reais do CEMADEN para a chuva ao vivo
- Previsão de risco para as próximas horas
- Aplicativo (PWA) com alertas e relatos de alagamento

> Projeto independente, sem vínculo com órgãos oficiais. Em emergências, siga as orientações da Defesa Civil.
