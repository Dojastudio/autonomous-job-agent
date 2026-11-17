"use client";
import { useEffect, useState, ReactNode } from "react";
import { supabase } from "@/lib/supabase";

export default function AuthGate({
  children,
}: {
  children: (id: string) => ReactNode;
}) {
  const [userId, setUserId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signup, setSignup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    supabase.auth.getSession().then(({ data, error }) => {
      setUserId(data.session?.user.id || null);
      setLoading(false);
      if (error) setMessage(error.message);
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setUserId(session?.user.id || null);
      setLoading(false);
    });
    return () => subscription.unsubscribe();
  }, []);
  if (loading)
    return (
      <main className="p-8 text-zinc-300" role="status">
        Loading your session…
      </main>
    );
  if (userId) return <>{children(userId)}</>;
  return (
    <main className="min-h-screen flex items-center justify-center p-6">
      <form
        className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900 p-8 space-y-5"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setMessage("");
          try {
            const { error } = signup
              ? await supabase.auth.signUp({
                  email,
                  password,
                  options: { emailRedirectTo: window.location.origin },
                })
              : await supabase.auth.signInWithPassword({ email, password });
            if (error) throw error;
            if (signup)
              setMessage(
                "Check your email to confirm your account, then sign in.",
              );
          } catch (err) {
            setMessage(
              err instanceof Error ? err.message : "Unable to sign in.",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        <div>
          <h1 className="text-2xl font-semibold">Job Aid</h1>
          <p className="mt-2 text-sm text-zinc-400">
            {signup
              ? "Create your account to start your job search."
              : "Sign in to manage your job search and applications."}
          </p>
        </div>
        <label className="block text-sm">
          Email
          <input
            className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 p-3"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block text-sm">
          Password
          <input
            className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 p-3"
            type="password"
            minLength={8}
            autoComplete={signup ? "new-password" : "current-password"}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {message && (
          <p role="status" className="text-sm text-amber-200">
            {message}
          </p>
        )}
        <button
          disabled={busy}
          className="w-full rounded-lg bg-indigo-500 p-3 font-medium disabled:opacity-50"
        >
          {busy ? "Please wait…" : signup ? "Create account" : "Sign in"}
        </button>
        <button
          type="button"
          className="text-sm text-indigo-300"
          onClick={() => {
            setSignup(!signup);
            setMessage("");
          }}
        >
          {signup ? "Already have an account? Sign in" : "Create an account"}
        </button>
      </form>
    </main>
  );
}
