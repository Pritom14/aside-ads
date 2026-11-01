/**
 * Example React component for rendering an Aside ad BESIDE a model answer.
 * Copy it into your app and restyle it. This is example code, not a published package.
 *
 * Your backend calls `AsideClient.getSponsored(...)` (it holds the API key) and sends
 * the resulting `SponsoredAd` to the browser alongside the assistant's answer.
 */
import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { trackImpression, type SponsoredAd } from "aside-ads";

export interface SponsoredCardProps {
  ad: SponsoredAd;
  className?: string;
}

export function SponsoredCard({ ad, className }: SponsoredCardProps) {
  const ref = useRef<HTMLElement | null>(null);

  // Count the impression once, when at least half of the card is visible.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let sent = false;
    const fire = () => {
      if (!sent) {
        sent = true;
        void trackImpression(ad);
      }
    };
    if (typeof IntersectionObserver === "undefined") {
      fire();
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting && e.intersectionRatio >= 0.5)) {
          fire();
          observer.disconnect();
        }
      },
      { threshold: [0.5] },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ad.impressionUrl]);

  return (
    <aside ref={ref} className={className} aria-label={`Sponsored: ${ad.advertiser}`} style={styles.card}>
      <span style={styles.label}>{ad.label}</span>
      {ad.imageUrl ? <img src={ad.imageUrl} alt="" style={styles.image} /> : null}
      <strong style={styles.headline}>{ad.headline}</strong>
      {ad.body ? <p style={styles.body}>{ad.body}</p> : null}
      <a href={ad.url} target="_blank" rel="sponsored noopener" style={styles.cta}>
        {ad.cta}
      </a>
      <span style={styles.domain}>
        {ad.advertiser} · {ad.displayUrl}
      </span>
    </aside>
  );
}

/** Two-column layout: the answer on the left, the sponsored card in its own column. */
export function AnswerWithAside({ answer, ad }: { answer: ReactNode; ad: SponsoredAd | null }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 260px", gap: 16, alignItems: "start" }}>
      <div>{answer}</div>
      {ad ? <SponsoredCard ad={ad} /> : <div />}
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  card: { display: "flex", flexDirection: "column", gap: 6, padding: 12, border: "1px solid #e3e5e8", borderRadius: 10, font: "14px/1.4 system-ui, sans-serif" },
  label: { fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", color: "#6b7078" },
  image: { width: "100%", borderRadius: 6 },
  headline: { fontSize: 15 },
  body: { margin: 0, color: "#4a4f57" },
  cta: { alignSelf: "flex-start", padding: "6px 10px", borderRadius: 6, background: "#1d1f23", color: "#fff", textDecoration: "none" },
  domain: { fontSize: 12, color: "#6b7078" },
};
