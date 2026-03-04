import { useState } from "react";
import "./ComedianSearch.css";

interface Comedian {
  name: string;
  slug: string;
  performanceCount: number;
  hue: number;
  sat: number;
  lit: number;
  initials: string;
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++)
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function prepareComedian(c: {
  name: string;
  slug: string;
  segments: { segment_type: string }[];
}): Comedian {
  const h = hash(c.name);
  return {
    name: c.name,
    slug: c.slug,
    performanceCount: c.segments.filter((s) => s.segment_type === "performance")
      .length,
    hue: h % 360,
    sat: 35 + (h % 25),
    lit: 12 + (h % 8),
    initials: c.name
      .split(/\s+/)
      .map((w) => w[0])
      .join("")
      .slice(0, 2)
      .toUpperCase(),
  };
}

function ComedianCard({ c }: { c: Comedian }) {
  return (
    <a href={`/comedians/${c.slug}`} className="card">
      <div
        className="poster"
        style={
          {
            "--h": c.hue,
            "--s": `${c.sat}%`,
            "--l": `${c.lit}%`,
          } as React.CSSProperties
        }
      >
        <svg
          className="mic-icon"
          viewBox="0 0 64 128"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <ellipse
            cx="32"
            cy="28"
            rx="14"
            ry="22"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <path
            d="M18 34 C18 52 46 52 46 34"
            stroke="currentColor"
            strokeWidth="1.5"
            fill="none"
          />
          <line
            x1="32"
            y1="52"
            x2="32"
            y2="90"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <line
            x1="22"
            y1="90"
            x2="42"
            y2="90"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
        <span className="initials">{c.initials}</span>
        <div className="poster-fade" />
      </div>
      <div className="info">
        <span className="card-name">{c.name}</span>
        {c.performanceCount > 0 && (
          <span className="card-sets">
            {c.performanceCount} set{c.performanceCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>
    </a>
  );
}

export default function ComedianSearch({
  comedians,
}: {
  comedians: Comedian[];
}) {
  const [query, setQuery] = useState("");

  const q = query.toLowerCase().trim();
  const filtered = q
    ? comedians.filter((c) => c.name.toLowerCase().includes(q))
    : comedians;

  return (
    <>
      <div className="search-bar">
        <svg
          className="search-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          type="text"
          placeholder="Search comedians…"
          autoComplete="off"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {filtered.length > 0 ? (
        <div className="row-wrap">
          {filtered.map((c) => (
            <ComedianCard key={c.slug} c={c} />
          ))}
        </div>
      ) : (
        <p className="no-results">No comedians found.</p>
      )}
    </>
  );
}
