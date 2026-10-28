import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, askAssistant, getAssistantStatus } from "../api";
import { Badge, Banner, Button, Field, Panel } from "../components";
import { replySource } from "../lib/labels";
import type { AssistantStatus } from "../types";

type AssistantPanelProps = { token: string; onUnauthorized: () => void };

const FALLBACK_MESSAGES: Record<string, string> = {
  disabled: "Live AI is off. This answer comes directly from your ledger.",
  unconfigured: "Live AI needs a configured provider. This answer comes directly from your ledger.",
  rate_limited: "Live AI is temporarily busy. This answer comes directly from your ledger.",
  provider_unavailable: "The AI provider could not answer. This answer comes directly from your ledger.",
  provider_timeout: "The AI provider took too long. This answer comes directly from your ledger.",
  invalid_response: "The AI provider returned an unusable reply. This answer comes directly from your ledger.",
};

type Turn = { question: string; text: string; source: string; fallback?: string | null };

export function AssistantPanel({ token, onUnauthorized }: AssistantPanelProps) {
  const [question, setQuestion] = useState("Where did most of my spending go this month?");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [conversationId, setConversationId] = useState<string>();
  const [error, setError] = useState("");
  const [asking, setAsking] = useState(false);
  const conversation = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (conversation.current) conversation.current.scrollTop = conversation.current.scrollHeight;
  }, [turns]);

  useEffect(() => {
    let cancelled = false;
    getAssistantStatus(token)
      .then((value) => { if (!cancelled) setStatus(value); })
      .catch((reason) => {
        if (!cancelled && reason instanceof ApiError && reason.status === 401) onUnauthorized();
      });
    return () => { cancelled = true; };
  }, [token, onUnauthorized]);

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (asking || !question.trim()) return;
    setAsking(true);
    setError("");
    try {
      const response = await askAssistant(token, question, conversationId);
      setTurns((previous) => [...previous, { question, text: response.reply, source: response.reply_source, fallback: response.fallback_reason }]);
      setConversationId(response.conversation_id);
      setQuestion("");
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
      else setError(reason instanceof Error ? reason.message : "The assistant is unavailable");
    } finally {
      setAsking(false);
    }
  }

  return (
    <Panel id="assistant" as="article" variant="accent" className="assistant" labelledBy="assistant-title">
      <div className="assistant__orb" aria-hidden="true">✦</div>
      <span className="eyebrow">Read-only assistant</span>
      <h2 id="assistant-title">Ask your finances</h2>
      {status && <p className="assistant__mode"><Badge>{status.mode === "llm" ? "Live AI configured" : "Ledger mode"}</Badge> {status.message}</p>}
      <div ref={conversation} className="assistant__answer" role="log" aria-label="Assistant conversation" aria-live="polite">
        {turns.length === 0 && <p>Ask about spending, categories, or your largest purchases. Answers use transactions recorded in your workspace.</p>}
        {turns.map((turn, index) => {
          const source = replySource(turn.source);
          return <div className="assistant__turn" key={index}>
            <p className="assistant__question"><strong>You:</strong> {turn.question}</p>
            <p>{turn.text}</p>
            <Badge description={source.description}>{source.label}</Badge>
            {turn.fallback && FALLBACK_MESSAGES[turn.fallback] && <p className="assistant__fallback">{FALLBACK_MESSAGES[turn.fallback]}</p>}
          </div>;
        })}
      </div>
      <form onSubmit={ask} aria-busy={asking}>
        <Field multiline label="Question for the assistant" hideLabel value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} maxLength={1000} placeholder="Ask about your transactions…" disabled={asking} />
        {error && <Banner variant="inline">{error}</Banner>}
        <Button type="submit" loading={asking} loadingLabel="Thinking…">Ask Ledger AI</Button>
      </form>
    </Panel>
  );
}
