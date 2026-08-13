/**
 * Surface C — the portfolio.
 *
 * The book is fetched on the server and handed to the client component, so the demo works
 * without a round trip and the synthetic label is present in the first paint rather than
 * arriving a moment later. Everything interactive is in `PortfolioView`.
 */

import { Shell } from "@/components/Shell";
import { Eyebrow, EmptyState } from "@/components/primitives";
import { api, type DemoBook } from "@/lib/api";
import { PortfolioView } from "./PortfolioView";

export const dynamic = "force-dynamic";

async function loadBook(): Promise<DemoBook | null> {
  try {
    return await api.demoBook();
  } catch {
    return null;
  }
}

export default async function PortfolioPage() {
  const book = await loadBook();

  return (
    <Shell active="/portfolio">
      <div className="mx-auto max-w-[100rem] space-y-6 px-5 py-8">
        <div>
          <Eyebrow>Surface C · portfolio</Eyebrow>
          <h1 className="display text-text mt-2 text-2xl">
            A book of cells, ranked and compared
          </h1>
          <p className="text-dim mt-3 max-w-3xl text-sm leading-relaxed">
            A client sends H3 cell identifiers and their own exposure values. Nothing finer
            leaves their systems and nothing finer is accepted here — a resolution-8 cell is
            about 0.74 km², which is the finest thing this layer needs to know about a risk.
            The index values are read from the archive exactly as the pipeline persisted them,
            each carrying the run that produced it; the ranking and the rollups are computed
            once on the server, with an audit row, and rendered here.
          </p>
        </div>

        {book === null ? (
          <EmptyState
            title="The demo book has not been built in this deployment"
            detail="It is written by pipeline/scripts/build_demo_book.py from Overture's open building footprints. Its values are synthetic and it says so; without it there is no book to scan."
          />
        ) : (
          <PortfolioView book={book} />
        )}
      </div>
    </Shell>
  );
}
