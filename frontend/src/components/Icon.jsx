import React from "react";

// Bộ icon SVG (Lucide-style, stroke 24x24) — KHÔNG dùng emoji. currentColor theo text.
const P = {
  scan: ["M3 7V5a2 2 0 0 1 2-2h2", "M17 3h2a2 2 0 0 1 2 2v2", "M21 17v2a2 2 0 0 1-2 2h-2", "M7 21H5a2 2 0 0 1-2-2v-2", "M7 12h10"],
  fileText: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M16 13H8", "M16 17H8", "M10 9H8"],
  sparkles: ["M9.94 14.06 8.5 14.06l1.44-.001A2 2 0 0 0 11.5 12.5l.5-2 .5 2a2 2 0 0 0 1.56 1.56l2 .5-2 .5A2 2 0 0 0 12.5 16.5l-.5 2-.5-2a2 2 0 0 0-1.56-1.56z", "M12 3v4", "M19 5v3", "M20.5 6.5h-3"],
  table: ["M3 9h18", "M3 15h18", "M12 3v18"],
  layers: ["m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z", "m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65", "m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"],
  scissors: ["M20 4 8.12 15.88", "M14.47 14.48 20 20", "M8.12 8.12 12 12"],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10l5 5 5-5", "M12 15V3"],
  refresh: ["M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8", "M21 3v5h-5", "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16", "M8 16H3v5"],
  chevronLeft: ["m15 18-6-6 6-6"],
  chevronRight: ["m9 18 6-6-6-6"],
  check: ["M20 6 9 17l-5-5"],
  upload: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M17 8l-5-5-5 5", "M12 3v12"],
  sliders: ["M21 4H14", "M10 4H3", "M21 12H12", "M8 12H3", "M21 20H16", "M12 20H3", "M14 2v4", "M8 10v4", "M16 18v4"],
  image: ["m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21", "M8 9h.01"],
  chevronDown: ["m6 9 6 6 6-6"],
  search: ["m21 21-4.34-4.34"],
  zoomIn: ["m21 21-4.34-4.34", "M11 8v6", "M8 11h6"],
  zoomOut: ["m21 21-4.34-4.34", "M8 11h6"],
  x: ["M18 6 6 18", "M6 6l12 12"],
  bell: ["M10.3 21a1.94 1.94 0 0 0 3.4 0", "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"],
  plus: ["M5 12h14", "M12 5v14"],
  trash: ["M3 6h18", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2", "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"],
  alertTriangle: ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z", "M12 9v4", "M12 17h.01"],
  checkCircle: ["m9 12 2 2 4-4"],
  clock: ["M12 6v6l4 2"],
  ban: ["m4.9 4.9 14.2 14.2"],
  barChart: ["M3 3v18h18", "M18 17V9", "M13 17V5", "M8 17v-3"],
  logOut: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  folder: ["M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"],
  settings: ["M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"],
};

const RECT = { table: true, image: true };
const CIRCLES = {
  scissors: [{ cx: 6, cy: 6, r: 3 }, { cx: 6, cy: 18, r: 3 }],
  image: [{ cx: 9, cy: 9, r: 2 }],
  search: [{ cx: 11, cy: 11, r: 8 }],
  zoomIn: [{ cx: 11, cy: 11, r: 8 }],
  zoomOut: [{ cx: 11, cy: 11, r: 8 }],
  checkCircle: [{ cx: 12, cy: 12, r: 10 }],
  clock: [{ cx: 12, cy: 12, r: 10 }],
  ban: [{ cx: 12, cy: 12, r: 10 }],
  settings: [{ cx: 12, cy: 12, r: 3 }],
};

export default function Icon({ name, size = 18, stroke = 2, className = "" }) {
  const paths = P[name] || [];
  return (
    <svg
      className={`ico ${className}`} width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
    >
      {RECT[name] && <rect x="3" y="3" width="18" height="18" rx="2" />}
      {(CIRCLES[name] || []).map((c, i) => <circle key={i} cx={c.cx} cy={c.cy} r={c.r} />)}
      {paths.map((d, i) => <path key={i} d={d} />)}
    </svg>
  );
}
