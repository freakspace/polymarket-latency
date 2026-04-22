import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          base: "#f5f7fb",
          surface: "#ffffff",
          elevated: "#ffffff",
          muted: "#f1f5f9",
        },
        border: {
          DEFAULT: "#e2e8f0",
          strong: "#cbd5e1",
        },
        text: {
          DEFAULT: "#0f172a",
          muted: "#475569",
          subtle: "#64748b",
        },
        accent: {
          DEFAULT: "#2563eb",
          soft: "rgba(37, 99, 235, 0.10)",
        },
        good: {
          DEFAULT: "#059669",
          soft: "rgba(5, 150, 105, 0.10)",
        },
        warn: {
          DEFAULT: "#b45309",
          soft: "rgba(180, 83, 9, 0.10)",
        },
        bad: {
          DEFAULT: "#dc2626",
          soft: "rgba(220, 38, 38, 0.10)",
        },
        segment: {
          normal: "#c2410c",
          stall: "#f59e0b",
          topo1: "#7c3aed",
        },
        topology: {
          1: "#2563eb",
          2: "#059669",
          5: "#7c3aed",
          10: "#db2777",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      fontSize: {
        xxs: ["11px", { lineHeight: "14px" }],
      },
    },
  },
  plugins: [],
};

export default config;
