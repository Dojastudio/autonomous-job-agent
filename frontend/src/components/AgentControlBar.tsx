"use client";

import React, { useState, useEffect } from "react";
import {
  Play,
  Plus,
  X,
  Loader2,
  Sparkles,
  SlidersHorizontal,
  Gauge,
  MapPin,
} from "lucide-react";

interface AgentControlBarProps {
  autoApplyEnabled: boolean;
  minMatchScore: number;
  dailyApplyLimit: number;
  targetRoles: string[];
  targetLocations: string[];
  isRunningCycle: boolean;
  onUpdateSettings: (patch: {
    auto_apply_enabled?: boolean;
    min_match_score?: number;
    daily_apply_limit?: number;
    target_roles?: string[];
    target_locations?: string[];
  }) => Promise<void>;
  onTriggerRunNow: () => Promise<void>;
}

const QUICK_LOCATION_PRESETS = [
  "Lagos, Nigeria",
  "Abuja, Nigeria",
  "Remote (Nigeria)",
  "Remote (EMEA)",
  "Remote (Worldwide)",
];

export default function AgentControlBar({
  autoApplyEnabled,
  minMatchScore,
  dailyApplyLimit,
  targetRoles,
  targetLocations,
  isRunningCycle,
  onUpdateSettings,
  onTriggerRunNow,
}: AgentControlBarProps) {
  const [newRoleInput, setNewRoleInput] = useState("");
  const [newLocationInput, setNewLocationInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [thresholdDraft, setThresholdDraft] = useState(minMatchScore);
  const [limitDraft, setLimitDraft] = useState(dailyApplyLimit);
  useEffect(() => setThresholdDraft(minMatchScore), [minMatchScore]);
  useEffect(() => setLimitDraft(dailyApplyLimit), [dailyApplyLimit]);
  const save = async (patch: Parameters<typeof onUpdateSettings>[0]) => {
    setSaving(true);
    try {
      await onUpdateSettings(patch);
      return true;
    } catch {
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleAddRole = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = newRoleInput.trim();
    if (!trimmed || targetRoles.includes(trimmed)) return;
    const updated = [...targetRoles, trimmed];
    if (await save({ target_roles: updated })) setNewRoleInput("");
  };

  const handleRemoveRole = async (roleToRemove: string) => {
    const updated = targetRoles.filter((r) => r !== roleToRemove);
    await save({ target_roles: updated });
  };

  const handleAddLocation = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = newLocationInput.trim();
    if (!trimmed || targetLocations.includes(trimmed)) return;
    const updated = [...targetLocations, trimmed];
    if (await save({ target_locations: updated })) setNewLocationInput("");
  };

  const handleTogglePresetLocation = async (loc: string) => {
    const exists = targetLocations.includes(loc);
    const updated = exists
      ? targetLocations.filter((l) => l !== loc)
      : [...targetLocations, loc];
    await save({ target_locations: updated });
  };

  const handleRemoveLocation = async (locToRemove: string) => {
    const updated = targetLocations.filter((l) => l !== locToRemove);
    await save({ target_locations: updated });
  };

  return (
    <fieldset
      disabled={saving}
      className="rounded-2xl bg-zinc-900/70 border border-zinc-800/80 backdrop-blur-md p-5 shadow-2xl space-y-4"
    >
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-5">
        {/* Left: Autonomous Engine Status & Switch */}
        <div className="flex items-center gap-4">
          <button
            type="button"
            role="switch"
            aria-checked={autoApplyEnabled}
            onClick={() => save({ auto_apply_enabled: !autoApplyEnabled })}
            className={`group relative inline-flex items-center gap-3 px-4 py-2.5 rounded-xl border text-xs font-semibold transition-all duration-200 ${
              autoApplyEnabled
                ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300 shadow-[0_0_25px_-5px_rgba(16,185,129,0.25)]"
                : "bg-zinc-900 border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200"
            }`}
          >
            <span className="relative flex h-2.5 w-2.5">
              {autoApplyEnabled && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              )}
              <span
                className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
                  autoApplyEnabled ? "bg-emerald-400" : "bg-zinc-600"
                }`}
              />
            </span>
            <span>
              {autoApplyEnabled ? "Auto-Apply Active" : "Auto-Apply Paused"}
            </span>
            <span
              className={`ml-1 px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${
                autoApplyEnabled
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-zinc-800 text-zinc-500"
              }`}
            >
              {autoApplyEnabled ? "ON" : "OFF"}
            </span>
          </button>

          <div className="hidden xl:flex flex-col">
            <span className="text-xs font-medium text-zinc-200">
              Your search preferences
            </span>
            <span className="text-[11px] text-zinc-500">
              Matches roles against your saved preferences
            </span>
          </div>
        </div>

        {/* Center/Right: Threshold Controls & Primary Trigger */}
        <div className="flex flex-wrap items-center gap-4 sm:gap-6">
          {/* Min Match Score Slider */}
          <div className="flex items-center gap-3 bg-zinc-950/80 border border-zinc-800/80 rounded-xl px-3.5 py-2">
            <SlidersHorizontal className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
            <div className="flex flex-col w-36">
              <div className="flex items-center justify-between text-[11px] mb-1">
                <span className="text-zinc-400 font-medium">
                  Match Threshold
                </span>
                <span className="font-mono font-semibold text-indigo-400">
                  &ge;{thresholdDraft}%
                </span>
              </div>
              <input
                type="range"
                min={50}
                max={95}
                step={5}
                value={thresholdDraft}
                onChange={(e) => setThresholdDraft(Number(e.target.value))}
                className="w-full h-1.5 bg-zinc-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
              />
            </div>
          </div>

          {/* Daily Apply Cap */}
          <div className="flex items-center gap-2.5 bg-zinc-950/80 border border-zinc-800/80 rounded-xl px-3.5 py-2">
            <Gauge className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
            <label className="text-[11px] font-medium text-zinc-400">
              Daily attempt limit
            </label>
            <input
              type="number"
              min={1}
              max={200}
              value={limitDraft}
              onChange={(e) =>
                setLimitDraft(
                  Math.max(1, Math.min(200, Number(e.target.value) || 1)),
                )
              }
              className="w-12 bg-zinc-900 border border-zinc-700/80 rounded-lg px-2 py-0.5 text-xs font-mono font-semibold text-zinc-100 text-center focus:outline-none focus:border-indigo-500"
            />
          </div>

          <button
            type="button"
            className="text-xs text-indigo-300 disabled:opacity-40"
            disabled={
              thresholdDraft === minMatchScore && limitDraft === dailyApplyLimit
            }
            onClick={async () => {
              if (
                !(await save({
                  min_match_score: thresholdDraft,
                  daily_apply_limit: limitDraft,
                }))
              ) {
                setThresholdDraft(minMatchScore);
                setLimitDraft(dailyApplyLimit);
              }
            }}
          >
            {saving ? "Saving…" : "Save limits"}
          </button>
          {/* Manual Trigger CTA */}
          <button
            type="button"
            disabled={isRunningCycle}
            onClick={onTriggerRunNow}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs font-semibold bg-white text-zinc-950 hover:bg-zinc-200 active:scale-[0.99] transition-all shadow-lg shadow-white/5 disabled:opacity-50"
          >
            {isRunningCycle ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-600" />
                <span>Search in progress…</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>Start job search</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Row 2: Target Roles */}
      <div className="pt-3.5 border-t border-zinc-800/70 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-zinc-400 mr-1.5">
            <Sparkles className="w-3 h-3 text-indigo-400" />
            Target Roles:
          </span>
          {targetRoles.length === 0 ? (
            <span className="text-xs text-zinc-500 italic">
              Add the roles you want to find
            </span>
          ) : (
            targetRoles.map((role) => (
              <span
                key={role}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium bg-zinc-800/90 text-zinc-200 border border-zinc-700/70 hover:border-zinc-600 transition"
              >
                {role}
                <button
                  type="button"
                  onClick={() => handleRemoveRole(role)}
                  className="text-zinc-500 hover:text-zinc-200 transition"
                  aria-label={`Remove ${role}`}
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))
          )}
        </div>

        <form onSubmit={handleAddRole} className="flex items-center gap-1.5">
          <input
            type="text"
            placeholder="Add target role..."
            value={newRoleInput}
            onChange={(e) => setNewRoleInput(e.target.value)}
            className="w-44 px-3 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition"
          />
          <button
            type="submit"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700/80 transition"
          >
            <Plus className="w-3.5 h-3.5" />
            Add Role
          </button>
        </form>
      </div>

      {/* Row 3: Target Locations (Lagos, Abuja, Nigeria, EMEA, Worldwide) */}
      <div className="pt-3 border-t border-zinc-800/70 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-zinc-400 mr-1.5">
            <MapPin className="w-3 h-3 text-emerald-400" />
            Target Locations:
          </span>

          {/* Active Location Tags */}
          {targetLocations.map((loc) => (
            <span
              key={loc}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
            >
              {loc}
              <button
                type="button"
                onClick={() => handleRemoveLocation(loc)}
                className="text-emerald-400/70 hover:text-emerald-200 transition"
                aria-label={`Remove ${loc}`}
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}

          {/* Quick-Toggle Presets that aren't currently active */}
          {QUICK_LOCATION_PRESETS.filter(
            (preset) => !targetLocations.includes(preset),
          ).map((preset) => (
            <button
              key={preset}
              type="button"
              onClick={() => handleTogglePresetLocation(preset)}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium bg-zinc-950 text-zinc-500 border border-zinc-800 hover:border-zinc-700 hover:text-zinc-300 transition"
              title={`Click to add ${preset}`}
            >
              <Plus className="w-2.5 h-2.5" />
              {preset}
            </button>
          ))}
        </div>

        <form
          onSubmit={handleAddLocation}
          className="flex items-center gap-1.5"
        >
          <input
            type="text"
            placeholder="Add city or country (e.g. Abuja)..."
            value={newLocationInput}
            onChange={(e) => setNewLocationInput(e.target.value)}
            className="w-48 px-3 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-emerald-500 transition"
          />
          <button
            type="submit"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700/80 transition"
          >
            <Plus className="w-3.5 h-3.5" />
            Add Location
          </button>
        </form>
      </div>
    </fieldset>
  );
}
