import { ImageResponse } from "next/og";

export const alt = "Klaros AI: build and operate your business with AI";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/** The preview card shown when a Klaros link is shared (chat apps, social, search results). */
export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          background: "linear-gradient(135deg, #1f2a44 0%, #2b3a5e 100%)",
          color: "white",
        }}
      >
        <div style={{ fontSize: 40, fontStyle: "italic", color: "#c9a45c", fontFamily: "Georgia, serif" }}>Klaros AI</div>
        <div style={{ marginTop: 28, fontSize: 76, lineHeight: 1.08, fontFamily: "Georgia, serif" }}>Build and operate your business with AI.</div>
        <div style={{ marginTop: 32, fontSize: 30, color: "rgba(255,255,255,0.75)" }}>
          Customers, providers, consent, referrals and revenue in one workspace.
        </div>
      </div>
    ),
    { ...size },
  );
}
