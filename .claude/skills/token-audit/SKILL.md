---
name: token-audit
description: Audita el footprint de tokens de este proyecto (CLAUDE.md, skills, memoria) y qué procesos podrían delegarse a scripts de Python en vez de tokens de LLM. Usar cuando el usuario pida revisar/optimizar consumo de tokens, o evaluar qué pasar a scripts.
---

# Auditoría de consumo de tokens

Repasá, en este orden, y corregí lo que sea seguro corregir sin perder ningún dato, decisión,
número o caveat ya documentado — esto es RELOCALIZAR contenido (cuándo se carga, dónde vive),
nunca resumirlo ni recortarlo:

1. **`CLAUDE.md`** (se carga entero en CADA conversación, sin condición): medí tamaño (`wc -l
   -c CLAUDE.md`) y señalá cualquier contenido específico de una sola pestaña/feature en vez de
   genuinamente transversal — eso pertenece al `SKILL.md` de esa pestaña, no acá (precedente:
   memoria `feedback_claude_md_token_efficiency`, y la ronda de auditoría de abajo).

2. **Cada `SKILL.md`** (se carga entero cada vez que esa skill se invoca): medí tamaño
   (`wc -l -c .claude/skills/*/SKILL.md | sort -n`). Si una sección de "Design history"/racional
   narrativo supera ~1/3 del archivo, externalizala a `references/design-history.md` dentro del
   directorio de esa skill, dejando en el `SKILL.md` un puntero corto ("ver
   `references/design-history.md` antes de tocar X"). Nunca borrar contenido — solo pasa de "se
   lee siempre" a "se lee bajo demanda, cuando la tarea realmente lo necesita". Verificá con
   `grep -n "^## Design history" .claude/skills/*/SKILL.md` cuáles ya la tienen.

3. **Duplicación entre skills**: buscá párrafos casi idénticos describiendo el mismo mecanismo
   compartido en 2+ skills (ej. una función usada por dos pestañas, descripta completa en
   ambas). Dejá la descripción completa en la skill dueña del archivo que la define, y en la
   otra un puntero de una línea.

4. **Procesos descriptos en prosa que re-derivan un cómputo repetible**: donde una skill diga
   que un script fue "descartable"/"scratchpad"/"no está en el repo", revisá si esa misma
   metodología ya se re-implementó más de una vez (`grep -rn "60/40\|chronological\|split
   cronológico" .claude/skills/` es un buen punto de partida en este proyecto — la metodología
   de validación fuera de muestra se repitió al menos 6-8 veces antes de esta skill existir). Si
   sí, extraela a un script real, parametrizable y versionado en `scripts/`, y actualizá cada
   skill que la mencionaba para que diga "correr `scripts/X.py`" en vez de re-explicar la
   metodología. Esto cambia tokens-re-derivando-lógica por ciclos-de-CPU-corriendo-un-script.

5. **Pasos de verificación manual que se re-escriben sesión tras sesión** (smoke tests, arrancar/
   parar el server, etc.): mismo tratamiento — promoverlos a un script en `scripts/` y que la
   skill correspondiente solo lo referencie.

Después de cada corrección, releé el `SKILL.md` + el archivo de referencia nuevo y confirmá que
ninguna oración/número/caveat se perdió en la mudanza (es una relocalización, no una reescritura
— un diff a ojo contra el original alcanza). Si algo parece genuinamente redundante o
desactualizado (no solo relocalizable), marcalo para que el usuario confirme antes de borrarlo —
no borrar unilateralmente.

Cerrá con una tabla antes/después (líneas/bytes) de cada archivo tocado, y agregá una entrada
nueva a "## Historial de auditorías" abajo con qué se hizo — para que la próxima corrida de esta
skill no vuelva a proponer lo mismo.

## Herramientas ya disponibles (de la primera pasada)

- `scripts/oos_validate.py` — validador fuera-de-muestra reusable (split cronológico 60/40,
  consistencia de signo train/test en varios horizontes, barrido de umbrales para detectar
  fragilidad). Importar `run_oos_validation`/`run_oos_validation_sweep` en vez de re-derivar esta
  metodología para una nueva investigación puntual (régimen, RSI, drawdown, score de un motor,
  etc.) — ver el docstring del módulo para el patrón de uso.
- `scripts/verify_app.py` — smoke test de las 6 pestañas vía `streamlit.testing.v1.AppTest`
  (`./venv/Scripts/python.exe scripts/verify_app.py`) en vez de retipear el snippet de AppTest
  cada sesión.
- `scripts/run_app.sh` / `scripts/stop_app.sh` — arrancar/parar el servidor Streamlit local (ver
  skill `financial-advisor-run-app`).

## Historial de auditorías

**Primera pasada (esta sesión).** Motivada por: el usuario pidió explícitamente auditar consumo
de tokens y evaluar qué delegar a scripts, después de una sesión que había cargado ~108KB de
contenido de skills (run-app + portfolio + speculation + cripto) para agregar el motor de
soporte/resistencia a Especulación.

Hallazgos y acciones:

1. **"Design history" era 48-69% de las 3 skills grandes** (cripto 279/578 líneas, portfolio
   154/256, speculation 303/433) — contenido narrativo de "por qué", necesario para no repetir
   investigaciones descartadas, pero no necesario en CADA invocación. Externalizado a
   `references/design-history.md` en cada una, verbatim (0 palabras perdidas, confirmado
   re-leyendo cada archivo nuevo contra el original visto en la misma conversación).

   | Skill | `SKILL.md` antes | `SKILL.md` después | `references/design-history.md` (nuevo) |
   |---|---|---|---|
   | `financial-advisor-cripto` | 578 líneas / 47,559 B | 326 líneas / 26,661 B | 264 líneas / 21,769 B |
   | `financial-advisor-portfolio` | 256 líneas / 19,685 B | 107 líneas / 6,747 B | 161 líneas / 13,668 B |
   | `financial-advisor-speculation` | 433 líneas / 35,319 B* | 137 líneas / 9,451 B | 313 líneas / 26,956 B |
   | `financial-advisor-run-app` | 138 líneas / 5,860 B | 88 líneas / 4,627 B | (sin references/ — ver más abajo) |

   *`financial-advisor-speculation` ya había crecido a 433 líneas dentro de esta misma sesión (se le
   agregó la sección del Market Reaction Zone Engine para acciones) antes de esta auditoría —
   el número "antes" de la tabla es el tamaño en el momento de auditar, no el original de sesiones
   previas.

2. **`financial-advisor-run-app` tenía ~90 líneas de bash embebido en prosa** (selección de puerto,
   health check, kill por línea de comando) que un LLM tenía que releer y re-tipear cada vez que
   arrancaba/paraba el servidor. Promovido a `scripts/run_app.sh` / `scripts/stop_app.sh` (probados
   de punta a punta: arranque, detección de instancia viva para reusar, y parada — los 3 caminos
   funcionan). El `SKILL.md` quedó con la lógica de decisión y una llamada a los scripts, no el
   bash completo.

3. **La metodología de validación fuera de muestra (split cronológico 60/40, chequeo de
   consistencia de signo en 4 horizontes, barrido de umbrales) se había re-derivado como "script
   descartable, no en el repo" al menos 6-8 veces**: Fibonacci, régimen/RSI, ADX, OBV,
   drawdown buckets, y 2 rondas del motor de soporte/resistencia. Extraída a
   `scripts/oos_validate.py` (`run_oos_validation`/`run_oos_validation_sweep`, con auto-test
   sintético incluido — no depende de red/proveedor). Las 3 skills grandes actualizadas para
   apuntar a este script en vez de decir "script descartable" sin más. No se re-corrió ninguna
   validación vieja retroactivamente — la herramienta queda lista para la PRÓXIMA vez que se pida
   revalidar algo.

4. **`CLAUDE.md` revisado — nada más movible en esta pasada.** Ya había pasado por una ronda de
   este mismo tipo de optimización (documentada en la memoria
   `feedback_claude_md_token_efficiency`); no se encontró contenido tab-específico adicional que
   no estuviera ya en la skill correspondiente.

5. **No se tocó duplicación cross-skill en esta pasada** (paso 3 de la lista de arriba) — se
   evaluó de pasada pero no se encontró un caso lo bastante grande como para justificar el
   riesgo de tocar 2 archivos a la vez en esta ronda; queda pendiente para la próxima corrida de
   esta skill si el contenido de cripto/speculation sigue creciendo en paralelo (ambas describen
   `render_speculation_indicators()` con cierto solapamiento).

Verificación: `python scripts/verify_app.py` → 0 excepciones en las 6 pestañas, antes y después de
todos los cambios de esta pasada. `scripts/run_app.sh`/`stop_app.sh` probados de punta a punta.
