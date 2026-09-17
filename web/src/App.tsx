import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  askAssistant,
  getNotifications,
  getSummary,
  getTransactions,
  login,
} from "./api";
import type { NotificationList, Summary, Transaction } from "./types";

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

function formatMoney(value: string | number) {
  return money.format(Number(value));
}

function Login({ onAuthenticated }: { onAuthenticated: (token: string) => void }) {
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("demo-password");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      onAuthenticated(await login(email, password));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign in");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-copy">
        <span className="eyebrow">Financial intelligence, grounded in your ledger</span>
        <h1>See where your money is going. Ask why.</h1>
        <p>
          Transactions move through event-driven services, AI categorization, anomaly detection,
          and a read-only assistant that keeps the arithmetic outside the model.
        </p>
        <div className="trust-row"><span>FastAPI</span><span>Kafka</span><span>PostgreSQL</span><span>OpenAI</span></div>
      </section>
      <form className="login-card" onSubmit={submit}>
        <div className="mark">L</div>
        <h2>Welcome to Ledger AI</h2>
        <p>Use the seeded demo account after running the setup steps.</p>
        <label>Email<input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required /></label>
        <label>Password<input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required /></label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={loading}>{loading ? "Signing in…" : "Open dashboard"}</button>
      </form>
    </main>
  );
}

function Dashboard({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [notifications, setNotifications] = useState<NotificationList>({ notifications: [], unread_count: 0 });
  const [question, setQuestion] = useState("Where did most of my spending go this month?");
  const [answer, setAnswer] = useState("");
  const [conversationId, setConversationId] = useState<string>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    Promise.all([getTransactions(token), getSummary(token), getNotifications(token)])
      .then(([page, nextSummary, nextNotifications]) => {
        setTransactions(page.items);
        setSummary(nextSummary);
        setNotifications(nextNotifications);
      })
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 401) onLogout();
        else setError(reason instanceof Error ? reason.message : "Unable to load dashboard");
      })
      .finally(() => setLoading(false));
  }, [token, onLogout]);

  const totals = useMemo(() => transactions.reduce(
    (value, transaction) => {
      const amount = Number(transaction.amount);
      if (amount < 0) value.outflow += Math.abs(amount);
      else value.inflow += amount;
      return value;
    },
    { inflow: 0, outflow: 0 },
  ), [transactions]);

  const maxCategory = Math.max(...(summary?.by_category.map((item) => Number(item.amount)) ?? [1]), 1);

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setError("");
    try {
      const response = await askAssistant(token, question, conversationId);
      setAnswer(response.reply);
      setConversationId(response.conversation_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The assistant is unavailable");
    } finally {
      setAsking(false);
    }
  }

  if (loading) return <main className="loading"><div className="pulse" />Loading your financial picture…</main>;

  return (
    <div className="app-shell">
      <aside>
        <div className="brand"><span className="mark">L</span><strong>Ledger AI</strong></div>
        <nav><a className="active" href="#overview">Overview</a><a href="#transactions">Transactions</a><a href="#assistant">Assistant</a></nav>
        <div className="system-card"><span className="status-dot" />All systems operational<small>Gateway + 4 services</small></div>
        <button className="text-button" onClick={onLogout}>Sign out</button>
      </aside>
      <main className="dashboard">
        <header><div><span className="eyebrow">Personal finance workspace</span><h1>Good morning, Vamsi.</h1></div><div className="period">This month</div></header>
        {error && <div className="banner">{error}</div>}

        <section id="overview" className="metric-grid">
          <article><span>Cash flow</span><strong>{formatMoney(totals.inflow - totals.outflow)}</strong><small>Across {transactions.length} recent transactions</small></article>
          <article><span>Money in</span><strong>{formatMoney(totals.inflow)}</strong><small>Recent credits</small></article>
          <article><span>Money out</span><strong>{formatMoney(summary?.total_spend ?? totals.outflow)}</strong><small>{summary?.change_vs_previous_pct == null ? "Current period" : `${summary.change_vs_previous_pct}% vs previous`}</small></article>
          <article className="alert-metric"><span>Unread alerts</span><strong>{notifications.unread_count}</strong><small>AI and rules-based monitoring</small></article>
        </section>

        <section className="main-grid">
          <article className="panel spending-panel">
            <div className="panel-heading"><div><span className="eyebrow">Insights</span><h2>Spending breakdown</h2></div><span className="source">{summary?.summary_source ?? "fallback"}</span></div>
            <p className="summary-copy">{summary?.summary ?? "No summary is available for this period yet."}</p>
            <div className="category-list">
              {summary?.by_category.slice(0, 6).map((item) => (
                <div className="category" key={item.category}>
                  <div><span>{item.category}</span><strong>{formatMoney(item.amount)}</strong></div>
                  <div className="bar"><i style={{ width: `${Math.max(5, Number(item.amount) / maxCategory * 100)}%` }} /></div>
                </div>
              ))}
            </div>
          </article>

          <article id="assistant" className="panel assistant-panel">
            <div className="assistant-orb">✦</div><span className="eyebrow">Read-only assistant</span><h2>Ask your finances</h2>
            <p>{answer || "I use scoped ledger aggregates for the numbers, then the model helps explain them clearly."}</p>
            <form onSubmit={ask}><textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} /><button className="primary" disabled={asking}>{asking ? "Thinking…" : "Ask Ledger AI"}</button></form>
          </article>
        </section>

        <section id="transactions" className="panel table-panel">
          <div className="panel-heading"><div><span className="eyebrow">Activity</span><h2>Recent transactions</h2></div><span>{transactions.length} shown</span></div>
          <div className="table-scroll"><table><thead><tr><th>Merchant</th><th>Category</th><th>Source</th><th>Date</th><th>Amount</th></tr></thead><tbody>
            {transactions.slice(0, 10).map((tx) => <tr key={tx.id}><td><strong>{tx.merchant ?? tx.description}</strong>{(tx.anomaly_score ?? 0) >= .8 && <em>Review</em>}</td><td>{tx.category ?? "Categorizing…"}</td><td><span className="pill">{tx.category_source ?? "pending"}</span></td><td>{date.format(new Date(tx.occurred_at))}</td><td className={Number(tx.amount) < 0 ? "negative" : "positive"}>{formatMoney(tx.amount)}</td></tr>)}
          </tbody></table></div>
        </section>

        <section className="panel alert-list"><div className="panel-heading"><div><span className="eyebrow">Monitoring</span><h2>Recent alerts</h2></div></div>
          {notifications.notifications.length ? notifications.notifications.map((item) => <div className="alert-row" key={item.id}><span className={item.read ? "muted-dot" : "alert-dot"} /><div><strong>{item.type.replaceAll("_", " ")}</strong><p>{item.message}</p></div><time>{date.format(new Date(item.created_at))}</time></div>) : <p className="empty">No recent alerts. That is usually good news.</p>}
        </section>
      </main>
    </div>
  );
}

export function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem("ledger-token") ?? "");
  function authenticate(value: string) { sessionStorage.setItem("ledger-token", value); setToken(value); }
  function logout() { sessionStorage.removeItem("ledger-token"); setToken(""); }
  return token ? <Dashboard token={token} onLogout={logout} /> : <Login onAuthenticated={authenticate} />;
}
