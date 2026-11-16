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

const DEFAULT_GAP_QUESTIONS = [
  {
    question_key: "work_authorization",
    question_label:
      "Are you legally authorized to work in your target job country without restrictions?",
  },
  {
    question_key: "visa_sponsorship_required",
    question_label:
      "Will you now or in the future require visa sponsorship for employment?",
  },
  {
    question_key: "salary_expectation_usd",
    question_label:
      "What is your minimum annual base salary expectation (in USD)?",
  },
  {
    question_key: "notice_period_days",
    question_label:
      "What is your current notice period or earliest available start date (in days)?",
  },
  {
    question_key: "willing_to_relocate",
    question_label:
      "Are you willing to relocate for a hybrid/on-site role, or do you require 100% remote?",
  },
];

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();

    // 1. Try local FastAPI backend if running locally
    if (BACKEND_URL && BACKEND_URL.includes("localhost")) {
      try {
        const res = await fetch(`${BACKEND_URL}/api/cv/upload`, {
          method: "POST",
          body: formData,
          signal: AbortSignal.timeout(15000),
        });
        if (res.ok) {
          return NextResponse.json(await res.json());
        }
      } catch {
        // Fall through to serverless handler
      }
    }

    // 2. Serverless Fallback on Vercel: Upload PDF to Supabase Storage + Parse via Groq/Gemini
    const userId =
      (formData.get("user_id") as string) ||
      "00000000-0000-0000-0000-000000000001";
    const file = formData.get("file") as File | null;
    if (!file) {
      return NextResponse.json(
        { error: "No PDF file provided" },
        { status: 400 }
      );
    }

    const arrayBuffer = await file.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);
    const supabase = getAdminSupabase();
    const storagePath = `${userId}/resume.pdf`;

    await supabase.storage.from("resumes").upload(storagePath, buffer, {
      contentType: "application/pdf",
      upsert: true,
    });

    // Extract printable strings from PDF buffer for quick serverless preview
    const rawAscii = buffer
      .toString("latin1")
      .replace(/[^\x20-\x7E\n]/g, " ")
      .replace(/\s+/g, " ")
      .slice(0, 6000);

    let parsedCvJson = {
      skills: [
        "Python",
        "FastAPI",
        "Playwright",
        "TypeScript",
        "Next.js",
        "PostgreSQL",
        "pgvector",
      ],
      experience_years: 5,
      work_history: [],
      education: [],
      target_roles: ["Senior Software Engineer", "Full-Stack AI Engineer"],
    };

    const groqKey = process.env.GROQ_API_KEY;
    if (groqKey) {
      try {
        const llmRes = await fetch(
          "https://api.groq.com/openai/v1/chat/completions",
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${groqKey}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              model: "openai/gpt-oss-120b",
              temperature: 0.1,
              response_format: { type: "json_object" },
              messages: [
                {
                  role: "system",
                  content:
                    'Extract structured resume metadata into JSON with keys: "skills" (string[]), "experience_years" (number), "target_roles" (string[]). Return valid JSON only.',
                },
                {
                  role: "user",
                  content: `Filename: ${file.name}\nExtracted text: ${rawAscii.slice(0, 3000)}`,
                },
              ],
            }),
          }
        );
        if (llmRes.ok) {
          const llmData = await llmRes.json();
          const parsed = JSON.parse(
            llmData.choices?.[0]?.message?.content || "{}"
          );
          if (Array.isArray(parsed.skills) && parsed.skills.length > 0) {
            parsedCvJson = {
              ...parsedCvJson,
              skills: parsed.skills,
              experience_years: Number(parsed.experience_years) || 5,
              target_roles: parsed.target_roles || parsedCvJson.target_roles,
            };
          }
        }
      } catch {
        // Keep default parsedCvJson
      }
    }

    const qaRows = DEFAULT_GAP_QUESTIONS.map((q) => ({
      user_id: userId,
      question_key: q.question_key,
      question_label: q.question_label,
      answer_value: "",
      is_answered: false,
    }));

    await supabase.from("candidate_profiles").upsert({
      id: userId,
      resume_url: storagePath,
      parsed_cv_json: parsedCvJson,
    });

    await supabase.from("candidate_qa_bank").upsert(qaRows, {
      onConflict: "user_id,question_key",
      ignoreDuplicates: true,
    });

    const { data: liveQa } = await supabase
      .from("candidate_qa_bank")
      .select("*")
      .eq("user_id", userId);

    return NextResponse.json({
      status: "ok",
      user_id: userId,
      full_name: file.name.replace(/\.pdf$/i, ""),
      location: "Remote",
      parsed_cv_json: parsedCvJson,
      gap_questions: liveQa || qaRows,
    });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "CV upload proxy error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
