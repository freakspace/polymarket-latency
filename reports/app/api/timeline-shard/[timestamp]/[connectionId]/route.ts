import { promises as fs } from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";

const RECORDINGS_ROOT = path.resolve(process.cwd(), "..", "recordings", "ws-bench");

export async function GET(
  _request: Request,
  context: { params: Promise<{ timestamp: string; connectionId: string }> },
): Promise<Response> {
  const { timestamp, connectionId } = await context.params;
  if (!/^[A-Za-z0-9_-]+$/.test(timestamp)) {
    return NextResponse.json({ error: "invalid timestamp" }, { status: 400 });
  }
  if (!/^[A-Za-z0-9_]+$/.test(connectionId)) {
    return NextResponse.json({ error: "invalid connectionId" }, { status: 400 });
  }
  const file = path.join(
    RECORDINGS_ROOT,
    timestamp,
    "timeline",
    "conn",
    `${connectionId}.10s.json`,
  );
  try {
    const raw = await fs.readFile(file, "utf8");
    return new Response(raw, {
      headers: {
        "content-type": "application/json",
        "cache-control": "private, max-age=300",
      },
    });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return NextResponse.json({ error: "shard not found" }, { status: 404 });
    }
    throw err;
  }
}

export const dynamic = "force-dynamic";
