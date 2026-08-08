import { Shell } from "@/components/Shell";
import { MapView } from "@/components/MapView";
import { EmptyState } from "@/components/primitives";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function MapPage() {
  try {
    const coverage = await api.coverage();
    return (
      <Shell active="/">
        <MapView coverage={coverage} />
      </Shell>
    );
  } catch (error) {
    return (
      <Shell active="/">
        <div className="p-8">
          <EmptyState
            title="The layer is not answering."
            detail={`${error instanceof Error ? error.message : String(error)} — start the API with \`make dev\` and build the lake with \`make seed\`.`}
          />
        </div>
      </Shell>
    );
  }
}
