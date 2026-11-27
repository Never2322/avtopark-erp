// Живой фон страницы входа. Картинка «Контур» лежит снизу, а поверх неё — слой в тех же координатах (1672 × 941 точек картинки):
// по неоновым линиям бегут огоньки, уголки логотипа мерцают как неоновые трубки, по слову пробегает блик, вывески на машине
// и гараже иногда моргают, светят фары, над мокрым асфальтом летят искры и плывёт туман. Цвета берутся из темы.

// неоновые линии картинки (по ним бегут огоньки): [путь, цвет 1 — синий, 2 — красный, длительность, задержка]
const RUNS: [string, 1 | 2, number, number][] = [
  ["M330,0 L215,112 L75,112 L0,190", 1, 3.6, 0],
  ["M1672,160 L1585,262 L1375,262", 2, 4.2, 1.1],
  ["M1672,690 L1590,760 L1400,760", 2, 3.9, 2.3],
  ["M1560,800 L1450,800 L1320,941", 1, 4.6, 0.6],
  ["M240,858 L455,858 L520,941", 1, 3.4, 1.8],
  ["M560,90 L1112,90", 1, 6.5, 3.2],
];
// уголки рамки логотипа на картинке
const CORNERS: [string, 1 | 2][] = [
  ["M754,134 V90 H832", 1],
  ["M870,90 H924 V134", 2],
  ["M754,188 V230 H824", 2],
  ["M862,230 H924 V188", 1],
];
const SPARKS = Array.from({ length: 16 }, (_, i) => ({
  left: (i * 61 + 7) % 100, delay: (i * 0.73) % 9, dur: 7 + ((i * 37) % 6), size: 2 + (i % 3), c: i % 2 ? 2 : 1,
}));

export function LoginScene() {
  return (
    <>
      <div className="login-bg" aria-hidden />
      <div className="login-mist" aria-hidden><span /><span /></div>
      <svg className="login-fx" viewBox="0 0 1672 941" preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id="lg-shine" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="#fff" stopOpacity="0" /><stop offset="0.5" stopColor="#fff" stopOpacity="0.85" /><stop offset="1" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
          <radialGradient id="lg-flare-1"><stop offset="0" stopColor="#fff" stopOpacity="0.95" /><stop offset="0.25" stopColor="var(--lg-c1)" stopOpacity="0.6" /><stop offset="1" stopColor="var(--lg-c1)" stopOpacity="0" /></radialGradient>
          <radialGradient id="lg-flare-2"><stop offset="0" stopColor="#fff" stopOpacity="0.9" /><stop offset="0.3" stopColor="var(--lg-c2)" stopOpacity="0.6" /><stop offset="1" stopColor="var(--lg-c2)" stopOpacity="0" /></radialGradient>
          <clipPath id="lg-word"><rect x="668" y="136" width="344" height="52" /></clipPath>
        </defs>
        {/* огонёк = широкий полупрозрачный штрих (свечение) + тонкий белый (сердцевина): без фильтров, чтобы не грузить браузер */}
        {RUNS.map(([d, c, dur, delay], i) => {
          const st = { animationDuration: `${dur}s`, animationDelay: `${delay}s` };
          return (
            <g key={i}>
              <path className={`lg-run lg-halo c${c}`} d={d} pathLength={100} style={st} />
              <path className={`lg-run lg-core`} d={d} pathLength={100} style={st} />
            </g>
          );
        })}
        {CORNERS.map(([d, c], i) => <path key={d} className={`lg-corner c${c}`} d={d} style={{ animationDelay: `${i * 1.7}s` }} />)}
        <g clipPath="url(#lg-word)"><rect className="lg-shine" x="560" y="130" width="90" height="64" fill="url(#lg-shine)" /></g>
        {/* фары мусоровоза и красный фонарь у гаража */}
        <circle className="lg-flare" cx="58" cy="594" r="46" fill="url(#lg-flare-1)" />
        <circle className="lg-flare" cx="196" cy="590" r="40" fill="url(#lg-flare-1)" style={{ animationDelay: "0.4s" }} />
        <circle className="lg-flare red" cx="1631" cy="560" r="34" fill="url(#lg-flare-2)" />
        {/* вывески «КОНТУР» на машине и на гараже иногда моргают */}
        <rect className="lg-blink" x="396" y="452" width="104" height="66" rx="6" />
        <rect className="lg-blink late" x="1365" y="385" width="158" height="62" rx="6" />
      </svg>
      <div className="login-ground" aria-hidden />
      <div className="login-sparks" aria-hidden>
        {SPARKS.map((s, i) => (
          <span key={i} className={`c${s.c}`} style={{ left: `${s.left}%`, width: s.size, height: s.size, animationDelay: `${s.delay}s`, animationDuration: `${s.dur}s` }} />
        ))}
      </div>
    </>
  );
}
