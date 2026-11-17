// Spanish (rioplatense) copy and number formatting, in one place.
//
// * Number rules (WP14 finding 5): every number the UI prints goes through
//   one of the fmt* helpers, with a sensible precision and a unit.
//   Decimal point, not comma: the agent's own Spanish messages use it, and
//   mixing both on one screen reads worse.
// * Alarm copy: a Spanish title + a one-line plain explanation for every
//   code ears can emit (docs/ears.md; src/muestra/ears/*.py), with graceful
//   fallbacks for codes that do not exist yet.
// * Error copy: known backend issue codes → Spanish; the raw backend string
//   is only ever kept as a secondary detail.

// ---------------------------------------------------------------- numbers

/** Round to `max` decimals and drop trailing zeros. Non-finite → "—". */
export function fmtNum(n: number | null | undefined, max = 1): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const r = Number(n.toFixed(max));
  return Object.is(r, -0) ? "0" : String(r);
}

/** 249.99999999999793 → "250", 260.8 → "260.8". */
export function fmtBpm(n: number | null | undefined): string {
  return fmtNum(n, 1);
}

export function fmtDb(n: number | null | undefined, opts: { sign?: boolean; unit?: boolean } = {}): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const s = fmtNum(n, 1);
  const signed = opts.sign && n > 0.05 ? `+${s}` : s;
  return opts.unit === false ? signed : `${signed} dB`;
}

export function fmtLufs(n: number | null | undefined): string {
  return n == null || !Number.isFinite(n) ? "—" : `${fmtNum(n, 1)} LUFS`;
}

/** 0..1 → "87%"; tiny non-zero values read "<1%" instead of "0%". */
export function fmtPct(frac: number | null | undefined, digits = 0): string {
  if (frac == null || !Number.isFinite(frac)) return "—";
  const p = frac * 100;
  if (p > 0 && p < 0.5 && digits === 0) return "<1%";
  return `${fmtNum(p, digits)}%`;
}

export function fmtMs(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return `${fmtNum(n, Math.abs(n) < 10 ? 1 : 0)} ms`;
}

export function fmtBeats(n: number): string {
  return `${fmtNum(n, 2)} ${Math.abs(n - 1) < 1e-9 ? "beat" : "beats"}`;
}

export function signed(n: number, digits = 0): string {
  const s = fmtNum(n, digits);
  return n > 0 && s !== "0" ? `+${s}` : s;
}

// ---------------------------------------------------------------- keys

const PC: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

export interface ParsedKey {
  /** pitch class 0..11 */
  pc: number;
  /** the spelling used by the input ("D#", "Eb") */
  accidental: "#" | "b" | "";
  mode: "major" | "minor" | null;
}

