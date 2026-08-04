import type { AnalysisContext } from "./types";

export function AnalysisContextNote({
  analysis,
}: {
  analysis: AnalysisContext | null | undefined;
}) {
  if (!analysis) return null;
  return (
    <p
      className={`analysis-context-note analysis-context-${analysis.state}`}
      aria-live="polite"
    >
      {analysis.label}
    </p>
  );
}
