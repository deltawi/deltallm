-- Inactive preparation only. Do not relax the prepared/zero-pending guards
-- without the writer fencing, attribution and cutover described in PR 4.
-- No existing table is changed and no layout is selected by this migration.
CREATE TABLE deltallm_telemetry_capacity_layout (
    queue_name TEXT PRIMARY KEY,
    epoch BIGINT NOT NULL,
    protocol_version INTEGER NOT NULL DEFAULT 2,
    state TEXT NOT NULL DEFAULT 'prepared',
    capacity BIGINT NOT NULL,
    required_reserve BIGINT NOT NULL,
    partition_count INTEGER NOT NULL,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT telemetry_layout_queue CHECK (queue_name IN ('audit', 'spend')),
    CONSTRAINT telemetry_layout_epoch CHECK (epoch > 0),
    CONSTRAINT telemetry_layout_protocol CHECK (protocol_version = 2),
    CONSTRAINT telemetry_layout_prepared CHECK (state = 'prepared'),
    CONSTRAINT telemetry_layout_capacity CHECK (capacity > 0),
    CONSTRAINT telemetry_layout_reserve CHECK (
        required_reserve >= 0 AND required_reserve <= capacity
        AND (queue_name = 'audit' OR required_reserve = 0)
    ),
    CONSTRAINT telemetry_layout_partition_count CHECK (
        partition_count BETWEEN 1 AND 64 AND partition_count <= capacity
    ),
    CONSTRAINT telemetry_layout_definition UNIQUE (
        queue_name, epoch, capacity, required_reserve, partition_count
    )
);

CREATE TABLE deltallm_telemetry_capacity_partition (
    queue_name TEXT NOT NULL,
    epoch BIGINT NOT NULL,
    partition_id INTEGER NOT NULL,
    capacity BIGINT NOT NULL,
    required_reserve BIGINT NOT NULL,
    partition_count INTEGER NOT NULL,
    quota BIGINT NOT NULL,
    best_effort_ceiling BIGINT NOT NULL,
    pending_count BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT telemetry_partition_pk PRIMARY KEY (queue_name, partition_id),
    CONSTRAINT telemetry_partition_layout FOREIGN KEY (
        queue_name, epoch, capacity, required_reserve, partition_count
    ) REFERENCES deltallm_telemetry_capacity_layout (
        queue_name, epoch, capacity, required_reserve, partition_count
    ) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT telemetry_partition_range CHECK (
        partition_count BETWEEN 1 AND 64
        AND partition_id >= 0 AND partition_id < partition_count
    ),
    CONSTRAINT telemetry_partition_quota CHECK (
        quota = capacity / partition_count
            + CASE WHEN partition_id < capacity % partition_count THEN 1 ELSE 0 END
    ),
    CONSTRAINT telemetry_partition_best_effort CHECK (
        best_effort_ceiling = (capacity - required_reserve) / partition_count
            + CASE WHEN partition_id < (capacity - required_reserve) % partition_count
                THEN 1 ELSE 0 END
        AND best_effort_ceiling BETWEEN 0 AND quota
    ),
    CONSTRAINT telemetry_partition_pending CHECK (pending_count BETWEEN 0 AND quota),
    CONSTRAINT telemetry_partition_inactive CHECK (pending_count = 0)
);
