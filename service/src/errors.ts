/**
 * Structured errors.
 *
 * Every failure the layer can produce has a stable code. An agent that hits one should be
 * able to branch on it without parsing prose, and a human reading a log should be able to
 * tell whether the problem is theirs or ours.
 */

export type ErrorCode =
  | "aoi_not_ingested"
  | "geometry_unsupported"
  | "no_data_for_period"
  | "claim_not_found"
  | "indicator_unavailable"
  | "lake_unavailable"
  | "invalid_request"
  | "internal";

export class ServiceError extends Error {
  constructor(
    public readonly code: ErrorCode,
    message: string,
    public readonly detail?: string,
    public readonly retryable = false,
  ) {
    super(message);
    this.name = "ServiceError";
  }

  toResponse(): {
    error: ErrorCode;
    message: string;
    detail?: string;
    retryable: boolean;
    generated_at: string;
  } {
    return {
      error: this.code,
      message: this.message,
      ...(this.detail === undefined ? {} : { detail: this.detail }),
      retryable: this.retryable,
      generated_at: new Date().toISOString(),
    };
  }
}

/**
 * The requested geometry has no ingested coverage.
 *
 * This is deliberately an error rather than a best guess from a neighbouring area. Serving
 * an enclosing area's average as if it described the requested parcel would be exactly the
 * kind of undefendable number this layer exists to refuse.
 */
export class AoiNotIngestedError extends ServiceError {
  constructor(geometryHash: string, available: string[]) {
    super(
      "aoi_not_ingested",
      "No ingested coverage for this geometry.",
      available.length > 0
        ? `Geometry hash ${geometryHash} is not in the data lake. Ingested areas: ${available.join(", ")}. ` +
          "Register the geometry with `gaia aoi add --geojson <file>` and run `make ingest`."
        : `Geometry hash ${geometryHash} is not in the data lake, which is empty. Run \`make seed\`.`,
    );
  }
}

export class NoDataForPeriodError extends ServiceError {
  constructor(period: string, available?: string) {
    super(
      "no_data_for_period",
      `No validated values for ${period}.`,
      available === undefined ? undefined : `Available coverage: ${available}.`,
    );
  }
}

export class ClaimNotFoundError extends ServiceError {
  constructor(claimId: string) {
    super(
      "claim_not_found",
      `No claim with id ${claimId}.`,
      "Claim ids are returned alongside every served value. They are stable for identical " +
        "questions, so an id that has never been served will not resolve.",
    );
  }
}

export class LakeUnavailableError extends ServiceError {
  constructor(path: string, cause: string) {
    super(
      "lake_unavailable",
      "The data lake could not be opened.",
      `${path}: ${cause}. Run \`make seed\` to build it.`,
      true,
    );
  }
}