/** "Eb", "D#", "F#m", "Bb minor", "C major" → pitch class + mode. */
export function parseKey(raw: string | null | undefined): ParsedKey | null {
  if (!raw) return null;
  const m = /^\s*([A-Ga-g])\s*([#♯b♭]?)\s*(.*)$/.exec(raw);
  if (!m) return null;
  const base = PC[m[1].toUpperCase()];
  const acc = m[2] === "#" || m[2] === "♯" ? "#" : m[2] === "b" || m[2] === "♭" ? "b" : "";
  const pc = (base + (acc === "#" ? 1 : acc === "b" ? -1 : 0) + 12) % 12;
  const rest = m[3].trim().toLowerCase();
  const mode = /^(m|min|minor|menor)\b/.test(rest) && !/^maj/.test(rest) ? "minor" : /^(maj|major|mayor)/.test(rest) ? "major" : null;
  return { pc, accidental: acc, mode };
}

/** Same note, whatever the spelling: D# = Eb. */
export function sameTonic(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = parseKey(a);
  const y = parseKey(b);
  return !!x && !!y && x.pc === y.pc;
}

/** One spelling for both: use the song's (target) accidental where the
 *  pitch class allows both, sharps otherwise. */
export function spellTonic(tonic: string, like: string | null | undefined): string {
  const k = parseKey(tonic);
  if (!k) return tonic;
  const pref = parseKey(like)?.accidental === "b" ? FLAT_NAMES : SHARP_NAMES;
  return pref[k.pc];
}

export function keyLabel(tonic: string, mode: "major" | "minor" | null | undefined, like?: string | null): string {
  const name = spellTonic(tonic, like ?? tonic);
  return mode === "minor" ? `${name} menor` : mode === "major" ? `${name} mayor` : name;
}

// ---------------------------------------------------------------- alarms

export interface AlarmCopy {
  title: string;
  /** one plain sentence: what it means for the music */
  explain: string;
}

/** name, range and how to say "too much / too little" of it */
const BAND_ES: Record<string, { name: string; hz: string; more: string; less: string }> = {
  sub: { name: "subgraves", hz: "20–60 Hz", more: "Demasiados subgraves", less: "Faltan subgraves" },
  bass: { name: "graves", hz: "60–250 Hz", more: "Demasiados graves", less: "Faltan graves" },
  low_mid: { name: "medios bajos", hz: "250–500 Hz", more: "Demasiados medios bajos", less: "Faltan medios bajos" },
  mid: { name: "medios", hz: "500 Hz–2 kHz", more: "Demasiados medios", less: "Faltan medios" },
  high_mid: { name: "medios altos", hz: "2–4 kHz", more: "Demasiados medios altos", less: "Faltan medios altos" },
  presence: { name: "presencia", hz: "4–8 kHz", more: "Demasiada presencia", less: "Falta presencia" },
  air: { name: "aire", hz: "8–20 kHz", more: "Demasiado aire", less: "Falta aire" },
};

const ALARMS: Record<string, AlarmCopy> = {
  // loudness / technical
  mix_silent: { title: "El mix está en silencio", explain: "No hay nada para escuchar: revisá mutes, ganancias y que los clips caigan dentro del rango renderizado." },
  clipping: { title: "Saturación digital (clipping)", explain: "El audio pasó el tope y se recorta: suena áspero. Bajá el nivel o el limitador del master." },
  true_peak_hot: { title: "Pico real muy alto", explain: "Está pegado al techo: al pasarlo a MP3 o al celular puede distorsionar. Dejale algo de margen." },
  loudness_off_target: { title: "Volumen lejos del objetivo", explain: "Suena más fuerte o más bajo de lo que pediste en el master (LUFS). Compará con tu referencia." },
  over_compressed: { title: "Demasiado comprimido", explain: "Casi no queda diferencia entre golpes fuertes y suaves: se siente plano y sin pegada." },
  no_loudness_target: { title: "Sin volumen objetivo", explain: "El master no tiene un volumen objetivo (LUFS): el nivel final queda a ojo." },
  dc_offset: { title: "Desplazamiento de continua", explain: "La señal tiene un corrimiento fijo que come margen y puede hacer clicks. Se arregla con un filtro pasa-altos." },
  low_end_phase: { title: "Graves fuera de fase", explain: "Los graves de izquierda y derecha se cancelan entre sí: en mono o en parlantes chicos se pierden." },
  track_silent: { title: "Pista en silencio", explain: "Esta pista no suena nada: revisá que no esté muteada ni fuera de rango." },
  track_inaudible: { title: "Pista casi inaudible", explain: "Está tan bajita en el mix que no se escucha. Subile el volumen o sacala." },
  // low end
  low_end_collision: { title: "Graves que se pisan", explain: "El bajo y el bombo suenan juntos en los graves y se tapan entre sí." },
  no_sidechain: { title: "Sin sidechain", explain: "El bajo y el bombo no se hacen lugar el uno al otro; con un sidechain el bombo pega más limpio." },
  sidechain_missing: { title: "Falta el sidechain", explain: "Hay choque de graves y no hay compresión por sidechain que lo resuelva." },
  sidechain_weak: { title: "Sidechain muy suave", explain: "El efecto bombea tan poco que casi no se nota: el bombo sigue tapado." },
  sidechain_no_recovery: { title: "El sidechain no se recupera", explain: "El volumen baja con cada golpe pero tarda demasiado en volver: el bajo se siente apagado." },
  // clicks
  click: { title: "Clicks en los cortes", explain: "Se oye un «tic» en el borde de un corte o una nota; suele arreglarse con un fundido corto." },
  loop_seam_click: { title: "Click al repetir el loop", explain: "El final del loop no empalma con el principio y se oye un salto al volver a empezar." },
  // timing
  lane_offset: { title: "Carril corrido de la grilla", explain: "Un golpe de la batería cae sistemáticamente antes o después del pulso." },
  notes_offset: { title: "Notas corridas de la grilla", explain: "Las notas de esta pista arrancan antes o después del pulso." },
  swing_mismatch: { title: "El swing no coincide", explain: "El «balanceo» de esta pista es distinto al del resto, y rítmicamente no encajan." },
  grid_drift: { title: "El tempo se va corriendo", explain: "El sample se va desfasando de la grilla a lo largo del loop." },
  clip_offset: { title: "Corte desfasado", explain: "Un corte del sample empieza corrido respecto del pulso." },
  grid_offset: { title: "Todo el sample corrido", explain: "El sample entero cae un poco antes o después de la grilla." },
  // tonal
  key_mismatch: { title: "Fuera de tonalidad", explain: "Lo que suena no está en la tonalidad de la canción: puede sonar «desafinado» contra el resto." },
  track_transposed: { title: "Pista transpuesta", explain: "Esta pista está subida o bajada de tono respecto del resto." },
  sample_detuned: { title: "Sample desafinado", explain: "El sample quedó levemente fuera de afinación contra el resto y va a «batir»." },
  bass_sample_clash: { title: "El bajo choca con el sample", explain: "Las notas del bajo y las del sample no armonizan entre sí." },
  // spectral extras not generated by ears but produced by older mocks/versions
  low_end_masking: { title: "Graves que se pisan", explain: "Dos pistas compiten por los mismos graves y se tapan entre sí." },
  masking: { title: "Graves que se pisan", explain: "Dos pistas compiten por los mismos graves y se tapan entre sí." },
  true_peak: { title: "Pico real muy alto", explain: "Está pegado al techo: al pasarlo a MP3 o al celular puede distorsionar." },
  alignment: { title: "Fuera de grilla", explain: "Los golpes caen corridos respecto del pulso." },
  off_grid: { title: "Fuera de grilla", explain: "Los golpes caen corridos respecto del pulso." },
  clicks: { title: "Clicks en los cortes", explain: "Se oye un «tic» en el borde de un corte; suele arreglarse con un fundido corto." },
  out_of_key: { title: "Fuera de tonalidad", explain: "Lo que suena no está en la tonalidad de la canción." },
  tonal: { title: "Fuera de tonalidad", explain: "Lo que suena no está en la tonalidad de la canción." },
  key: { title: "Fuera de tonalidad", explain: "Lo que suena no está en la tonalidad de la canción." },
  loudness: { title: "Volumen general", explain: "El volumen general se aleja de lo esperado." },
  too_quiet: { title: "Muy bajo de volumen", explain: "Suena más bajo que lo habitual para este estilo." },
  too_loud: { title: "Muy fuerte", explain: "Suena más fuerte que lo habitual y deja poco margen." },
};

/** Title + plain explanation for any alarm code, graceful for unknown ones. */
export function alarmCopy(code: string): AlarmCopy {
  const known = ALARMS[code];
  if (known) return known;
  const sp = /^spectral_([a-z_]+)_(excess|deficit)$/.exec(code);
  if (sp) {
    const band = BAND_ES[sp[1]];
    const name = band ? `${band.name} (${band.hz})` : `la banda «${sp[1].replace(/_/g, " ")}»`;
    return sp[2] === "excess"
      ? { title: band ? band.more : "Demasiado en una banda de frecuencias", explain: `Hay de más en ${name} comparado con tu referencia: el mix puede sonar pesado o tapado en esa zona.` }
      : { title: band ? band.less : "Falta algo en una banda de frecuencias", explain: `Hay de menos en ${name} comparado con tu referencia: el mix puede sonar flaco o apagado en esa zona.` };
  }
  return { title: "Aviso de los oídos del agente", explain: "El análisis automático marcó algo que esta versión de la app todavía no sabe explicar; el mensaje de abajo tiene el detalle." };
}

export function severityLabel(s: "info" | "warn" | "error"): string {
  return s === "error" ? "grave" : s === "warn" ? "ojo" : "aviso";
}

// ---------------------------------------------------------------- chop scores

export interface ScoreFacet {
  key: "seam" | "pulse" | "motion" | "tonal" | "bleed";
  /** question a musician asks */
  label: string;
  /** the answer in words */
  verdict: string;
  /** 0..1 for the little bar, when there is one */
  level: number | null;
  /** extra number in plain terms, if any */
  extra?: string;
}

function band(v: number, cuts: [number, string][], last: string): string {
  for (const [c, w] of cuts) if (v >= c) return w;
  return last;
}

/** The backend's four score sentences → what a musician wants to know. Lines
 *  it cannot parse come back in `rest` (kept as plain notes). */
export function describeChopScore(reasons: readonly string[] | null | undefined): { facets: ScoreFacet[]; rest: string[] } {
  const facets: ScoreFacet[] = [];
  const rest: string[] = [];
  for (const r of reasons ?? []) {
    let m: RegExpExecArray | null;
    if ((m = /Continuidad del loop:\s*(\d+(?:\.\d+)?)/i.exec(r))) {
      const v = Number(m[1]);
      facets.push({
        key: "seam",
        label: "Qué tan bien cierra el loop",
        verdict: band(v, [[0.8, "cierra muy bien"], [0.55, "cierra bien"], [0.3, "se nota el salto al repetir"]], "el salto se nota bastante"),
        level: v,
      });
    } else if ((m = /Pulso estable:\s*(\d+(?:\.\d+)?)/i.exec(r))) {
      const v = Number(m[1]);
      facets.push({
        key: "pulse",
        label: "Estabilidad del tempo",
        verdict: band(v, [[0.8, "firme, el pulso casi no se mueve"], [0.5, "algo irregular"]], "el tempo se mueve bastante"),
        level: v,
      });
    } else if ((m = /Energ[ií]a relativa:\s*(\d+(?:\.\d+)?);?\s*ataques por segundo:\s*(\d+(?:\.\d+)?)/i.exec(r))) {
      const e = Number(m[1]);
      const hits = Number(m[2]);
      facets.push({
        key: "motion",
        label: "Cuánto movimiento tiene",
        verdict: band(hits, [[4, "muy movido"], [2, "movido"], [0.8, "tranquilo"]], "casi sin ataques"),
        level: Math.min(1, hits / 4),
        extra: `${fmtNum(hits, 1)} golpes por segundo · ${band(e, [[0.8, "fuerte"], [0.4, "volumen medio"]], "suave")} para esta fuente`,
      });
    } else if ((m = /Contenido tonal:\s*(\d+(?:\.\d+)?);?\s*coincidencia con voz[^:]*:\s*(\d+(?:\.\d+)?)/i.exec(r))) {
      const t = Number(m[1]);
      const bleed = Number(m[2]);
      facets.push({
        key: "tonal",
        label: "Qué tan melódico es",
        verdict: band(t, [[0.6, "muy melódico"], [0.3, "algo melódico"]], "más bien percusivo o ruidoso"),
        level: t,
      });
      if (bleed >= 0.15) {
        facets.push({
          key: "bleed",
          label: "Se cuela la voz",
          verdict: bleed >= 0.4 ? "se escucha bastante voz de fondo" : "se cuela un poco de voz",
          level: Math.min(1, bleed),
        });
      }
    } else {
      rest.push(r);
    }
  }
  return { facets, rest };
}

/** Overall verdict for the 0..1 score (the card's headline). */
export function scoreVerdict(v: number): string {
  return band(v, [[0.75, "muy buen candidato"], [0.5, "buen candidato"], [0.3, "dudoso"]], "flojo");
}

// ---------------------------------------------------------------- errors

export interface ApiIssueLike {
  code?: string;
  message?: string;
  hint?: string | null;
  path?: string;
}

export interface FriendlyError {
  /** Spanish, what to tell the person */
  message: string;
  /** what to do about it, when we know */
  advice: string | null;
  /** the backend's own text (English), secondary */
  detail: string | null;
  code: string | null;
}

const SPANISH_CHARS = /[áéíóúñ¿¡]/i;

const ISSUE_COPY: Record<string, { message: string; advice?: string }> = {
  clip_overrun: {
    message: "Ese corte es más largo y el clip ya no entra en la canción.",
    advice: "Alargá la canción (length_bars) o bajá la repetición del clip; pedíselo al agente.",
  },
  clip_position: { message: "El clip quedaría empezando antes del inicio de la canción.", advice: "Pedile al agente que lo mueva." },
  chop_overrun: { message: "El corte ajustado no entra en la grilla de tiempo de la fuente.", advice: "Probá con un ajuste más chico." },
  stale_candidates: {
    message: "Esos candidatos quedaron viejos: la fuente o su grilla cambió desde que el agente los propuso.",
    advice: "Pedile al agente que busque candidatos de nuevo.",
  },
  ref_not_found: { message: "No encontré ese candidato o esa fuente.", advice: "Puede que el agente los haya regenerado; recargá la lista." },
  grid_mismatch: { message: "El corte no coincide con la grilla de tiempo.", advice: "Probá con otro candidato o sin ajuste." },
  invalid_value: { message: "Alguno de los valores no es válido.", advice: "Revisá el nombre del chop y el ajuste." },
  out_of_range: { message: "Un valor quedó fuera de rango.", advice: "Probá con un ajuste más chico." },
  duplicate_id: { message: "Ya existe algo con ese nombre." },
  file_not_found: { message: "Falta un archivo en el proyecto.", advice: "Pedile al agente que lo regenere." },
  invalid_audio: { message: "Ese audio no se pudo leer." },
  project_locked: { message: "Esta pantalla está atada a otro proyecto.", advice: "Abrí `muestra serve` con el proyecto que querés." },
  not_implemented: { message: "Esa función todavía no está disponible en el servidor." },
  internal_error: { message: "Falló algo adentro del servidor.", advice: "Mirá la terminal donde corre `muestra serve`." },
  cancelled: { message: "Se canceló antes de empezar." },
  too_large: { message: "El pedido es demasiado grande." },
  already_exists: { message: "Eso ya existe." },
};

/** Map any thrown error (ApiError with issues, network failure, …) to
 *  Spanish copy, keeping the backend's own text as `detail`. */
export function friendlyError(
  e: unknown,
  what = "No se pudo completar la acción",
): FriendlyError {
  const err = e as { message?: string; status?: number | null; issues?: ApiIssueLike[] } | null;
  const issues = Array.isArray(err?.issues) ? err!.issues! : [];
  const raw = err?.message ?? (typeof e === "string" ? e : null);
  const first = issues.find((i) => i.code && ISSUE_COPY[i.code]) ?? null;
  if (first?.code) {
    const c = ISSUE_COPY[first.code];
    return { message: c.message, advice: c.advice ?? null, detail: first.message ?? raw, code: first.code };
  }
  if (err && err.status == null && raw) {
    return {
      message: "No pude hablar con el servidor.",
      advice: "Fijate que `muestra serve` siga corriendo y que tengas conexión con la compu.",
      detail: raw,
      code: "network",
    };
  }
  if (err?.status === 401 || err?.status === 403) {
    return { message: "El servidor no me dejó entrar.", advice: "Abrí el link que muestra `muestra serve` en la terminal.", detail: raw, code: "unauthorized" };
  }
  const code = issues[0]?.code ?? null;
  const msg = issues[0]?.message ?? raw ?? null;
  // a backend message that is already Spanish may be shown as the message
  if (msg && SPANISH_CHARS.test(msg)) return { message: msg, advice: issues[0]?.hint ?? null, detail: null, code };
  return { message: what + ".", advice: null, detail: msg, code };
}

// ---------------------------------------------------------------- versions without audio

/** What to say to the agent to get the audio of a version back. */
export function regenRequest(version: string): string {
  return `La versión ${version} no tiene audio (se borró renders/). Regenerala con render de ${version}, sin cambiar la canción.`;
}
