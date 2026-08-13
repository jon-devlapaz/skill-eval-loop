# Frozen Python test behavior map

This appendix names every test at the frozen Python baseline. The owning class
defines the proof area; each test name states the precise behavior asserted.
Parameterized/subtest matrices remain owned by the named test and must be
preserved when differential scenarios are expanded.

## WorkflowContractTests

- `test_missing_evals_require_fresh_subagent_authoring`: missing evals require fresh subagent authoring.
- `test_model_choice_and_setup_changes_require_confirmation`: model choice and setup changes require confirmation.
- `test_interaction_asks_one_question_at_a_time`: interaction asks one question at a time.
- `test_harness_claims_link_the_complete_evidence_matrix`: harness claims link the complete evidence matrix.

## ModelRecommendationTests

- `test_provider_name_does_not_inflate_model_tier`: provider name does not inflate model tier.
- `test_marker_substrings_do_not_inflate_model_tier`: marker substrings do not inflate model tier.
- `test_pi_inventory_parser_uses_exact_provider_model_ids`: pi inventory parser uses exact provider model ids.
- `test_standard_task_recommends_balanced_target_and_quality_judge`: standard task recommends balanced target and quality judge.
- `test_portability_recommends_a_cross_tier_matrix`: portability recommends a cross tier matrix.
- `test_missing_tier_is_disclosed_as_a_fallback`: missing tier is disclosed as a fallback.
- `test_subset_counter_references_use_per_case_counts`: subset counter references use per case counts.
- `test_counter_references_require_exact_per_case_vectors`: counter references require exact per case vectors.
- `test_invocation_counts_require_positive_integer_trials`: invocation counts require positive integer trials.

## SuiteAuditTests

- `test_valid_provenance_suite_passes`: valid provenance suite passes.
- `test_missing_schema_three_contrast_fails_with_actionable_code`: missing schema three contrast fails with actionable code.
- `test_non_discriminating_contrast_fails_with_actionable_code`: non discriminating contrast fails with actionable code.
- `test_contrast_claim_with_only_workspace_graders_fails_audit`: contrast claim with only workspace graders fails audit.
- `test_malformed_contrast_fails_with_actionable_code`: malformed contrast fails with actionable code.
- `test_schema_three_without_a_discrimination_claim_remains_valid`: schema three without a discrimination claim remains valid.
- `test_tampered_provenance_fails_closed`: tampered provenance fails closed.

## SuiteValidationTests

- `test_duplicate_grader_names_are_rejected_before_execution`: duplicate grader names are rejected before execution.
- `test_schema_two_model_grader_requires_rubric`: schema two model grader requires rubric.

## ObsoletePolicyTests

- `test_schema_three_rejects_obsolete_distribution_policy`: schema three rejects obsolete distribution policy.

## CounterReferenceTests

- `test_a_wrong_counter_reference_is_accepted`: a wrong counter reference is accepted.
- `test_a_model_grader_that_accepts_the_counter_stops_the_run`: a model grader that accepts the counter stops the run.
- `test_schema_three_response_graders_require_a_counter_reference`: schema three response graders require a counter reference.
- `test_schema_two_without_a_counter_reference_remains_compatible`: schema two without a counter reference remains compatible.
- `test_counter_reference_retains_its_judge_records`: counter reference retains its judge records.
- `test_counter_reference_must_be_an_object`: counter reference must be an object.
- `test_counter_reference_response_must_be_a_string`: counter reference response must be a string.
- `test_empty_counter_reference_object_is_rejected`: empty counter reference object is rejected.
- `test_counter_reference_requires_a_response_sensitive_grader`: counter reference requires a response sensitive grader.

## TargetAttestationOwnerTests

- `test_evaluate_passes_when_attested`: evaluate passes when attested.
- `test_evaluate_reports_codex_rollout_missing`: evaluate reports codex rollout missing.
- `test_target_and_judge_share_model_reason_order`: target and judge share model reason order.
- `test_require_uses_the_first_shared_failure_for_target_and_judge`: require uses the first shared failure for target and judge.
- `test_judge_uses_shared_model_mismatch_policy`: judge uses shared model mismatch policy.
- `test_evaluate_reports_manifest_model_mismatch`: evaluate reports manifest model mismatch.
- `test_evaluate_fail_closed_on_empty_recorded_model`: evaluate fail closed on empty recorded model.
- `test_require_raises_when_forced_skill_not_accessed`: require raises when forced skill not accessed.
- `test_require_skips_forced_skill_outside_codex_forced_treatment`: require skips forced skill outside codex forced treatment.
- `test_evaluate_reports_forced_skill_when_write_context_supplied`: evaluate reports forced skill when write context supplied.
- `test_require_maps_evaluate_reasons_to_runtime_errors`: require maps evaluate reasons to runtime errors.
- `test_require_reports_cross_provider_same_leaf_mismatch`: require reports cross provider same leaf mismatch.

## RuntimeTests

