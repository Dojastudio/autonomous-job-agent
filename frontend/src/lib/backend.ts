import { NextRequest, NextResponse } from "next/server";

export async function proxy(req: NextRequest, path: string) {
  const authorization = req.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) {
    return NextResponse.json(
      { detail: "Sign in to continue." },
      { status: 401 },
    );
  }
  const base = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  try {
    const headers: Record<string, string> = { authorization };
    const contentType = req.headers.get("content-type");
    if (contentType) headers["content-type"] = contentType;
    const response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
      method: req.method,
      headers,
      cache: "no-store",
      body: req.method === "GET" ? undefined : await req.arrayBuffer(),
      signal: AbortSignal.timeout(path.includes("/cv/") ? 120000 : 15000),
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "The processing service is unavailable. Please retry later." },
      { status: 503 },
    );
  }
}
