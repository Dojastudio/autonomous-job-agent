"use client";

import React, { useState } from "react";
import {
  CheckCircle2,
  Clock,
  AlertCircle,
  XCircle,
  FileCode2,
  Image as ImageIcon,
  ExternalLink,
  X,
  ShieldAlert,
  Search,
  MapPin,
} from "lucide-react";
import { ApplicationRecord, ApplicationStatus } from "@/lib/supabase";

interface ApplicationTrackerProps {
  applications: ApplicationRecord[];
}

const FILTER_TABS: Array<{ label: string; value: "all" | ApplicationStatus }> = [
  { label: "All", value: "all" },
  { label: "Applied", value: "applied" },
  { label: "Queued", value: "queued" },
  { label: "Needs Input", value: "needs_human_input" },
  { label: "Failed", value: "failed" },
];

function formatDeterministicTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export default function ApplicationTracker({
  applications,
}: ApplicationTrackerProps) {
  const [selectedStatus, setSelectedStatus] = useState<
    "all" | ApplicationStatus
  >("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [answersModalApp, setAnswersModalApp] =
    useState<ApplicationRecord | null>(null);
  const [proofModalApp, setProofModalApp] =
    useState<ApplicationRecord | null>(null);

  const filteredApps = applications.filter((app) => {
    const matchesStatus =
      selectedStatus === "all" ? true : app.status === selectedStatus;
    if (!matchesStatus) return false;
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    const company = app.jobs_discovered?.company_name?.toLowerCase() || "";
    const role = app.jobs_discovered?.job_title?.toLowerCase() || "";
    const source = app.jobs_discovered?.source?.toLowerCase() || "";
    return company.includes(q) || role.includes(q) || source.includes(q);
  });

  const renderStatusBadge = (status: ApplicationStatus) => {
    switch (status) {
      case "applied":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/25">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            Applied
          </span>
        );
      case "queued":
      case "applying":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-sky-500/10 text-sky-400 border border-sky-500/25">
            <Clock className="w-3 h-3" />
            {status === "applying" ? "Applying..." : "Queued"}
          </span>
        );
      case "needs_human_input":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/25">
            <AlertCircle className="w-3 h-3" />
            Needs Input
          </span>
        );
      case "failed":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/25">
            <XCircle className="w-3 h-3" />
            Failed
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-zinc-800 text-zinc-300">
            {status}
          </span>
        );
    }
  };

  return (
    <section className="rounded-2xl bg-zinc-900/70 border border-zinc-800/80 backdrop-blur-md shadow-2xl overflow-hidden">
      {/* Top Toolbar */}
      <div className="p-5 border-b border-zinc-800/80 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">
            Live Application Pipeline
          </h2>
          <p className="text-[11px] text-zinc-400">
            Verifiable DOM field mappings &amp; full-page submission screenshots
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Search Input */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search company, role, ATS..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 pr-3 py-1.5 rounded-xl bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 w-52 transition"
            />
          </div>

          {/* Filter Pills */}
          <div className="flex items-center gap-1 bg-zinc-950 p-1 rounded-xl border border-zinc-800/90">
            {FILTER_TABS.map((tab) => {
              const active = selectedStatus === tab.value;
              const count =
                tab.value === "all"
                  ? applications.length
                  : applications.filter((a) => a.status === tab.value).length;
              return (
                <button
                  key={tab.value}
                  type="button"
                  onClick={() => setSelectedStatus(tab.value)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-medium transition ${
                    active
                      ? "bg-zinc-800 text-white shadow-sm"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {tab.label}
                  <span
                    className={`ml-1.5 font-mono text-[10px] ${
                      active ? "text-indigo-400" : "text-zinc-500"
                    }`}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-zinc-800/80 bg-zinc-950/40 text-[11px] font-medium uppercase tracking-wider text-zinc-500">
              <th className="py-3 px-5">Company &amp; Role</th>
              <th className="py-3 px-4">Match</th>
              <th className="py-3 px-4">ATS Source</th>
              <th className="py-3 px-4">Status</th>
              <th className="py-3 px-4">Submitted</th>
              <th className="py-3 px-5 text-right">Verification</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/60 text-sm">
            {filteredApps.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="py-12 text-center text-zinc-500 text-xs"
                >
                  No applications match your current filter.
                </td>
              </tr>
            ) : (
              filteredApps.map((app) => {
                const job = app.jobs_discovered;
                const company = job?.company_name || "Unknown";
                const initial = company.charAt(0).toUpperCase();

                return (
                  <tr
                    key={app.id}
                    className="group hover:bg-zinc-800/30 transition-colors"
                  >
                    {/* Company + Role + Reasoning */}
                    <td className="py-3.5 px-5">
                      <div className="flex items-start gap-3">
                        <div className="w-9 h-9 rounded-xl bg-zinc-800/90 border border-zinc-700/70 flex items-center justify-center text-xs font-bold text-zinc-200 shrink-0 mt-0.5">
                          {initial}
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-zinc-100 text-xs sm:text-sm">
                              {job?.job_title || "Software Engineer"}
                            </span>
                            {job?.apply_url && (
                              <a
                                href={job.apply_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-zinc-500 hover:text-indigo-400 transition"
                                title="Open ATS Job Posting"
                              >
                                <ExternalLink className="w-3.5 h-3.5" />
                              </a>
                            )}
                          </div>

                          <div className="flex items-center gap-2 text-xs text-zinc-400 mt-0.5">
                            <span className="font-medium text-zinc-300">
                              {company}
                            </span>
                            <span>•</span>
                            <span className="inline-flex items-center gap-1 text-[11px] text-zinc-400">
                              <MapPin className="w-3 h-3 text-zinc-500" />
                              {job?.location || "Remote"}
                            </span>
                          </div>

                          {app.match_reasoning && (
                            <p className="text-[11px] text-zinc-500 line-clamp-1 mt-1">
                              {app.match_reasoning}
                            </p>
                          )}

                          {app.error_log && app.status !== "applied" && (
                            <div className="mt-1.5 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-amber-500/10 border border-amber-500/20 text-[11px] text-amber-300">
                              <ShieldAlert className="w-3 h-3 shrink-0" />
                              <span>{app.error_log}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    </td>

                    {/* Match Score with visual bar */}
                    <td className="py-3.5 px-4">
                      <div className="flex flex-col gap-1 w-20">
                        <span
                          className={`font-mono text-xs font-bold ${
                            app.match_score >= 85
                              ? "text-emerald-400"
                              : app.match_score >= 75
                              ? "text-indigo-400"
                              : "text-zinc-400"
                          }`}
                        >
                          {app.match_score}%
                        </span>
                        <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              app.match_score >= 85
                                ? "bg-emerald-400"
                                : app.match_score >= 75
                                ? "bg-indigo-400"
                                : "bg-zinc-500"
                            }`}
                            style={{ width: `${app.match_score}%` }}
                          />
                        </div>
                      </div>
                    </td>

                    {/* ATS Source */}
                    <td className="py-3.5 px-4">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-mono uppercase bg-zinc-950 text-zinc-300 border border-zinc-800">
                        {job?.source || "ats"}
                      </span>
                    </td>

                    {/* Status Badge */}
                    <td className="py-3.5 px-4">
                      {renderStatusBadge(app.status)}
                    </td>

                    {/* Timestamp */}
                    <td className="py-3.5 px-4 font-mono text-[11px] text-zinc-400">
                      {formatDeterministicTimestamp(app.applied_at)}
                    </td>

                    {/* Action Modals */}
                    <td className="py-3.5 px-5 text-right">
                      <div className="inline-flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => setAnswersModalApp(app)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-zinc-950 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition"
                        >
                          <FileCode2 className="w-3.5 h-3.5 text-indigo-400" />
                          Answers
                        </button>
                        <button
                          type="button"
                          onClick={() => setProofModalApp(app)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-zinc-950 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition"
                        >
                          <ImageIcon className="w-3.5 h-3.5 text-emerald-400" />
                          Proof
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Modal 1: View Submitted Answers JSON Audit Log */}
      {answersModalApp && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl max-w-xl w-full p-6 shadow-2xl">
            <div className="flex items-center justify-between pb-3 border-b border-zinc-800">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100">
                  DOM Form Mapping Audit Log
                </h3>
                <p className="text-xs text-zinc-400">
                  {answersModalApp.jobs_discovered?.company_name} —{" "}
                  {answersModalApp.jobs_discovered?.job_title}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setAnswersModalApp(null)}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="mt-4 max-h-80 overflow-y-auto space-y-2">
              {Object.keys(answersModalApp.submitted_answers_json || {})
                .length === 0 ? (
                <p className="text-xs text-zinc-400 py-6 text-center">
                  No DOM fields have been submitted yet (current status:{" "}
                  <code className="text-indigo-300">
                    {answersModalApp.status}
                  </code>
                  ).
                </p>
              ) : (
                Object.entries(answersModalApp.submitted_answers_json).map(
                  ([selector, value]) => (
                    <div
                      key={selector}
                      className="flex items-center justify-between gap-4 bg-zinc-950 border border-zinc-800/80 rounded-xl px-3.5 py-2.5 text-xs"
                    >
                      <code className="text-indigo-400 font-mono">
                        {selector}
                      </code>
                      <span className="text-zinc-200 font-medium text-right break-all">
                        {String(value)}
                      </span>
                    </div>
                  )
                )
              )}
            </div>
          </div>
        </div>
      )}

      {/* Modal 2: View Confirmation Proof Screenshot */}
      {proofModalApp && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl max-w-2xl w-full p-6 shadow-2xl">
            <div className="flex items-center justify-between pb-3 border-b border-zinc-800">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100">
                  Playwright Submission Proof Screenshot
                </h3>
                <p className="text-xs text-zinc-400">
                  {proofModalApp.jobs_discovered?.company_name} —{" "}
                  {proofModalApp.jobs_discovered?.job_title}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setProofModalApp(null)}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="mt-4 bg-zinc-950 border border-zinc-800 rounded-xl p-5 text-center">
              {proofModalApp.proof_screenshot_url ? (
                <div className="space-y-4">
                  <div className="text-xs text-emerald-400 font-mono break-all bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2">
                    Bucket Path: {proofModalApp.proof_screenshot_url}
                  </div>
                  {proofModalApp.proof_screenshot_url.startsWith("http") ? (
                    <img
                      src={proofModalApp.proof_screenshot_url}
                      alt="Submission Proof"
                      className="max-h-96 mx-auto rounded-lg border border-zinc-800 object-contain"
                    />
                  ) : (
                    <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-8">
                      <CheckCircle2 className="w-10 h-10 text-emerald-400 mx-auto mb-2" />
                      <p className="text-sm font-semibold text-zinc-100">
                        Verified Confirmation Screenshot Recorded
                      </p>
                      <p className="text-xs text-zinc-400 mt-1">
                        Full-page PNG stored in Supabase Storage at{" "}
                        {proofModalApp.applied_at
                          ? formatDeterministicTimestamp(
                              proofModalApp.applied_at
                            )
                          : "submission time"}
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-xs text-zinc-400 py-8">
                  No confirmation screenshot captured yet (status:{" "}
                  <code className="text-indigo-300">{proofModalApp.status}</code>
                  ).
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
