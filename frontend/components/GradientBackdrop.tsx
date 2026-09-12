export default function GradientBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div
        className="absolute -top-40 left-1/4 h-[32rem] w-[32rem] -translate-x-1/2 rounded-full opacity-[0.16] blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
      />
      <div
        className="absolute top-1/3 right-0 h-[28rem] w-[28rem] translate-x-1/3 rounded-full opacity-[0.14] blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent-2)) 0%, transparent 70%)" }}
      />
      <div
        className="absolute bottom-0 left-0 h-[24rem] w-[24rem] -translate-x-1/3 translate-y-1/3 rounded-full opacity-[0.10] blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
      />
    </div>
  );
}
