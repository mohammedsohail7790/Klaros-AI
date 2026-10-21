/* Clean Room's drawn pantry: flat shapes in the five brand colours only (cream, Meadow Green, Apricot, Charcoal, Sage Mist). They illustrate shelf-stable groceries in general; none
   of them is a picture of a product we sell. Everything here is decorative (aria-hidden); the words next to each drawing carry the meaning. */

const C = {
  cream: "fill-[rgb(var(--color-background))]",
  sage: "fill-[rgb(var(--color-surface-muted))]",
  apricot: "fill-[rgb(var(--color-accent))]",
  green: "fill-[rgb(var(--color-brand))]",
  charcoal: "fill-[rgb(var(--color-foreground))]",
} as const;
type Tone = keyof typeof C;

type Kind = "jar" | "can" | "box" | "bag" | "bottle";
interface Item { kind: Kind; x: number; w: number; h: number; body: Tone; label: Tone; tilt?: number }

function Piece({ kind, x, w, h, body, label, y }: Item & { y: number }) {
  const top = y - h;
  switch (kind) {
    case "jar":
      return (
        <g>
          <rect x={x} y={top + h * 0.14} width={w} height={h * 0.86} rx={w * 0.14} className={C[body]} />
          <rect x={x + w * 0.1} y={top} width={w * 0.8} height={h * 0.16} rx={5} className={C.charcoal} />
          <rect x={x + w * 0.1} y={top + h * 0.42} width={w * 0.8} height={h * 0.3} rx={4} className={C[label]} />
        </g>
      );
    case "can":
      return (
        <g>
          <rect x={x} y={top} width={w} height={h} rx={8} className={C[body]} />
          <rect x={x} y={top + h * 0.1} width={w} height={4} className={C.charcoal} opacity={0.18} />
          <rect x={x} y={top + h * 0.9 - 4} width={w} height={4} className={C.charcoal} opacity={0.18} />
          <rect x={x + w * 0.14} y={top + h * 0.3} width={w * 0.72} height={h * 0.34} rx={4} className={C[label]} />
        </g>
      );
    case "box":
      return (
        <g>
          <rect x={x} y={top} width={w} height={h} rx={5} className={C[body]} />
          <rect x={x} y={top} width={w} height={h * 0.09} className={C.charcoal} opacity={0.16} />
          <circle cx={x + w / 2} cy={top + h * 0.46} r={w * 0.2} className={C[label]} />
          <rect x={x + w * 0.2} y={top + h * 0.76} width={w * 0.6} height={5} rx={2.5} className={C[label]} />
        </g>
      );
    case "bag":
      return (
        <g>
          <path d={`M${x + 4} ${top + 16} L${x + w - 4} ${top + 16} L${x + w} ${y} L${x} ${y} Z`} className={C[body]} />
          <path d={`M${x + 4} ${top + 16} Q${x + w * 0.25} ${top - 4} ${x + w * 0.5} ${top + 10} Q${x + w * 0.75} ${top - 4} ${x + w - 4} ${top + 16} Z`} className={C[body]} />
          <rect x={x + w * 0.16} y={top + h * 0.4} width={w * 0.68} height={h * 0.3} rx={6} className={C[label]} />
        </g>
      );
    case "bottle":
      return (
        <g>
          <rect x={x + w * 0.3} y={top} width={w * 0.4} height={h * 0.18} rx={4} className={C.charcoal} />
          <path d={`M${x + w * 0.32} ${top + h * 0.16} L${x + w * 0.68} ${top + h * 0.16} Q${x + w} ${top + h * 0.3} ${x + w} ${top + h * 0.45} L${x + w} ${y - 8} Q${x + w} ${y} ${x + w - 8} ${y} L${x + 8} ${y} Q${x} ${y} ${x} ${y - 8} L${x} ${top + h * 0.45} Q${x} ${top + h * 0.3} ${x + w * 0.32} ${top + h * 0.16} Z`} className={C[body]} />
          <rect x={x + w * 0.12} y={top + h * 0.5} width={w * 0.76} height={h * 0.26} rx={4} className={C[label]} />
        </g>
      );
  }
}

