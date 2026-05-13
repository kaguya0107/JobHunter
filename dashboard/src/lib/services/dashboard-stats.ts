import { DiscordDeliveryStatus } from "@prisma/client";

import { db } from "@/lib/db";

export async function getDashboardStatsBundle() {
  const startUtcDay = new Date();
  startUtcDay.setUTCHours(0, 0, 0, 0);

  const weekAgo = new Date(Date.now() - 7 * 86400_000);

  const [
    activeSources,
    jobsToday,
    discordSentToday,
    totalJobs,
    scrapeWeek,
    latencyRows,
    recentRuns,
    failedRecently,
  ] = await Promise.all([
    db.monitoringSource.count({ where: { active: true } }),
    db.detectedJob.count({ where: { detectedAt: { gte: startUtcDay } } }),
    db.discordNotification.count({
      where: { status: DiscordDeliveryStatus.SENT, sentAt: { gte: startUtcDay } },
    }),
    db.detectedJob.count(),
    db.scrapeHistory.findMany({
      where: { startedAt: { gte: weekAgo } },
      select: { success: true },
    }),
    db.scrapeHistory.findMany({
      where: { finishedAt: { not: null }, startedAt: { gte: weekAgo } },
      select: { startedAt: true, finishedAt: true },
      take: 250,
      orderBy: { startedAt: "desc" },
    }),
    db.scrapeHistory.findMany({
      take: 14,
      orderBy: { startedAt: "desc" },
      include: { source: { select: { platform: true, url: true } } },
    }),
    db.scrapeHistory.count({
      where: { success: false, startedAt: { gte: weekAgo } },
    }),
  ]);

  const totalScrapesWeek = Math.max(scrapeWeek.length, 1);
  const failedScrapesWeek = scrapeWeek.filter((s) => !s.success).length;
  const errorRate = failedScrapesWeek / totalScrapesWeek;

  const durSec = latencyRows
    .map((r) => (r.finishedAt!.getTime() - r.startedAt.getTime()) / 1000)
    .filter((n) => n > 0 && n < 600);
  const avgLatencySec = durSec.length ? durSec.reduce((a, b) => a + b, 0) / durSec.length : null;

  const jobsPerDay: { day: string; count: number }[] = [];

  for (let idx = 6; idx >= 0; idx--) {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - idx);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    const count = await db.detectedJob.count({
      where: { detectedAt: { gte: start, lt: end } },
    });
    jobsPerDay.push({
      day: `${start.getMonth() + 1}/${start.getDate()}`,
      count,
    });
  }

  const sliceWeek = scrapeWeek.slice(-48);
  const scrapeSpark = sliceWeek.map((s, tick) => ({
    tick,
    success: s.success ? 1 : 0,
  }));

  const recentActivity = recentRuns.map((h) => ({
    id: h.id,
    platform: h.source.platform,
    success: h.success,
    jobsFound: h.jobsFound,
    startedAt: h.startedAt.toISOString(),
    errorMessage: h.errorMessage,
    urlSlice: h.source.url.slice(0, 88),
  }));

  return {
    activeSources,
    jobsToday,
    discordSentToday,
    totalJobs,
    errorRate,
    avgLatencySec,
    backlogHint: Math.min(failedRecently, 50),
    recentActivity,
    jobsPerDay,
    scrapeSpark,
    generatedAt: new Date().toISOString(),
  };
}
