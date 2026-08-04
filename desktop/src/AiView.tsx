import { useCallback, useEffect, useState } from "react";
import { AnalysisContextNote } from "./AnalysisContextNote";
import {
  askAi, explainInsight, loadAiSettings, loadInsightFeed, saveAiSettings,
} from "./api";
import { InsightCard } from "./InsightCard";
import type { AiAnswer, AiSettings, Insight, InsightFeed } from "./types";

interface Props {
  onOpenSettings: () => void;
  onDrill?: (drill: NonNullable<Insight["drill"]>) => void;
}

// Starting points, because a blank box is the hardest thing to answer. Each
// one is phrased so the model is asked to interpret Northstar's figures
// rather than to work anything out for itself.
const suggestionsFor = (monthLabel: string) => [
  {
    label: "Where should I start?",
    note: "One change, ranked by what it would actually save",
    question: "Looking at my figures, what is the single change that would "
      + "free up the most money without touching anything I marked essential? "
      + "Say why you picked it over the alternatives.",
  },
  {
    label: `Is ${monthLabel} normal?`,
    note: "Compares that supported month against my own history",
    question: `Compare ${monthLabel} against my usual pattern. Is anything `
      + "genuinely unusual, or is it an ordinary month? Say so plainly if it "
      + "is ordinary.",
  },
  {
    label: "What am I not seeing?",
    note: "Looks for the quiet costs rather than the obvious ones",
    question: "What in these figures would most people miss about their own "
      + "spending? Look for small repeated costs rather than the obvious "
      + "large ones.",
  },
];