- `test_model_identity_requires_the_full_provider_and_model`: model identity requires the full provider and model.
- `test_harness_choices_are_explicit_and_complete`: harness choices are explicit and complete.
- `test_skill_payload_rejects_symlinked_files`: skill payload rejects symlinked files.
- `test_skill_payload_digest_includes_executable_mode`: skill payload digest includes executable mode.
- `test_each_harness_isolates_the_skill_to_treatment`: each harness isolates the skill to treatment.
- `test_each_harness_builds_a_skill_free_judge`: each harness builds a skill free judge.
- `test_pi_changes_only_explicit_skill_availability`: pi changes only explicit skill availability.
- `test_autonomous_mode_leaves_the_treatment_task_unexpanded`: autonomous mode leaves the treatment task unexpanded.
- `test_moving_model_aliases_are_rejected`: moving model aliases are rejected.
- `test_trace_separates_injection_from_explicit_access`: trace separates injection from explicit access.
- `test_trace_does_not_infer_actual_model_from_request`: trace does not infer actual model from request.
- `test_hermes_no_tools_uses_an_explicit_disabled_toolset`: hermes no tools uses an explicit disabled toolset.
- `test_codex_uses_an_isolated_codex_home`: codex uses an isolated codex home.
- `test_codex_persists_session_for_runtime_attestation`: codex persists session for runtime attestation.
- `test_codex_rollout_attests_model_and_skill_availability`: codex rollout attests model and skill availability.
- `test_conflicting_trace_models_fail_attestation`: conflicting trace models fail attestation.
- `test_same_leaf_models_from_different_providers_conflict`: same leaf models from different providers conflict.
- `test_pi_trace_combines_separate_provider_and_model_fields`: pi trace combines separate provider and model fields.
- `test_pi_trace_conflicting_separate_providers_fail_attestation`: pi trace conflicting separate providers fail attestation.
- `test_codex_skill_catalog_with_description_attests_injection`: codex skill catalog with description attests injection.
- `test_codex_structured_skill_payload_attests_explicit_access`: codex structured skill payload attests explicit access.

## ProcessControlTests

- `test_timeout_terminates_process_group`: timeout terminates process group.
- `test_keyboard_interrupt_terminates_headless_process_group`: keyboard interrupt terminates headless process group.
- `test_headless_runtime_preserves_partial_interrupt_output`: headless runtime preserves partial interrupt output.

## PlanningTests

- `test_cli_defaults_to_one_pilot_trial`: cli defaults to one pilot trial.
- `test_default_dry_run_path_is_external_and_not_created`: default dry run path is external and not created.
- `test_external_output_override_is_preserved`: external output override is preserved.
- `test_output_inside_active_skills_is_rejected`: output inside active skills is rejected.
- `test_output_inside_external_target_skill_is_rejected`: output inside external target skill is rejected.
- `test_control_fixture_rejects_project_local_target_skill`: control fixture rejects project local target skill.
- `test_reference_failure_prevents_trials`: reference failure prevents trials.
- `test_failed_target_stops_before_model_grading`: failed target stops before model grading.
- `test_herdr_observer_requires_environment_before_creating_output`: herdr observer requires environment before creating output.
- `test_herdr_workspace_uses_named_retained_2x2_layout`: herdr workspace uses named retained 2x2 layout.
- `test_herdr_job_serializes_only_supported_environment_overrides`: herdr job serializes only supported environment overrides.
- `test_herdr_worker_applies_serialized_environment_overrides`: herdr worker applies serialized environment overrides.
- `test_cancel_targets_only_the_active_eval_pane`: cancel targets only the active eval pane.
- `test_finish_retains_workspace_and_notifies_once`: finish retains workspace and notifies once.
- `test_cancellation_marks_partial_run_invalid_and_retains_it`: cancellation marks partial run invalid and retains it.

## EndToEndTests

- `test_forced_codex_treatment_requires_explicit_skill_access`: forced codex treatment requires explicit skill access.
- `test_missing_judge_attestation_stops_after_first_paid_call`: missing judge attestation stops after first paid call.
- `test_missing_target_attestation_stops_after_first_paid_call`: missing target attestation stops after first paid call.
- `test_wrong_judge_model_stops_after_first_paid_call`: wrong judge model stops after first paid call.
- `test_wrong_target_model_stops_after_first_paid_call`: wrong target model stops after first paid call.
- `test_codex_run_hashes_persisted_runtime_attestation`: codex run hashes persisted runtime attestation.
- `test_fake_runs_complete_for_every_selected_harness`: fake runs complete for every selected harness.
- `test_fake_pi_run_writes_a_valid_paired_result`: fake pi run writes a valid paired result.
- `test_model_judges_share_the_visible_judge_results_pane`: model judges share the visible judge results pane.
- `test_condition_order_is_counterbalanced_in_the_manifest`: condition order is counterbalanced in the manifest.
- `test_dry_run_counts_multiple_graders_and_subset_counters`: dry run counts multiple graders and subset counters.

