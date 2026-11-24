# Parleys UCL

Modelo de probabilidades para partidos de la Champions League, con un agente que lee noticias de lesiones y rotaciones, un generador de parleys por nivel de riesgo y una app para el iPhone (PWA).

Todo funciona con servicios gratuitos.

## Cómo funciona

1. **Nivel de cada equipo:** Elo de [ClubElo](http://clubelo.com) más ventaja de local.
2. **Goles esperados:** la diferencia de Elo se convierte en goles con una constante calibrada para que coincida con la expectativa del propio Elo.
3. **Agente de noticias** (para el próximo día con partidos):
   - lesiones y sanciones oficiales de API-Football;
   - quién fue titular, cuántos minutos jugó y con qué nota en los últimos partidos (un mal partido o una salida temprana bajan su probabilidad de ser titular);
   - titulares de prensa de Google News en español e inglés, interpretados por una IA gratuita (Groq; Gemini si Groq se queda sin cuota).
   Con eso arma su propio once probable y ajusta el ataque y la defensa de cada equipo según quién falta y qué tan importante es.
4. **Forma reciente** e **historial directo** con peso pequeño (el Elo ya refleja casi todo).
5. **Matriz de marcadores** (Poisson con corrección Dixon-Coles): de ahí salen 1X2, doble oportunidad, goles, ambos marcan, goles por equipo y combinadas del mismo partido calculadas en conjunto.
6. **Mezcla con el mercado:** cuando hay cuotas reales se quita el margen de la casa y se combina con el modelo. Se marca "valor" cuando la probabilidad final supera lo que paga la cuota.
7. **Parleys:** seguro, equilibrado y soñador, por día y para toda la jornada. Una selección por partido, así que la probabilidad del parley es el producto.
8. **Confiabilidad:** cada predicción se guarda y se califica al terminar el partido (`data/calibration.json`).

## Uso

- Botón **Actualizar** en la app → dispara el workflow `Actualizar jornada` en GitHub Actions → corre el análisis (2 a 4 minutos), guarda `data/` y publica la app.
- Sin claves, o con `--demo`, genera datos de ejemplo ficticios.

```bash
pip install -r requirements.txt
python -m predictor.pipeline --demo   # datos de ejemplo
python -m pytest -q
```

## Secrets del repositorio

| Nombre | Para qué |
|---|---|
| `API_FOOTBALL_KEY` | Partidos, lesiones, alineaciones, cuotas (plan gratis: 100 peticiones al día) |
| `GROQ_API_KEY` | IA principal para leer titulares |
| `GEMINI_API_KEY` | IA de respaldo |

Cada corrida gasta como máximo `API_BUDGET` peticiones (75 por defecto) y guarda caché en `data/cache/`, así que se pueden hacer un par de actualizaciones al día.

## Aviso

Son estimaciones de un modelo, no certezas. Ningún sistema garantiza ganancias.
