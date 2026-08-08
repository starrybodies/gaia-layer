import { Shell } from "@/components/Shell";
import { MapView } from "@/components/MapView";
import { EmptyState } from "@/components/primitives";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function MapPage() {
  try {
    const coverage = await api.coverage();
    return (
      <Shell active="/map" flush>
        <MapView coverage={coverage} />
      </Shell>
    );
  } catch (error) {
    return (
      <Shell active="/map">
        <div className="mx-auto max-w-3xl px-5 py-24">
          <EmptyState
            title="The layer is not answering."
            detail={`${error instanceof Error ? error.message : String(error)} — the API and the console are the same deployment, so this usually means the data lake failed to open.`}
          />
        </div>
      </Shell>
    );
  }
}
