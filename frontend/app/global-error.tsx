"use client";

/** Last-resort boundary for a crash in the root layout itself. It cannot rely on the app's styles or fonts, so it is deliberately plain. */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center", margin: 0, padding: 24 }}>
        <div role="alert" style={{ maxWidth: 420, textAlign: "center" }}>
          <h1 style={{ fontSize: 22 }}>Klaros hit a problem</h1>
          <p style={{ color: "#555" }}>Please reload the page. If it keeps happening, contact us at meetklaros.com/contact.</p>
          <button type="button" onClick={reset} style={{ padding: "8px 16px", borderRadius: 8, border: "1px solid #ccc", cursor: "pointer" }}>
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
