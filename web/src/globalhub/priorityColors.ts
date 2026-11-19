/** The color of each task priority, P0 (red) to P3 (green): a tinted pill and a solid dot. */
export const PRIORITY_COLORS = [
  {
    value: 0,
    pill: "border-[rgba(239,68,68,0.55)] bg-[rgba(239,68,68,0.07)] text-[#b91c1c] dark:bg-[rgba(239,68,68,0.08)] dark:text-[#f87171]",
    dot: "bg-[#ef4444]",
  },
  {
    value: 1,
    pill: "border-[rgba(249,115,22,0.55)] bg-[rgba(249,115,22,0.07)] text-[#c2410c] dark:bg-[rgba(249,115,22,0.08)] dark:text-[#fb923c]",
    dot: "bg-[#f97316]",
  },
  {
    value: 2,
    pill: "border-[rgba(234,179,8,0.6)] bg-[rgba(234,179,8,0.09)] text-[#a16207] dark:bg-[rgba(234,179,8,0.08)] dark:text-[#facc15]",
    dot: "bg-[#eab308]",
  },
  {
    value: 3,
    pill: "border-[rgba(34,197,94,0.55)] bg-[rgba(34,197,94,0.07)] text-[#15803d] dark:bg-[rgba(34,197,94,0.08)] dark:text-[#4ade80]",
    dot: "bg-[#22c55e]",
  },
];