const SHELVES: { y: number; items: Item[] }[] = [
  { y: 172, items: [
    { kind: "jar", x: 70, w: 92, h: 118, body: "cream", label: "apricot" },
    { kind: "can", x: 178, w: 76, h: 96, body: "sage", label: "green" },
    { kind: "bag", x: 270, w: 104, h: 132, body: "apricot", label: "cream" },
    { kind: "box", x: 392, w: 92, h: 120, body: "cream", label: "green" },
    { kind: "bottle", x: 506, w: 70, h: 140, body: "sage", label: "apricot" },
    { kind: "can", x: 596, w: 82, h: 104, body: "cream", label: "apricot" },
    { kind: "jar", x: 698, w: 100, h: 124, body: "sage", label: "green" },
    { kind: "box", x: 818, w: 86, h: 112, body: "apricot", label: "cream" },
    { kind: "bag", x: 922, w: 98, h: 126, body: "cream", label: "green" },
    { kind: "can", x: 1038, w: 72, h: 92, body: "sage", label: "apricot" },
  ] },
  { y: 352, items: [
    { kind: "bag", x: 56, w: 108, h: 140, body: "sage", label: "green" },
    { kind: "jar", x: 182, w: 88, h: 112, body: "apricot", label: "cream" },
    { kind: "box", x: 288, w: 96, h: 128, body: "cream", label: "apricot" },
    { kind: "can", x: 402, w: 78, h: 100, body: "cream", label: "green" },
    { kind: "bottle", x: 500, w: 74, h: 146, body: "apricot", label: "cream" },
    { kind: "jar", x: 592, w: 96, h: 122, body: "cream", label: "green" },
    { kind: "can", x: 706, w: 80, h: 108, body: "sage", label: "apricot" },
    { kind: "bag", x: 804, w: 106, h: 134, body: "cream", label: "apricot" },
    { kind: "box", x: 928, w: 90, h: 116, body: "sage", label: "green" },
    { kind: "jar", x: 1036, w: 84, h: 106, body: "cream", label: "apricot" },
  ] },
  { y: 532, items: [
    { kind: "box", x: 84, w: 94, h: 118, body: "cream", label: "green" },
    { kind: "can", x: 196, w: 84, h: 106, body: "apricot", label: "cream" },
    { kind: "jar", x: 298, w: 100, h: 130, body: "sage", label: "apricot" },
    { kind: "bag", x: 416, w: 110, h: 138, body: "cream", label: "green" },
    { kind: "bottle", x: 544, w: 72, h: 138, body: "cream", label: "apricot" },
    { kind: "box", x: 634, w: 96, h: 124, body: "apricot", label: "cream" },
    { kind: "can", x: 750, w: 78, h: 100, body: "sage", label: "green" },
    { kind: "jar", x: 846, w: 94, h: 116, body: "cream", label: "apricot" },
    { kind: "bag", x: 958, w: 102, h: 130, body: "sage", label: "apricot" },
  ] },
];

/** The hero: three shelves of pantry goods that settle into place once, on load (see .cr-settle in globals.css). */
export function PantryScene({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 1180 560" className={className} aria-hidden="true" focusable="false" preserveAspectRatio="xMidYMax slice">
      {SHELVES.map((shelf, s) => (
        <g key={shelf.y}>
          <g className="cr-settle" style={{ animationDelay: `${0.5 + s * 0.16}s` }}>
            {shelf.items.map((it) => <Piece key={it.x} {...it} y={shelf.y} />)}
          </g>
          <rect x={0} y={shelf.y} width={1180} height={10} className={C.cream} opacity={0.9} />
          <rect x={0} y={shelf.y + 10} width={1180} height={6} className={C.charcoal} opacity={0.22} />
        </g>
      ))}
    </svg>
  );
}

/** A few goods on a shelf, for the "shelf-stable" panel. */
export function ShelfMark({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 480 360" className={className} aria-hidden="true" focusable="false">
      <Piece kind="jar" x={56} w={110} h={150} body="cream" label="apricot" y={250} />
      <Piece kind="can" x={186} w={92} h={118} body="green" label="cream" y={250} />
      <Piece kind="bag" x={298} w={122} h={166} body="apricot" label="cream" y={250} />
      <rect x={20} y={250} width={440} height={12} rx={6} className={C.green} />
      <rect x={44} y={262} width={10} height={64} className={C.green} opacity={0.5} />
      <rect x={426} y={262} width={10} height={64} className={C.green} opacity={0.5} />
    </svg>
  );
}

/** A parcel travelling from the supplier's warehouse to a door, for the "shipped by our supplier" panel. */
export function ParcelRoute({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 480 360" className={className} aria-hidden="true" focusable="false">
      <path d="M70 250 C 150 120, 260 330, 400 110" fill="none" strokeWidth={5} strokeDasharray="2 14" strokeLinecap="round" className="stroke-[rgb(var(--color-background))]" opacity={0.7} />
      <rect x={34} y={226} width={72} height={64} rx={6} className={C.cream} />
      <rect x={34} y={226} width={72} height={14} className={C.charcoal} opacity={0.2} />
      <rect x={52} y={256} width={36} height={22} rx={3} className={C.apricot} />
      <path d="M360 120 L400 84 L440 120 L440 168 L360 168 Z" className={C.cream} />
      <rect x={390} y={134} width={20} height={34} rx={2} className={C.apricot} />
      <g transform="rotate(-8 230 200)">
        <rect x={176} y={158} width={108} height={84} rx={8} className={C.apricot} />
        <rect x={176} y={158} width={108} height={18} className={C.charcoal} opacity={0.18} />
        <rect x={222} y={158} width={16} height={84} className={C.cream} opacity={0.85} />
        <rect x={190} y={206} width={26} height={18} rx={3} className={C.cream} />
      </g>
    </svg>
  );
}

/** A payment card with a lock, for the "you pay on Stripe's page" panel. */
export function SecureCard({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 480 360" className={className} aria-hidden="true" focusable="false">
      <rect x={70} y={96} width={300} height={188} rx={22} className={C.green} />
      <rect x={70} y={134} width={300} height={36} className={C.charcoal} opacity={0.35} />
      <rect x={100} y={206} width={54} height={40} rx={7} className={C.apricot} />
      <rect x={176} y={216} width={150} height={9} rx={4.5} className={C.cream} opacity={0.7} />
      <rect x={176} y={234} width={96} height={9} rx={4.5} className={C.cream} opacity={0.45} />
      <circle cx={356} cy={252} r={58} className={C.cream} />
      <rect x={326} y={246} width={60} height={46} rx={9} className={C.charcoal} />
      <path d="M338 246 V234 a18 18 0 0 1 36 0 V246" fill="none" strokeWidth={9} strokeLinecap="round" className="stroke-[rgb(var(--color-foreground))]" />
      <circle cx={356} cy={268} r={6} className={C.apricot} />
    </svg>
  );
}
