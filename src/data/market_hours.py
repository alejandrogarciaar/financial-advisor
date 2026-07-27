"""Chequeo simple de horario regular de mercado (NYSE/NASDAQ, 9:30-16:00 hora de Nueva York,
lunes a viernes). No contempla feriados bursátiles (Acción de Gracias, Navidad, etc.) — es una
aproximación para evitar gastar cuota de FMP fuera de horario, no un calendario de mercado
completo. En el peor caso (feriado no detectado), simplemente se sigue llamando a la API en un
día que el mercado está cerrado igual, que es el comportamiento actual sin este chequeo.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def is_market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(MARKET_TZ)).astimezone(MARKET_TZ)
    if now.weekday() >= 5:  # sábado=5, domingo=6
        return False
    return MARKET_OPEN <= now.time() < MARKET_CLOSE
