// Логотип «Контур»: слово в рамке из четырёх светящихся уголков — синие и красные по диагонали.
export function Logo({ size = "md" }: { size?: "md" | "lg" }) {
  return (
    <span className={`kontur-logo ${size}`} role="img" aria-label="Контур">
      <svg className="kontur-frame" viewBox="0 0 200 64" preserveAspectRatio="none" aria-hidden>
        <path className="k-blue" d="M4 22 V4 H70" />
        <path className="k-red" d="M130 4 H196 V22" />
        <path className="k-red" d="M4 42 V60 H70" />
        <path className="k-blue" d="M130 60 H196 V42" />
      </svg>
      <span className="kontur-word" aria-hidden>КОНТУР</span>
    </span>
  );
}
