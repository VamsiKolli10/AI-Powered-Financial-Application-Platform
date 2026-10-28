import { type FormEvent, useState } from "react";
import { login } from "../api";
import { Banner, BrandMark, Button, Field } from "../components";

export function Login({ onAuthenticated }: { onAuthenticated: (token: string) => void }) {
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
    <main className="login">
      <section className="login__copy">
        <span className="eyebrow">Financial intelligence, grounded in your ledger</span>
        <h1>See where your money is going. Ask why.</h1>
        <p>
          Transactions move through event-driven services, AI categorization, anomaly detection,
          and a read-only assistant that keeps the arithmetic outside the model.
        </p>
        <ul className="login__stack" aria-label="Built with">
          {["FastAPI", "Kafka", "PostgreSQL", "OpenAI"].map((name) => (
            <li key={name} className="badge badge--neutral">{name}</li>
          ))}
        </ul>
      </section>
      <form className="login__card" onSubmit={submit}>
        <BrandMark />
        <h2>Welcome to Ledger AI</h2>
        <p>Sign in to your workspace. New local setups start with an empty ledger.</p>
        <Field label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
        <Field label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        {error && <Banner variant="inline">{error}</Banner>}
        <Button type="submit" loading={loading} loadingLabel="Signing in…">Open dashboard</Button>
      </form>
    </main>
  );
}
