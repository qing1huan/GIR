"""Explicit, deduplicated requirement mapping; no AI or gameplay coverage estimate."""
from collections import Counter

# Methods are unique checks. Multiple requirement tags must never be summed.
GROUPS = {
    "test_contract.ContractTests": [
        ("A01_types_numeric_and_depth", "TYPE_VALIDATION", "A01"),
        ("A01_unknown_ast_and_static_types", "COMPILE_NEGATIVE", "A01 A07"),
        ("A02_special_keys_copy_and_order", "RUNTIME_SEMANTIC", "A02"),
        ("A03_authority_and_explicit_logs", "RUNTIME_SEMANTIC", "A03"),
        ("A04_atomicity_rng_spawn_future_commands_retry", "RUNTIME_SEMANTIC", "A04 A09"),
        ("A05_own_reads_other_job_and_invalid", "RUNTIME_SEMANTIC", "A05"),
        ("A05_snapshot_delete_spawn_and_alias", "RUNTIME_SEMANTIC", "A05"),
        ("A06_exclusive_add_priority_tie_invalid_loser", "RUNTIME_SEMANTIC", "A06"),
        ("A06_float_fold_and_numeric_overflow", "RUNTIME_SEMANTIC", "A01 A06"),
        ("A06_clamp_once_health_library", "RUNTIME_SEMANTIC", "A06 A08"),
        ("A07_root_order_bfs_future_and_event_ids", "RUNTIME_SEMANTIC", "A07"),
        ("A07_event_cycle_budget_and_no_truncation", "RUNTIME_SEMANTIC", "A04 A07"),
        ("A07_finite_loops", "RUNTIME_SEMANTIC", "A07"),
        ("A08_door_lowering_core_plan_and_source", "RUNTIME_SEMANTIC", "A08"),
        ("A09_delayed_failure_response_snapshot_restore_replay", "RUNTIME_SEMANTIC", "A09 A10"),
        ("A09_query_missing_epoch_and_command_conflict", "RUNTIME_SEMANTIC", "A03 A09"),
        ("A09_pure_extension_no_hidden_world", "RUNTIME_SEMANTIC", "A03 A09"),
    ],
    "test_boundaries.AdditionalBoundaries": [
        ("value_nodes_bytes_map_len_and_structural_eq", "TYPE_VALIDATION", "A01 A02"),
        ("ast_depth_malformed_and_forged_annotations", "COMPILE_NEGATIVE", "A01 A08"),
        ("default_event_cycle_has_bounded_uids", "RUNTIME_SEMANTIC", "A07"),
        ("new_max_clamp_and_scoped_loop_accumulator", "RUNTIME_SEMANTIC", "A06 A07"),
        ("failed_intermediate_write_and_two_deletes", "RUNTIME_SEMANTIC", "A01 A05 A06"),
        ("budget_units_effects_access_commands_and_live", "RUNTIME_SEMANTIC", "A04 A07 A09"),
        ("rng_known_vector_and_replay_tamper", "RUNTIME_SEMANTIC", "A04 A09"),
        ("entity_stale_service_reply_resolve_none", "RUNTIME_SEMANTIC", "A05 A09"),
        ("retry_cannot_replace_failed_admitted_input", "RUNTIME_SEMANTIC", "A04"),
        ("map_structural_eq_list_order_and_queued_copy", "RUNTIME_SEMANTIC", "A02"),
        ("dynamic_alias_and_container_cell_conflict", "RUNTIME_SEMANTIC", "A05 A06"),
        ("observation_epoch_same_for_later_microstep", "RUNTIME_SEMANTIC", "A03 A05 A09"),
        ("clamp_cannot_hide_base_overflow_and_snapshot_config_mismatch", "RUNTIME_SEMANTIC", "A01 A06 A10"),
    ],
    "test_cli.CommandLineTests": [
        ("run_replay_inspect", "CLI_INTEGRATION", "A09"),
        ("step_delayed_reply_restore_and_segment_replay", "CLI_INTEGRATION", "A09 A10"),
        ("abort_resume_requires_same_input_retry_and_driver_persists", "CLI_INTEGRATION", "A04 A10"),
        ("malformed_is_structured_error", "CLI_INTEGRATION", "A01"),
    ],
    "test_closure.QueryClosure": [
        ("A05_nearest_predicate_tie_stale_and_replay", "RUNTIME_SEMANTIC", "A03 A05 A09"),
        ("A05_predicate_reads_snapshot_not_own_overlay", "RUNTIME_SEMANTIC", "A05"),
        ("A05_query_and_nearest_error_rollback", "RUNTIME_SEMANTIC", "A01 A04 A05"),
    ],
    "test_closure.LoweringClosure": [
        ("A08_cost_preserving_constant_fold_and_budget_fault", "RUNTIME_SEMANTIC", "A04 A07 A08"),
        ("A08_reorder_mutations_detect_semantic_difference_and_origin", "RUNTIME_SEMANTIC", "A08"),
        ("A08_override_reexpand_conflict_preserves_manual", "COMPILE_NEGATIVE", "A08"),
    ],
    "test_closure.ServiceClosure": [
        ("A09_mock_failure_delayed_ack_restore_and_changed_provider_replay", "RUNTIME_SEMANTIC", "A09"),
        ("A09_missing_version_pure_extension_budget_and_rollback", "RUNTIME_SEMANTIC", "A03 A04 A09"),
    ],
    "test_closure.LifecycleClosure": [
        ("A10_preserve_quest_versions_dedup_save_and_replay", "RUNTIME_SEMANTIC", "A10"),
        ("A10_rebase_explicit_timing_pending_and_failure_atomic", "RUNTIME_SEMANTIC", "A04 A10"),
        ("A10_partial_migration_err_hidden_read_and_budget_rollback", "RUNTIME_SEMANTIC", "A03 A10"),
        ("A10_cumulative_migration_budget_and_namespace_abort_restore", "RUNTIME_SEMANTIC", "A04 A07 A10"),
        ("A10_restart_cancels_timer_no_reward_and_bad_abi_rejected", "RUNTIME_SEMANTIC", "A10"),
    ],
    "test_closure.EditingClosure": [
        ("A11_patch_validate_revision_connect_activate_and_new_version_rollback", "RUNTIME_SEMANTIC", "A11"),
        ("A11_unknown_port_type_control_and_atomic_failure", "COMPILE_NEGATIVE", "A11"),
        ("A11_unknown_impact_create_delete_and_uncommitted_rollback", "RUNTIME_SEMANTIC", "A11"),
        ("A11_shared_field_impact_pending_migration_and_overflow_atomic", "RUNTIME_SEMANTIC", "A10 A11"),
    ],
}

