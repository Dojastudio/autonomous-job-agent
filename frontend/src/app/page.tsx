"use client";

import React, { useEffect, useState } from "react";
import {
  Bot,
  ShieldCheck,
  Cpu,
  Database,
  CheckCircle2,
  Clock,
  AlertTriangle,
  Sparkles,
  X,
} from "lucide-react";
import OnboardingCard from "@/components/OnboardingCard";
import AgentControlBar from "@/components/AgentControlBar";
import ApplicationTracker from "@/components/ApplicationTracker";
import {
  ApplicationRecord,
  CandidateProfile,
  supabase,
  QABankItem,
} from "@/lib/supabase";

import AuthGate from "@/components/AuthGate";
import { apiFetch } from "@/lib/api";

const INITIAL_PROFILE: CandidateProfile = {
  id: "",
  full_name: null,
  email: null,
  phone: null,
  location: "Remote",
  linkedin_url: null,
  portfolio_url: null,
  github_url: null,
  resume_url: null,
  parsed_cv_json: {
    skills: [],
    experience_years: 0,
    work_history: [],
    education: [],
    target_roles: [],
  },
  auto_apply_enabled: false,
  daily_apply_limit: 15,
  min_match_score: 75,
};

const INITIAL_QA_BANK: QABankItem[] = [];

const INITIAL_APPLICATIONS: ApplicationRecord[] = [];

export default function Page() {
  return <AuthGate>{(id) => <DashboardPage key={id} userId={id} />}</AuthGate>;
}

