import { Prisma } from "@prisma/client";

import { db } from "@/lib/db";
import { ok } from "@/lib/api-response";

export const dynamic = "force-dynamic";

/** Same window as jobs UI “Fresh” badge (last 2 hours). */
const FRESH_WINDOW_MS = 2 * 60 * 60 * 1000;

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const q = searchParams.get("q")?.trim();
  const platform = searchParams.get("platform")?.trim();
  const tag = searchParams.get("tag")?.trim();

  const pageRaw = parseInt(searchParams.get("page") ?? "1", 10);
  const limitRaw = parseInt(searchParams.get("limit") ?? "20", 10);
  const page = Number.isFinite(pageRaw) && pageRaw >= 1 ? pageRaw : 1;
  const limit = Math.min(Math.max(Number.isFinite(limitRaw) && limitRaw >= 1 ? limitRaw : 20, 1), 100);

  const where: Prisma.DetectedJobWhereInput = {};
  if (q) {
    where.OR = [
      { title: { contains: q, mode: "insensitive" } },
      { clientName: { contains: q, mode: "insensitive" } },
      { budget: { contains: q, mode: "insensitive" } },
    ];
  }
  if (platform) {
    where.source = { platform: { equals: platform, mode: "insensitive" } };
  }
  if (tag) {
    where.tags = { has: tag };
  }

  const sort = searchParams.get("sort")?.trim();
  const orderBy: Prisma.DetectedJobOrderByWithRelationInput =
    sort === "score"
      ? { aiScore: "desc" }
      : sort === "posted"
        ? { postedAt: "desc" }
        : { detectedAt: "desc" };

  const freshSince = new Date(Date.now() - FRESH_WINDOW_MS);
  const whereFresh: Prisma.DetectedJobWhereInput = {
    ...where,
    detectedAt: { gte: freshSince },
  };

  const skip = (page - 1) * limit;

  const [total, freshInWindow, rows] = await Promise.all([
    db.detectedJob.count({ where }),
    db.detectedJob.count({ where: whereFresh }),
    db.detectedJob.findMany({
      where,
      include: {
        source: { select: { platform: true, url: true } },
        discordNotifications: { take: 1, orderBy: { sentAt: "desc" } },
        aiAnalysis: true,
      },
      orderBy,
      skip,
      take: limit,
    }),
  ]);

  const mapped = rows.map((job) => ({
    ...job,
    platform: job.source.platform,
    sourceUrl: job.source.url,
    aiScoreNormalized: job.aiAnalysis?.relevanceScore ?? job.aiScore,
    notificationStatus:
      job.discordNotifications[0]?.status ??
      (job.notificationSent ? ("SENT" as const) : ("PENDING" as const)),
  }));

  return ok({
    jobs: mapped,
    total,
    page,
    limit,
    freshInWindow,
    totalPages: Math.max(1, Math.ceil(total / limit) || 1),
  });
}