FIXTURES = {
    "PT01": "A05 A08", "PT02": "A01 A02", "PT02-invalid": "A01", "PT02-overflow": "A01 A04",
    "PT09": "A02 A04", "PT09-insufficient": "A02", "PT09-full": "A02",
    "PT11": "A06 A07", "PT12": "A08 A09", "PT13": "A03 A09",
    "PT14": "A04 A06", "PT15": "A04 A07",
}


def registry():
    entries = {}
    for group, rows in GROUPS.items():
        for method, kind, criteria in rows:
            id_ = "headless_v01.tests."+group+".test_"+method
            entries[id_] = {"kind": kind, "criteria": criteria.split(), "fixture": None,
                            "first_slice": not group.startswith("test_closure.")}
    for fixture, criteria in FIXTURES.items():
        id_ = "headless_v01.tests.test_end_to_end.EndToEndTests.test_"+fixture.replace("-", "_")
        entries[id_] = {"kind": "END_TO_END", "criteria": criteria.split(), "fixture": fixture, "first_slice": True}
    return entries


def audit(records):
    mapping = registry(); actual = [r["test"] for r in records]
    if len(actual) != len(set(actual)) or set(actual) != set(mapping):
        raise AssertionError({"unmapped": sorted(set(actual)-set(mapping)), "missing": sorted(set(mapping)-set(actual)), "duplicate_ids": len(actual)-len(set(actual))})
    rows = [{**r, **mapping[r["test"]]} for r in records]
    return {"kind": "ENGINEERING_CHECK_ACCOUNTING_ONLY", "unique_method_count": len(rows),
            "unique_first_slice_count": sum(r["first_slice"] for r in rows),
            "unique_added_count": sum(not r["first_slice"] for r in rows),
            "kinds": dict(Counter(r["kind"] for r in rows)),
            "end_to_end_fixture_count_included_in_unique_methods": len(FIXTURES),
            "extra_trace_generations": {"count": 12, "adds_unique_checks": 0},
            "extra_CLI_artifact_commands": {"count": 7, "adds_unique_checks": 0},
            "fixture_serialization_structure_checks": {"count": 12, "adds_semantic_checks": 0},
            "criterion_tags_overlap": True, "criterion_counts_must_not_be_summed": True,
            "editor_impact_test_labels": "selection metadata, not additional executed tests",
            "actual_ai_runs": 0, "actual_gameplay_coverage": None, "tests": rows}