function DashboardPage({ userId }: { userId: string }) {
  const [profile, setProfile] = useState<CandidateProfile>({
    ...INITIAL_PROFILE,
    id: userId,
  });
  const [qaBank, setQaBank] = useState<QABankItem[]>([]);
  const [applications, setApplications] = useState<ApplicationRecord[]>([]);
  const [run, setRun] = useState<{
    id: string;
    status: string;
    progress: string;
    error?: string;
  } | null>(null);
  const [workerOnline, setWorkerOnline] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [statusBanner, setStatusBanner] = useState<string | null>(null);
  const isRunningCycle =
    submitting || run?.status === "queued" || run?.status === "running";
  const refreshDashboardFromBackend = async () => {
    const res = await apiFetch("/api/agent");
    const data = await res.json();
    if (data.profile) setProfile(data.profile);
    setWorkerOnline(data.worker_online === true);
    setQaBank(data.qa_bank || []);
    setApplications(data.applications || []);
    setRun(data.run || null);
  };
  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        if (mounted) await refreshDashboardFromBackend();
      } catch (error) {
        if (mounted)
          setStatusBanner(
            error instanceof Error
              ? error.message
              : "Could not load dashboard.",
          );
      }
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);
  const handleUploadSuccess = () => {
    setStatusBanner("CV saved. Review your profile and screening questions.");
    void refreshDashboardFromBackend().catch((error) =>
      setStatusBanner(error.message),
    );
  };
  const handleSaveQAAnswers = async (
    answers: Array<{ question_key: string; answer_value: string }>,
  ) => {
    try {
      await apiFetch("/api/qa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers }),
      });
      await refreshDashboardFromBackend();
      setStatusBanner(
        "Answers saved. Eligible paused applications have been queued.",
      );
    } catch (error) {
      setStatusBanner(
        error instanceof Error ? error.message : "Could not save answers.",
      );
      throw error;
    }
  };
  const handleUpdateSettings = async (
    patch: Partial<
      Pick<
        CandidateProfile,
        "auto_apply_enabled" | "min_match_score" | "daily_apply_limit"
      >
    > & { target_roles?: string[]; target_locations?: string[] },
  ) => {
    try {
      await apiFetch("/api/agent", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      await refreshDashboardFromBackend();
      setStatusBanner("Preferences saved.");
    } catch (error) {
      setStatusBanner(
        error instanceof Error ? error.message : "Could not save preferences.",
      );
      throw error;
    }
  };
  const handleTriggerRunNow = async () => {
    setSubmitting(true);
    try {
      const res = await apiFetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_discovery: true,
          run_auto_apply: false,
        }),
      });
      setRun((await res.json()).run);
      setStatusBanner("Run queued. Progress will update here.");
    } catch (error) {
      setStatusBanner(
        error instanceof Error ? error.message : "Could not queue run.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  // KPI Metrics
  const appliedCount = applications.filter(
    (a) =>
      a.status === "applied" && !!a.confirmation && !a.evidence_review_required,
  ).length;
  const queuedCount = applications.filter((a) => a.status === "queued").length;
  const needsInputCount = applications.filter(
    (a) => a.status === "needs_human_input",
  ).length;
  const avgMatchScore =
    applications.length > 0
      ? Math.round(
          applications.reduce((acc, a) => acc + a.match_score, 0) /
            applications.length,
        )
      : 0;

  return (
    <div className="min-h-screen flex flex-col">
      {/* Sticky Glass Header */}
      <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white shadow-lg shadow-indigo-500/20">
              <Bot className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-sm sm:text-base font-bold tracking-tight text-zinc-100">
                  Autonomous Job Agent
                </h1>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-mono uppercase tracking-wider bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                  v2.0
                </span>
              </div>
              <p className="text-[11px] text-zinc-400 hidden sm:block">
                Find relevant roles and track confirmed applications
              </p>
            </div>
          </div>

          <button
            className="text-sm text-zinc-300"
            onClick={() => void supabase.auth.signOut()}
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-5">
        {!workerOnline && (
          <p
            role="status"
            className="rounded-xl border border-amber-800 bg-amber-950/30 p-4 text-sm text-amber-200"
          >
            The application worker is offline. You can update your profile;
            queued searches will wait for it to reconnect.
          </p>
        )}
        {run && (
          <div
            role="status"
            className="rounded-xl border border-zinc-700 bg-zinc-900 p-4 text-sm"
          >
            <strong className="capitalize">{run.status}</strong>
            <span className="ml-3 text-zinc-300">
              {run.error || run.progress}
            </span>
          </div>
        )}
        {/* Status Toast Banner */}
        {statusBanner && (
          <div className="bg-indigo-500/10 border border-indigo-500/30 text-indigo-200 px-4 py-2.5 rounded-xl text-xs font-medium flex items-center justify-between shadow-lg">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-indigo-400 shrink-0" />
              <span>{statusBanner}</span>
            </div>
            <button
              type="button"
              onClick={() => setStatusBanner(null)}
              className="text-indigo-300 hover:text-white p-1"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Top KPI Strip */}
        <section className="grid grid-cols-2 lg:grid-cols-4 gap-3.5">
          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Confirmed applications
              </span>
              <div className="text-2xl font-bold text-zinc-100 font-mono mt-0.5">
                {appliedCount}
                <span className="text-xs font-normal text-zinc-500 ml-1.5">
                  in latest 100 records
                </span>
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
              <CheckCircle2 className="w-4 h-4" />
            </div>
          </div>

          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Queued applications
              </span>
              <div className="text-2xl font-bold text-zinc-100 font-mono mt-0.5">
                {queuedCount}
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center text-sky-400">
              <Clock className="w-4 h-4" />
            </div>
          </div>

          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Paused (Needs Input)
              </span>
              <div className="text-2xl font-bold text-zinc-100 font-mono mt-0.5">
                {needsInputCount}
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400">
              <AlertTriangle className="w-4 h-4" />
            </div>
          </div>

          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Avg Match Score
              </span>
              <div className="text-2xl font-bold text-indigo-400 font-mono mt-0.5">
                {avgMatchScore}%
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
              <Sparkles className="w-4 h-4" />
            </div>
          </div>
        </section>

        {/* 1. Agent Control Bar */}
        <AgentControlBar
          autoApplyEnabled={profile.auto_apply_enabled}
          minMatchScore={profile.min_match_score}
          dailyApplyLimit={profile.daily_apply_limit}
          targetRoles={profile.parsed_cv_json?.target_roles || []}
          targetLocations={profile.parsed_cv_json?.target_locations || []}
          isRunningCycle={isRunningCycle}
          onUpdateSettings={handleUpdateSettings}
          onTriggerRunNow={handleTriggerRunNow}
        />

        {/* 2. Onboarding / Profile Card + Screening Q&A Memory Bank */}
        <OnboardingCard
          userId={profile.id}
          profile={profile}
          qaBank={qaBank}
          onUploadSuccess={handleUploadSuccess}
          onSaveAnswers={handleSaveQAAnswers}
        />

        {/* 3. Live Application Tracker Table */}
        <ApplicationTracker applications={applications} />
      </main>
    </div>
  );
}
