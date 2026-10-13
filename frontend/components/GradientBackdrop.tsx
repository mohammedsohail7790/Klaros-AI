/**
 * The shared background for every marketing and auth surface: a quiet indigo glow
 * at the top over a faint dot grid. Pure CSS — no canvas, no animation loop.
 */
export default function GradientBackdrop() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(60% 50% at 50% -10%, rgb(var(--color-accent) / 0.14) 0%, transparent 70%), radial-gradient(40% 40% at 100% 20%, rgb(124 58 237 / 0.07) 0%, transparent 70%)",
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.55]"
        style={{
          backgroundImage: "radial-gradient(rgb(var(--color-border-strong)) 1px, transparent 1px)",
          backgroundSize: "26px 26px",
          maskImage: "radial-gradient(70% 60% at 50% 0%, black 0%, transparent 75%)",
          WebkitMaskImage: "radial-gradient(70% 60% at 50% 0%, black 0%, transparent 75%)",
        }}
      />
    </div>
  );
}
