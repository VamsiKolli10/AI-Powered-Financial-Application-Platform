import { useCallback, useState } from "react";
import { Dashboard } from "./features/Dashboard";
import { Login } from "./features/Login";

const TOKEN_KEY = "ledger-token";

function readToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function App() {
  const [token, setToken] = useState(readToken);

  const authenticate = useCallback((value: string) => {
    try {
      sessionStorage.setItem(TOKEN_KEY, value);
    } catch {
      // Storage unavailable (private mode): the session still works until reload.
    }
    setToken(value);
  }, []);

  const logout = useCallback(() => {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      // Nothing stored to remove.
    }
    setToken("");
  }, []);

  return token ? <Dashboard token={token} onLogout={logout} /> : <Login onAuthenticated={authenticate} />;
}
