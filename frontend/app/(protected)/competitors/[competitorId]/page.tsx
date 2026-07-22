import { CompetitorIntelligence } from "@/app/_components/competitor-intelligence";

export default async function CompetitorDetailPage({
  params,
}: {
  params: Promise<{ competitorId: string }>;
}) {
  const { competitorId } = await params;
  return <CompetitorIntelligence competitorId={competitorId} />;
}
