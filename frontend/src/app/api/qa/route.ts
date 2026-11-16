import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";
import { BACKEND_URL } from "@/lib/supabase";

function getAdminSupabase() {
  const url =
    process.env.SUPABASE_URL ||
    process.env.NEXT_PUBLIC_SUPABASE_URL ||
    "https://zovizzgqngiasajjzhcw.supabase.co";
  const key =
    process.env.SUPABASE_SERVICE_KEY ||
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
    "";
  return createClient(url, key);
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();

    // 1. Try local FastAPI backend if running locally
    if (BACKEND_URL && BACKEND_URL.includes("localhost")) {
      try {
        const res = await fetch(`${BACKEND_URL}/api/qa/answer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(2500),
        });
        if (res.ok) {
          return NextResponse.json(await res.json());
        }
      } catch {
        // Fall through to direct Supabase update
      }
    }

    // 2. Direct Supabase update (Vercel Serverless)
    const supabase = getAdminSupabase();
    const userId = body.user_id || "00000000-0000-0000-0000-000000000001";
    const answers = Array.isArray(body.answers) ? body.answers : [];

    let updatedCount = 0;
    for (const item of answers) {
      if (!item.answer_value?.trim()) continue;
      await supabase
        .from("candidate_qa_bank")
        .update({
          answer_value: item.answer_value.trim(),
          is_answered: true,
        })
        .eq("user_id", userId)
        .eq("question_key", item.question_key);
      updatedCount += 1;
    }

    // Re-queue applications paused on needs_human_input
    await supabase
      .from("applications")
      .update({ status: "queued", error_log: null })
      .eq("user_id", userId)
      .eq("status", "needs_human_input");

    return NextResponse.json({ status: "ok", updated_answers: updatedCount });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "QA save proxy error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