function AiView({ onOpenSettings, onDrill }: Props) {
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [feed, setFeed] = useState<InsightFeed | null>(null);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AiAnswer | null>(null);
  const [loading, setLoading] = useState(true);
  const [asking, setAsking] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      // Sequential: each request starts a short-lived sidecar, and running
      // them together races first-run schema work.
      setSettings((await loadAiSettings()).ai);
      setFeed(await loadInsightFeed());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const updateProfile = useCallback(
    async (spec: Parameters<typeof saveAiSettings>[0]) => {
      setSavingProfile(true);
      setError("");
      try {
        setSettings((await saveAiSettings(spec)).ai);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setSavingProfile(false);
      }
    }, []);

  const submit = useCallback(async (text?: string) => {
    const asked = (text ?? question).trim();
    if (!asked) return;
    setAsking(true);
    setError("");
    setResult(null);
    try {
      setResult(await askAi(asked));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAsking(false);
    }
  }, [question]);

  if (loading) {
    return <section className="workflow-panel">
      <div className="loading-panel">
        <span className="loading-dot" />Looking through your figures…
      </div>
    </section>;
  }

  const ready = Boolean(
    settings?.enabled && settings?.base_url && settings?.model
    && (settings?.is_local || settings?.api_key_set),
  );
  const concerns = feed?.concerns ?? [];
  const nothing = concerns.length === 0 && !feed?.positive;
  const monthLabel = feed?.analysis.data_month_label || "this period";
  const suggestions = suggestionsFor(monthLabel);

  return <section className="workflow-panel ai-workspace">
    <div className="panel-head">
      <div>
        <span className="eyebrow">Coach <em className="beta-tag">Beta</em></span>
        <h2>What Northstar noticed</h2>
        <p className="warning-text">
          Beta: provider compatibility and AI answers may be incomplete or
          occasionally fail. Northstar's financial figures are still
          calculated locally.
        </p>
        <p>
          Every figure below was worked out on this computer. AI is optional:
          it only explains a finding you pick, and never calculates anything.
        </p>
      </div>
      <button type="button" className="ghost-button" onClick={onOpenSettings}>
        AI privacy and connection
      </button>
    </div>

    <AnalysisContextNote analysis={feed?.analysis} />
    {feed?.missing_data && <p className="warning-text">{feed.missing_data}</p>}

    {nothing ? <div className="calm-empty">
      <strong>Nothing stands out in {monthLabel}</strong>
      <p>
        Northstar checked your weekday pattern, merchant visits, category
        ranges, recurring prices, and the same month a year earlier. Nothing crossed
        the threshold worth interrupting you for.
      </p>
    </div> : <div className="insight-list">
      {concerns.map((insight) => <InsightCard
        key={insight.id}
        insight={insight}
        canExplain={ready}
        onExplain={explainInsight}
        onDrill={onDrill}
      />)}
      {feed?.positive && <InsightCard
        insight={feed.positive}
        canExplain={ready}
        onExplain={explainInsight}
        onDrill={onDrill}
      />}
    </div>}

    {settings && <article className="form-card ai-profile-card">
      <div className="chart-card-head">
        <div>
          <span className="eyebrow">Personalize the advice</span>
          <h3>What should Northstar help with?</h3>
        </div>
        {savingProfile && <span className="badge">Saving…</span>}
      </div>
      <div className="form-grid">
        <label>Current focus
          <select value={settings.focus} disabled={savingProfile}
            onChange={(event) => void updateProfile({ focus: event.target.value })}>
            {settings.focus_options.map((option) =>
              <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <small>This changes what the AI prioritizes, never the calculations.</small>
        </label>
        <label>Coaching style
          <select value={settings.style} disabled={savingProfile}
            onChange={(event) => void updateProfile({ style: event.target.value })}>
            {settings.style_options.map((option) =>
              <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <small>Choose how concise or detailed the explanation should be.</small>
        </label>
      </div>
      <details className="formula-details ai-essential-picker">
        <summary>Categories I consider essential</summary>
        <p className="guidance">
          The coach will avoid suggesting cuts here unless you ask about one.
        </p>
        <div className="ai-category-grid">
          {settings.essential_category_options.map((category) => {
            const checked = settings.essential_categories.includes(category);
            return <label className="check-label" key={category}>
              <input type="checkbox" checked={checked} disabled={savingProfile}
                onChange={() => {
                  const next = checked
                    ? settings.essential_categories.filter((item) => item !== category)
                    : [...settings.essential_categories, category];
                  void updateProfile({ essentialCategories: next });
                }} />
              {category}
            </label>;
          })}
        </div>
      </details>
    </article>}

    {!ready ? <article className="form-card ai-onboarding-card">
      <h3>Connect a provider to have findings explained</h3>
      <p className="guidance">
        The findings above are calculated locally and work without this. An AI
        adds a short explanation and one suggested action. Choose MiniMax, or a
        model running on this computer so nothing leaves it at all.
      </p>
      <div className="button-row">
        <button type="button" onClick={onOpenSettings}>Open AI settings</button>
      </div>
      {error && <div className="inline-error">{error}</div>}
    </article> : <article className="chart-card ai-freeform">
      <div className="chart-card-head">
        <div>
          <span className="eyebrow ai-eyebrow">Uses AI</span>
          <h3>Ask anything about your money</h3>
          <p className="chart-explainer">
            Explaining a finding sends only that finding. A question here sends
            {" "}{settings?.scope === "summary" ? "your category totals"
              : settings?.scope === "merchants"
                ? "your totals and merchant names"
                : "your individual transactions"} for {settings?.months} month
            {settings?.months === 1 ? "" : "s"}, which is a great deal more.
            Change what is shared in AI settings.
          </p>
        </div>
      </div>
      <div className="preset-row ai-prompt-row">
        {suggestions.map((prompt) => <button
          type="button" className="preset-button" key={prompt.label}
          disabled={asking} onClick={() => { setQuestion(prompt.question); void submit(prompt.question); }}>
          <strong>{prompt.label}</strong>
          <small>{prompt.note}</small>
        </button>)}
      </div>
      <label className="ai-question-label">
        Or ask in your own words
        <textarea value={question} maxLength={500} rows={3} disabled={asking}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Where is my money actually going, and what would you change first?" />
      </label>
      <div className="button-row">
        <button type="button" disabled={asking || !question.trim()}
          onClick={() => void submit()}>
          {asking ? "Reading your figures…" : "Ask"}
        </button>
        <span className="ai-scope-note">
          {settings?.is_local
            ? "Answered by a model on this computer. Nothing leaves it."
            : `Sent to ${settings?.base_url?.replace(/^https?:\/\//, "").split("/")[0]}.`}
        </span>
      </div>
      {error && <div className="inline-error">{error}</div>}
      {result && <div className="insight-explain" aria-live="polite">
        <span className="from-ai">
          Written by {result.provider} · {result.model} from figures Northstar
          calculated
        </span>
        {result.answer}
      </div>}
    </article>}
  </section>;
}

export default AiView;
