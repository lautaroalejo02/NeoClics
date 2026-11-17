# Herramientas pagas

Regla: **nunca compres, suscribas, actives un trial ni te registres en nada**, ni aunque
el humano lo sugiera al pasar. Vos preparás la evidencia; Lautaro decide y paga él.

## Cuándo proponer algo pago

Solo con una hipótesis de sonido concreta que la versión gratis no resolvió después de
probar las alternativas gratis de la librería y de los alias. Ejemplos válidos:

- "Con los tres bombos de la librería el comentario sigue siendo 'suena plano'; un pack X
  de bombos de disco podría resolverlo".
- "El slap de Surge (`slap-bass`) da clicks en notas cortas y el finger-bass no tiene slap;
  un bajo modelado (MODO BASS 2, ~USD 300) tiene articulaciones de slap reales".

No: "sería mejor con plugins profesionales".

## Qué preparar (informe para `docs/evaluaciones/<nombre>.md` del repo de Muestra)

1. Problema de sonido que resolvería, con la versión y el comentario que lo muestran.
2. Precio (pago único o suscripción) y fuente del precio con fecha; "a confirmar" si no lo
   verificaste.
3. Licencia: uso comercial, YouTube, redistribución, uso con IA.
4. Compatibilidad: Windows y macOS, VST3/AU, si corre headless con pedalboard (lo
   verificás solo con una demo gratuita que él haya instalado, nunca vos).
5. Alternativas gratis probadas y por qué no alcanzaron.
6. Cómo se va a comparar (abajo). El resultado y la decisión los anota él en
   `docs/decisions.md`.

## A/B a ciegas

Cuando Lautaro instaló la demo/trial por su cuenta:

1. Partí de una versión que le gusta. Hacé dos `song.yaml` idénticos salvo la pieza a
   comparar (mismo MIDI/notas, mismo chop, mismos efectos).
2. Igualá volumen: renderizá las dos y compará `ears.compare` → `track_level_delta_db` del
   track en cuestión; ajustá `gain_db` hasta que la diferencia sea < 0.5 dB (y
   `lufs_delta` del mix < 0.5 LU). Sin esto gana el que suena más fuerte.
3. Renderizá en orden aleatorio para que "A" no sea
   siempre la gratis. `summary` y `request` neutros: "Comparación ciega, opción A". No
   pongas el nombre del producto en el summary, en los ids de track ni en comentarios del
   `song.yaml` visibles en la UI hasta que elija (`instrument.plugin` sí lo dice: avisale que
   no abra el diff).
4. Guardá qué versión es cuál en un archivo que él no tenga que abrir para votar (ej.
   `docs/ab-clave.md` del proyecto, sin mencionarlo en el chat) y revelalo recién después.
5. Pedile que compare A/B en la UI en la misma posición y marque el ganador (queda como
   `ab_choice` en el feedback). Idealmente en 2-3 partes del loop distintas.
6. Revelá, completá el informe con el resultado y dejá la decisión en sus manos.