## AggregateTests

- `test_undeclared_grader_discrimination_remains_unproven`: undeclared grader discrimination remains unproven.
- `test_no_judge_calls_have_zero_usage_only_when_zero_are_expected`: no judge calls have zero usage only when zero are expected.
- `test_unexpected_usage_when_zero_expected_is_not_reported_as_zero`: unexpected usage when zero expected is not reported as zero.
- `test_accounting_snapshot_requires_complete_unique_references`: accounting snapshot requires complete unique references.
- `test_counter_presence_must_match_accounting_snapshot`: counter presence must match accounting snapshot.
- `test_declared_counter_reference_must_be_an_object`: declared counter reference must be an object.
- `test_counter_reference_must_retain_a_failing_grading`: counter reference must retain a failing grading.
- `test_counter_reference_must_fail_every_response_grader`: counter reference must fail every response grader.
- `test_case_contrast_requires_complete_snapshot_metadata`: case contrast requires complete snapshot metadata.
- `test_case_contrast_requires_a_counter_for_response_graders`: case contrast requires a counter for response graders.
- `test_case_contrast_revalidates_each_named_grader`: case contrast revalidates each named grader.
- `test_partial_accounting_metadata_is_rejected`: partial accounting metadata is rejected.
- `test_condition_judges_cannot_be_shifted_between_cases`: condition judges cannot be shifted between cases.
- `test_complete_full_usage_includes_target_and_all_judges`: complete full usage includes target and all judges.
- `test_missing_target_or_judge_usage_is_null_with_coverage`: missing target or judge usage is null with coverage.
- `test_legacy_snapshot_keeps_target_usage_and_marks_new_buckets_unknown`: legacy snapshot keeps target usage and marks new buckets unknown.
- `test_paired_outcomes_produce_descriptive_verdict`: paired outcomes produce descriptive verdict.
- `test_missing_runtime_attestation_is_separate_from_treatment_validity`: missing runtime attestation is separate from treatment validity.
- `test_forced_skill_access_remains_a_write_time_only_check`: forced skill access remains a write time only check.
- `test_trace_visible_control_use_blocks_mechanism_claim`: trace visible control use blocks mechanism claim.
- `test_unforced_treatment_blocks_mechanism_claim`: unforced treatment blocks mechanism claim.
- `test_autonomous_access_is_scored_as_a_routing_decision`: autonomous access is scored as a routing decision.
- `test_artifact_hash_drift_fails`: artifact hash drift fails.
- `test_non_codex_trace_is_reparsed_during_aggregation`: non codex trace is reparsed during aggregation.
- `test_reparsed_trace_owns_model_attestation_during_aggregation`: reparsed trace owns model attestation during aggregation.
- `test_aggregation_requires_complete_case_trial_matrix`: aggregation requires complete case trial matrix.
- `test_condition_record_identity_must_match_enclosing_pair`: condition record identity must match enclosing pair.
- `test_inconsistent_grading_summary_fails`: inconsistent grading summary fails.
- `test_control_exposure_fails`: control exposure fails.

## EvaluatorMutationTests

- `test_sealed_run_accepts_good_and_rejects_corrupt_artifacts`: sealed run accepts good and rejects corrupt artifacts.
- `test_sealed_marker_grader_rejects_a_wrong_marker`: sealed marker grader rejects a wrong marker.
- `test_deliberate_response_mutations_fail_deterministic_graders`: deliberate response mutations fail deterministic graders.

## ModelGraderTests

- `test_json_fence_is_accepted`: json fence is accepted.

## ReleaseIdentityTests

- `test_matching_receipt_and_payload_are_verified_deterministically`: matching receipt and payload are verified deterministically.
- `test_uppercase_receipt_revision_names_the_same_git_object`: uppercase receipt revision names the same git object.
- `test_ancestor_receipt_fails_even_when_skill_bytes_are_unchanged`: ancestor receipt fails even when skill bytes are unchanged.
- `test_wrong_receipt_source_fails_at_source_boundary`: wrong receipt source fails at source boundary.
- `test_noncanonical_expected_source_is_rejected`: noncanonical expected source is rejected.
- `test_payload_byte_drift_reports_the_changed_path`: payload byte drift reports the changed path.
- `test_executable_mode_drift_is_part_of_payload_identity`: executable mode drift is part of payload identity.
- `test_extra_empty_directory_is_payload_drift`: extra empty directory is payload drift.
- `test_wrong_receipt_path_fails_before_payload_comparison`: wrong receipt path fails before payload comparison.
- `test_installed_symlink_is_rejected`: installed symlink is rejected.
- `test_symlinked_installed_root_is_rejected`: symlinked installed root is rejected.
- `test_git_symlink_is_rejected`: git symlink is rejected.
- `test_uncommitted_worktree_drift_does_not_change_git_identity`: uncommitted worktree drift does not change git identity.


