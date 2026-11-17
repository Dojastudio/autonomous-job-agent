"use client";
import { apiFetch } from "@/lib/api";

import React, { useState } from "react";
import {
  UploadCloud,
  FileText,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Send,
  MapPin,
  Briefcase,
  Lock,
  ChevronDown,
  ChevronUp,
  Edit3,
} from "lucide-react";
import { CandidateProfile, QABankItem } from "@/lib/supabase";

interface OnboardingCardProps {
  userId: string;
  profile: CandidateProfile | null;
  qaBank: QABankItem[];
  onUploadSuccess: (data: {
    profile: Partial<CandidateProfile>;
    gap_questions: QABankItem[];
  }) => void;
  onSaveAnswers: (
    answers: Array<{ question_key: string; answer_value: string }>,
  ) => Promise<void>;
}

export default function OnboardingCard({
  userId,
  profile,
  qaBank,
  onUploadSuccess,
  onSaveAnswers,
}: OnboardingCardProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isSavingQA, setIsSavingQA] = useState(false);
  const [showAnsweredQA, setShowAnsweredQA] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [draftAnswers, setDraftAnswers] = useState<Record<string, string>>({});

  const pendingQuestions = qaBank.filter((q) => !q.is_answered);
  const answeredQuestions = qaBank.filter((q) => q.is_answered);
  const parsedCv = profile?.parsed_cv_json;

  const handleFileUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadError("Please upload a valid PDF resume (.pdf).");
      return;
    }
    setUploadError(null);
    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append("user_id", userId);
      formData.append("file", file);

      const res = await apiFetch("/api/cv", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || data.error || "Failed to parse CV");
      }

      onUploadSuccess({
        profile: {
          id: userId,
          full_name: data.full_name,
          email: data.email,
          phone: data.phone,
          location: data.location,
          parsed_cv_json: data.parsed_cv_json,
        },
        gap_questions: data.gap_questions || [],
      });
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setIsUploading(false);
    }
  };

  const handleSubmitPendingAnswers = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload = Object.entries(draftAnswers)
      .filter(([, val]) => val.trim().length > 0)
      .map(([question_key, answer_value]) => ({ question_key, answer_value }));

    if (payload.length === 0) return;

    setIsSavingQA(true);
    try {
      await onSaveAnswers(payload);
      setDraftAnswers({});
      setEditingKey(null);
    } catch {
      // Parent displays the save failure; preserve the draft for retry.
    } finally {
      setIsSavingQA(false);
    }
  };

  return (
    <section className="grid grid-cols-1 lg:grid-cols-12 gap-5">
      {/* Left Card: Resume Profile & Extracted Skills */}
      <div className="lg:col-span-5 rounded-2xl bg-zinc-900/70 border border-zinc-800/80 backdrop-blur-md p-5 flex flex-col justify-between shadow-xl">
        <div>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
                <FileText className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-zinc-100">
                  Your CV and profile
                </h2>
                <p className="text-[11px] text-zinc-400 flex items-center gap-1">
                  <Lock className="w-3 h-3 text-emerald-400" />
                  Review extracted details before applying
                </p>
              </div>
            </div>

            {profile?.full_name && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                <CheckCircle2 className="w-3 h-3" />
                CV saved
              </span>
            )}
          </div>

          {/* Compact Dropzone */}
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file) handleFileUpload(file);
            }}
            className={`group flex items-center justify-between gap-4 border border-dashed rounded-xl px-4 py-3.5 cursor-pointer transition-all ${
              isDragging
                ? "border-indigo-400 bg-indigo-500/10"
                : "border-zinc-700/80 hover:border-indigo-500/60 bg-zinc-950/60 hover:bg-zinc-950"
            }`}
          >
            <input
              type="file"
              accept="application/pdf"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFileUpload(file);
              }}
            />
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-zinc-900 border border-zinc-800 group-hover:border-indigo-500/40 text-indigo-400 transition">
                {isUploading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <UploadCloud className="w-4 h-4" />
                )}
              </div>
              <div>
                <p className="text-xs font-medium text-zinc-200">
                  {isUploading
                    ? "Extracting PDF & Generating Gap Questions..."
                    : "Drop PDF Resume or click to replace"}
                </p>
                <p className="text-[11px] text-zinc-500">
                  Upload a text-based PDF, up to 10 MB
                </p>
              </div>
            </div>
            <span className="shrink-0 px-2.5 py-1 rounded-lg text-[11px] font-medium bg-zinc-800 text-zinc-300 group-hover:bg-indigo-600 group-hover:text-white transition">
              Upload PDF
            </span>
          </label>

          {uploadError && (
            <p className="mt-2.5 text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-1.5">
              {uploadError}
            </p>
          )}

          {/* Candidate Summary & Skills */}
          {parsedCv && (
            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-3 gap-2 bg-zinc-950/70 border border-zinc-800/80 rounded-xl p-2.5 text-xs">
                <div className="px-2">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500 block">
                    Candidate
                  </span>
                  <span className="font-semibold text-zinc-200 truncate block mt-0.5">
                    {profile?.full_name || "Anonymized"}
                  </span>
                </div>
                <div className="px-2 border-x border-zinc-800/80">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500 flex items-center gap-1">
                    <Briefcase className="w-2.5 h-2.5" /> Experience
                  </span>
                  <span className="font-semibold text-indigo-400 block mt-0.5">
                    {parsedCv.experience_years || 0} Years
                  </span>
                </div>
                <div className="px-2">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500 flex items-center gap-1">
                    <MapPin className="w-2.5 h-2.5" /> Location
                  </span>
                  <span className="font-semibold text-zinc-200 truncate block mt-0.5">
                    {profile?.location || "Remote"}
                  </span>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between text-[11px] text-zinc-400 mb-1.5">
                  <span className="font-medium">Skills</span>
                  <span className="font-mono text-zinc-500">
                    {parsedCv.skills?.length || 0} skills
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto">
                  {(parsedCv.skills || []).map((skill) => (
                    <span
                      key={skill}
                      className="px-2 py-0.5 rounded-md text-[11px] font-medium bg-zinc-800/80 text-zinc-300 border border-zinc-700/60"
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right Card: Dynamic Screening Q&A Memory Bank */}
      <div className="lg:col-span-7 rounded-2xl bg-zinc-900/70 border border-zinc-800/80 backdrop-blur-md p-5 flex flex-col justify-between shadow-xl">
        <div>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2.5">
              <div
                className={`w-8 h-8 rounded-lg flex items-center justify-center border ${
                  pendingQuestions.length > 0
                    ? "bg-amber-500/10 border-amber-500/25 text-amber-400"
                    : "bg-emerald-500/10 border-emerald-500/25 text-emerald-400"
                }`}
              >
                {pendingQuestions.length > 0 ? (
                  <AlertTriangle className="w-4 h-4" />
                ) : (
                  <CheckCircle2 className="w-4 h-4" />
                )}
              </div>
              <div>
                <h2 className="text-sm font-semibold text-zinc-100">
                  Screening questions
                </h2>
                <p className="text-[11px] text-zinc-400">
                  {pendingQuestions.length > 0
                    ? `${pendingQuestions.length} required answer(s) missing — applications pause when an answer is needed`
                    : "No unanswered screening questions"}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <span
                className={`px-2.5 py-0.5 rounded-md text-[11px] font-mono font-semibold border ${
                  pendingQuestions.length > 0
                    ? "bg-amber-500/10 text-amber-300 border-amber-500/25"
                    : "bg-emerald-500/10 text-emerald-300 border-emerald-500/25"
                }`}
              >
                {answeredQuestions.length}/{qaBank.length} Saved
              </span>
            </div>
          </div>

          {/* Pending Unanswered Questions Form */}
          {pendingQuestions.length > 0 && (
            <form onSubmit={handleSubmitPendingAnswers} className="space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {pendingQuestions.map((q) => (
                  <div
                    key={q.question_key}
                    className="bg-zinc-950/80 border border-amber-500/25 rounded-xl p-3 focus-within:border-amber-400/60 transition"
                  >
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className="text-[10px] font-mono uppercase tracking-wider text-amber-400/90">
                        {q.question_key.replace(/_/g, " ")}
                      </span>
                      <span className="text-[10px] px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-300">
                        Required
                      </span>
                    </div>
                    <label className="block text-xs text-zinc-200 mb-2 leading-snug">
                      {q.question_label}
                    </label>
                    <input
                      type="text"
                      placeholder="Type verified answer..."
                      value={draftAnswers[q.question_key] ?? ""}
                      onChange={(e) =>
                        setDraftAnswers((prev) => ({
                          ...prev,
                          [q.question_key]: e.target.value,
                        }))
                      }
                      className="w-full px-3 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-amber-400"
                    />
                  </div>
                ))}
              </div>

              <div className="flex items-center justify-between pt-1">
                <span className="text-[11px] text-zinc-400">
                  Saving automatically re-queues any{" "}
                  <span className="text-amber-300 font-medium">
                    Needs Input
                  </span>{" "}
                  applications.
                </span>
                <button
                  type="submit"
                  disabled={isSavingQA}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold bg-amber-400 hover:bg-amber-300 text-zinc-950 transition shadow-lg shadow-amber-500/10 disabled:opacity-50"
                >
                  {isSavingQA ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Send className="w-3.5 h-3.5" />
                  )}
                  Save Saved Answers
                </button>
              </div>
            </form>
          )}

          {/* Answered Questions Section (Collapsible when pending exist, or always visible when all answered) */}
          {answeredQuestions.length > 0 && (
            <div
              className={
                pendingQuestions.length > 0
                  ? "mt-4 pt-3 border-t border-zinc-800/70"
                  : ""
              }
            >
              {pendingQuestions.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAnsweredQA(!showAnsweredQA)}
                  className="flex items-center justify-between w-full text-left text-[11px] font-medium text-zinc-400 hover:text-zinc-200 transition"
                >
                  <span>
                    View {answeredQuestions.length} verified screening answer(s)
                  </span>
                  {showAnsweredQA ? (
                    <ChevronUp className="w-3.5 h-3.5" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5" />
                  )}
                </button>
              )}

              {(pendingQuestions.length === 0 || showAnsweredQA) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 mt-2.5">
                  {answeredQuestions.map((q) => {
                    const isEditing = editingKey === q.question_key;
                    return (
                      <div
                        key={q.question_key}
                        className="bg-zinc-950/60 border border-zinc-800/80 rounded-xl px-3.5 py-2.5 flex flex-col justify-between"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="text-[11px] text-zinc-400 leading-snug line-clamp-2">
                            {q.question_label}
                          </span>
                          <button
                            type="button"
                            onClick={() => {
                              if (isEditing) {
                                setEditingKey(null);
                              } else {
                                setEditingKey(q.question_key);
                                setDraftAnswers((prev) => ({
                                  ...prev,
                                  [q.question_key]: q.answer_value,
                                }));
                              }
                            }}
                            className="text-zinc-500 hover:text-zinc-300 shrink-0"
                            title="Edit answer"
                          >
                            <Edit3 className="w-3 h-3" />
                          </button>
                        </div>

                        {isEditing ? (
                          <div className="mt-2 flex items-center gap-1.5">
                            <input
                              type="text"
                              value={draftAnswers[q.question_key] ?? ""}
                              onChange={(e) =>
                                setDraftAnswers((prev) => ({
                                  ...prev,
                                  [q.question_key]: e.target.value,
                                }))
                              }
                              className="flex-1 px-2.5 py-1 rounded-lg bg-zinc-900 border border-zinc-700 text-xs text-zinc-100 focus:outline-none focus:border-indigo-500"
                            />
                            <button
                              type="button"
                              onClick={async () => {
                                const val =
                                  draftAnswers[q.question_key]?.trim();
                                if (val) {
                                  await onSaveAnswers([
                                    {
                                      question_key: q.question_key,
                                      answer_value: val,
                                    },
                                  ]);
                                }
                                setEditingKey(null);
                              }}
                              className="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-indigo-600 text-white hover:bg-indigo-500"
                            >
                              Save
                            </button>
                          </div>
                        ) : (
                          <div className="mt-1.5 flex items-center gap-1.5">
                            <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" />
                            <span className="text-xs font-semibold text-zinc-100 truncate">
                              {q.answer_value}
                            </span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
